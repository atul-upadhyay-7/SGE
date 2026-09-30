"""
anomaly.py
----------
Detect anomalous degradation patterns in battery cycles.

What "anomaly" means in this project
------------------------------------
A cell ageing normally is not an anomaly. Every cell here fades,
and the rate differs from cell to cell, so an absolute threshold
on SOH cannot tell a healthy cell from a sick one. What can be
flagged is a cycle that departs from the behaviour of cells like
it: capacity that fell further in one cycle than it normally
does, or a fade rate no other cell in the set has shown.

Three detectors, each answering a different question
-----------------------------------------------------
residual    The model is trained across cells, so it has learned
            what a cycle at this point usually looks like. A
            large gap between actual and predicted SOH is
            therefore a cell-specific departure from the norm,
            which is exactly what an inspection should catch.
            The prediction comes from the same leave-one-cell-out
            protocol as train_soh.py, so the cell being inspected
            was never in the model that judged it.

event       Physics that needs no model at all. A single-cycle
            capacity loss far beyond measurement scatter is a
            damage event; a capacity gain beyond that same
            scatter is not good news, it is a sensor or
            calibration artefact that would corrupt every
            downstream target.

knee        Degradation is convex, a shallow shoulder followed
            by an accelerating fall. The transition into that
            accelerating phase is the single most useful thing
            a battery management system can be told early, so
            it gets its own detector on the trailing fade rate.

Why a robust scale and not a standard deviation
-----------------------------------------------
The detectors are looking for the tail of their own
distribution. That distribution is built from a handful of
cells, so the mean and standard deviation are themselves
determined by whatever anomaly is being hunted. The median and
the median absolute deviation are not: a handful of extreme
cycles cannot move the median, and the MAD climbs to meet them.
Thresholds are therefore expressed as a multiple of that
robust sigma, and the report prints the sigma it used so a
threshold is never a number without a scale.

Honest limits
-------------
These detectors flag cycles for a human to look at. They do not
diagnose the cause, and a flag is not proof of a fault. The
reference capacity is the cell's own first measured capacity, so
a cell that was already partly aged when it was first seen will
read high throughout and its early cycles may be flagged as
apparent recovery.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from evaluation import (
    loco_evaluate
)

from features import (
    drop_constant_features,
    make_xy
)


DATA_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
)

RESULT_DIR = Path(
    "data/processed"
)

TARGET = "soh"

PREDICTION_COLUMN = "predicted_soh"

# Multiples of the robust sigma at which a cycle is called
# anomalous. Three and a half keeps the false-alarm rate low on
# normal noise, which matters because a detector that cries wolf
# on a healthy fleet gets switched off.
ANOMALY_RESIDUAL_K = 3.5

# A one-cycle capacity move larger than this share of the cell's
# own beginning-of-life capacity is treated as an event rather
# than as fade. The discharge capacity in this dataset carries a
# few tenths of a percent of scatter, so 2% is well clear of
# noise without waiting for a catastrophic step.
ANOMALY_CAPACITY_JUMP_PCT = 2.0

# Width of the trailing window used for the fade rate, in
# cycles. Short enough to still catch the knee, long enough
# that the rate is not dominated by single-cycle scatter.
KNEE_WINDOW = 20

# A trailing fade rate this many robust sigmas above the
# cell's own typical rate is taken as knee onset.
KNEE_RATE_K = 3.0

# Scale factor that makes the median absolute deviation
# comparable to a standard deviation for normally distributed
# data. Without it every threshold below would be optimistic by
# roughly half.
MAD_TO_SIGMA = 1.4826


def robust_sigma(values):
    """
    Return a spread estimate that outliers cannot inflate.

    The MAD is scaled to sigma units. A degenerate series,
    where more than half the values are identical, drives the
    MAD to zero, so the standard deviation is used instead and
    finally 0.0 if even that is unavailable. Returning nan
    rather than raising keeps the callers total, because a cell
    with too few cycles to characterise is expected here.
    """

    values = np.asarray(
        values,
        dtype=float
    )

    values = values[
        np.isfinite(values)
    ]

    if values.size == 0:

        return float("nan")

    median = np.median(values)

    mad = np.median(
        np.abs(values - median)
    )

    sigma = float(mad) * MAD_TO_SIGMA

    if sigma > 0:

        return sigma

    sigma = float(
        np.std(values)
    )

    return 0.0 if not np.isfinite(sigma) else sigma


def residual_bounds(
    residual,
    k=ANOMALY_RESIDUAL_K
):
    """
    Symmetric robust bounds around a residual series.

    The centre is the median residual rather than zero. A
    persistent offset means the model is systematically biased
    for this cell, and measuring the deviation from that bias
    isolates genuine one-off departures instead of re-flagging
    the same offset on every single cycle.
    """

    residual = np.asarray(
        residual,
        dtype=float
    )

    finite = residual[
        np.isfinite(residual)
    ]

    if finite.size == 0:

        return (
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan")
        )

    center = float(
        np.median(finite)
    )

    sigma = robust_sigma(finite)

    spread = k * sigma

    return (
        center - spread,
        center + spread,
        center,
        sigma
    )


def flag_residuals(
    frame,
    k=ANOMALY_RESIDUAL_K
):
    """
    Mark cycles whose actual SOH departs from the model's.

    Adds residual, residual_sigma, residual_anomaly and
    residual_direction. Direction separates the two very
    different situations a large gap can mean: the cell faded
    faster than any comparable cell has (a degradation event)
    or it faded slower, which on this dataset is far more often
    a measurement artefact than a genuine recovery.
    """

    frame = frame.copy()

    frame["residual"] = (
        frame[TARGET] - frame[PREDICTION_COLUMN]
    )

    lower, upper, center, sigma = (
        residual_bounds(
            frame["residual"],
            k=k
        )
    )

    frame["residual_center"] = center
    frame["residual_sigma"] = sigma
    frame["residual_lower"] = lower
    frame["residual_upper"] = upper

    frame["residual_anomaly"] = (
        (frame["residual"] < lower)
        | (frame["residual"] > upper)
    )

    frame["residual_direction"] = np.where(
        frame["residual"] < lower,
        "faster_than_expected",
        np.where(
            frame["residual"] > upper,
            "slower_than_expected",
            "normal"
        )
    )

    frame.loc[
        ~frame["residual_anomaly"],
        "residual_direction"
    ] = "normal"

    return frame


def flag_capacity_events(
    frame,
    jump_pct=ANOMALY_CAPACITY_JUMP_PCT
):
    """
    Mark one-cycle capacity moves too large to be fade.

    Measured against the cell's own beginning-of-life capacity
    so the threshold means the same thing for every cell. Adds
    capacity_jump_pct, capacity_anomaly and capacity_event.
    """

    frame = frame.copy()

    reference = frame["reference_capacity_ah"]

    frame["capacity_jump_pct"] = (
        frame["capacity_change_ah"]
        / reference
        * 100
    )

    frame["capacity_anomaly"] = (
        frame["capacity_jump_pct"].abs()
        > jump_pct
    )

    frame["capacity_event"] = np.where(
        frame["capacity_change_ah"] < -(
            reference * jump_pct / 100
        ),
        "abrupt_loss",
        np.where(
            frame["capacity_change_ah"] > (
                reference * jump_pct / 100
            ),
            "recovery",
            "none"
        )
    )

    frame.loc[
        ~frame["capacity_anomaly"],
        "capacity_event"
    ] = "none"

    return frame


def trailing_fade_rate(
    frame,
    window=KNEE_WINDOW
):
    """
    Fade rate in percentage points of SOH per cycle.

    Measured over a trailing window rather than one cycle,
    because single-cycle capacity scatter would otherwise
    dominate. Positive means fading. Returns nan until the
    window is full, since a partial window would report a rate
    averaged over a different number of cycles for every row
    and could not be compared against anything.
    """

    frame = frame.sort_values("cycle").copy()

    cycles = frame["cycle"].to_numpy(dtype=float)
    soh = frame[TARGET].to_numpy(dtype=float)

    span = cycles[window:] - cycles[:-window]
    drop = soh[window:] - soh[:-window]

    rate = np.full(len(frame), np.nan)

    with np.errstate(
        divide="ignore",
        invalid="ignore"
    ):

        rate[window:] = np.where(
            span != 0,
            -drop / span,
            np.nan
        )

    frame["fade_rate_pct_per_cycle"] = rate

    return frame


def flag_knee(
    frame,
    window=KNEE_WINDOW,
    k=KNEE_RATE_K
):
    """
    Mark the onset of accelerating fade.

    The threshold is the cell's own median trailing rate plus k
    robust sigmas, so a cell that fades fast everywhere is not
    flagged simply for fading fast, only for the step change
    that marks the knee. Adds knee_anomaly and fade_rate_sigma.
    """

    frame = trailing_fade_rate(
        frame,
        window=window
    )

    rate = frame["fade_rate_pct_per_cycle"]

    sigma = robust_sigma(rate)

    center = float(
        np.nanmedian(
            rate.to_numpy(dtype=float)
        )
    )

    threshold = center + k * sigma

    frame["fade_rate_center"] = center
    frame["fade_rate_sigma"] = sigma
    frame["fade_rate_threshold"] = threshold

    frame["knee_anomaly"] = (
        rate > threshold
    )

    return frame


def analyse_cell(
    frame,
    residual_k=ANOMALY_RESIDUAL_K,
    jump_pct=ANOMALY_CAPACITY_JUMP_PCT,
    knee_window=KNEE_WINDOW,
    knee_k=KNEE_RATE_K
):
    """
    Run every detector over one cell's cycles.

    The detectors are applied in a fixed order because each one
    reads the frame the previous one produced, and the frame is
    sorted by cycle first so the trailing windows are trailing.
    """

    frame = frame.sort_values("cycle").copy()

    frame = flag_residuals(
        frame,
        k=residual_k
    )

    frame = flag_capacity_events(
        frame,
        jump_pct=jump_pct
    )

    frame = flag_knee(
        frame,
        window=knee_window,
        k=knee_k
    )

    frame["anomaly"] = (
        frame["residual_anomaly"]
        | frame["capacity_anomaly"]
        | frame["knee_anomaly"]
    )

    frame["anomaly_reasons"] = frame.apply(
        describe_reasons,
        axis=1
    )

    return frame


def describe_reasons(row):
    """
    Comma-separated list of the detectors that fired.

    Kept as text rather than three separate boolean columns so
    that a single exported row states plainly why it was
    flagged.
    """

    reasons = []

    if row["residual_anomaly"]:

        reasons.append(
            f"residual:{row['residual_direction']}"
        )

    if row["capacity_anomaly"]:

        reasons.append(
            f"capacity:{row['capacity_event']}"
        )

    if row["knee_anomaly"]:

        reasons.append("knee")

    if not reasons:

        return ""

    return ",".join(reasons)


def residual_predictions(
    df,
    target=TARGET,
    model_name="XGBoost"
):
    """
    Predictions for every cell under leave-one-cell-out.

    Reuses the shared protocol from evaluation.py rather than
    trusting the prediction files already on disk, because a
    detector that judges a cell using a model that was trained
    on that same cell is measuring memorisation. The cell under
    inspection is held out of the model that judges it.
    """

    _, _, features = make_xy(
        df,
        target
    )

    features, _ = drop_constant_features(
        df[features]
    )

    _, predictions, _ = loco_evaluate(
        df,
        target,
        features,
        model_name,
        PREDICTION_COLUMN
    )

    return predictions


def summarise(frames):
    """
    Per-cell flag counts and rates.

    Rates are reported against the cycles a detector could
    actually judge. The residual and capacity detectors see
    every cycle, but the fade rate needs a full window, so
    dividing by all cycles would understate the rate for short
    cells by construction.
    """

    rows = []

    for frame in frames:

        cell = frame["cell_id"].iloc[0]

        judged = frame["fade_rate_pct_per_cycle"].notna()

        rows.append({
            "cell_id": cell,
            "cycles": len(frame),
            "any_anomaly": int(frame["anomaly"].sum()),
            "any_rate_pct": float(
                frame["anomaly"].mean() * 100
            ),
            "residual_anomalies": int(
                frame["residual_anomaly"].sum()
            ),
            "residual_sigma": float(
                frame["residual_sigma"].iloc[0]
            ),
            "capacity_anomalies": int(
                frame["capacity_anomaly"].sum()
            ),
            "knee_cycles_judged": int(judged.sum()),
            "knee_anomalies": int(
                frame["knee_anomaly"].sum()
            ),
            "knee_rate_pct": (
                float(
                    frame.loc[
                        judged, "knee_anomaly"
                    ].mean() * 100
                )
                if judged.any()
                else float("nan")
            ),
            "first_knee_cycle": (
                int(
                    frame.loc[
                        frame["knee_anomaly"], "cycle"
                    ].min()
                )
                if frame["knee_anomaly"].any()
                else None
            )
        })

    return pd.DataFrame(rows)


def load_dataset(path=DATA_FILE):
    """
    Read the processed dataset, with a remedy on failure.
    """

    if not Path(path).exists():

        raise FileNotFoundError(
            f"{path} not found.\n"
            "Run load_data.py and preprocessing.py first."
        )

    return pd.read_csv(path)


def main():

    df = load_dataset()

    RESULT_DIR.mkdir(
        exist_ok=True,
        parents=True
    )

    print(
        "\n========== ANOMALY DETECTION =========="
    )

    print(
        f"\nRows            : {len(df)}"
    )

    print(
        f"Cells           : "
        f"{df['cell_id'].nunique()}"
    )

    print(
        f"Residual K      : {ANOMALY_RESIDUAL_K} "
        "(robust sigma)"
    )

    print(
        f"Capacity jump   : "
        f"{ANOMALY_CAPACITY_JUMP_PCT}% of reference"
    )

    print(
        f"Knee window     : {KNEE_WINDOW} cycles"
    )

    print(
        f"Knee K          : {KNEE_RATE_K} "
        "(robust sigma)"
    )

    print(
        "\nScoring every cell with a model that never "
        "saw it."
    )

    predictions = residual_predictions(
        df
    )

    frames = [
        analyse_cell(frame)
        for frame in predictions
    ]

    report = pd.concat(
        frames,
        ignore_index=True
    )

    summary = summarise(frames)

    print(
        "\n\n========== PER-CELL SUMMARY =========="
    )

    print(
        summary.round(3)
        .to_string(index=False)
    )

    total = len(report)

    flagged = int(
        report["anomaly"].sum()
    )

    print(
        "\n\n========== FLAG COUNTS =========="
    )

    for column, label in [
        ("residual_anomaly", "residual"),
        ("capacity_anomaly", "capacity event"),
        ("knee_anomaly", "knee")
    ]:

        print(
            f"{label:22s}: "
            f"{int(report[column].sum())}"
        )

    print(
        f"{'any detector':22s}: {flagged}"
    )

    print(
        f"\nFlagged cycles  : {flagged} "
        f"of {total} "
        f"({flagged / total * 100:.1f}%)"
    )

    faster = int(
        (
            report["residual_direction"]
            == "faster_than_expected"
        ).sum()
    )

    slower = int(
        (
            report["residual_direction"]
            == "slower_than_expected"
        ).sum()
    )

    print(
        "\nResidual direction:"
    )

    print(
        f"  faster than expected : {faster}"
    )

    print(
        f"  slower than expected : {slower}"
    )

    if slower > faster:

        print(
            "\nMore cycles read better "
            "than the model expected"
        )

        print(
            "than worse. On this dataset "
            "that is far more"
        )

        print(
            "consistent with discharge "
            "measurement scatter"
        )

        print(
            "than with genuine "
            "recovery, which a fading"
        )

        print(
            "cell does not produce. "
            "These warrant a"
        )

        print(
            "calibration check before "
            "they are read as good"
        )

        print(
            "news."
        )

    print(
        "\n\n========== FLAGGED CYCLES =========="
    )

    events = report[
        report["anomaly"]
    ]

    if events.empty:

        print(
            "\nNo cycles exceeded any "
            "threshold."
        )

    else:

        columns = [
            "cell_id",
            "cycle",
            TARGET,
            PREDICTION_COLUMN,
            "residual",
            "capacity_jump_pct",
            "fade_rate_pct_per_cycle",
            "anomaly_reasons"
        ]

        print(
            events[columns]
            .round(3)
            .to_string(index=False)
        )

    print(
        "\n\n========== KNEE ONSET =========="
    )

    knees = summary[
        summary["knee_anomalies"] > 0
    ]

    if knees.empty:

        print(
            "\nNo cell showed a fade rate "
            "beyond its threshold."
        )

        print(
            "This is expected for cells "
            "that fade linearly over"
        )

        print(
            "their whole life rather than "
            "through a knee."
        )

    else:

        print(
            knees[
                [
                    "cell_id",
                    "first_knee_cycle",
                    "knee_anomalies"
                ]
            ]
            .to_string(index=False)
        )

    report.to_csv(
        RESULT_DIR / "anomaly_cycle_report.csv",
        index=False
    )

    events.to_csv(
        RESULT_DIR / "anomaly_events.csv",
        index=False
    )

    summary.to_csv(
        RESULT_DIR / "anomaly_summary.csv",
        index=False
    )

    print(
        "\n\n========== LIMITS =========="
    )

    print(
        "\nThese detectors rank cycles "
        "for inspection."
    )

    print(
        "They do not diagnose a cause, "
        "and a flag is not"
    )

    print(
        "proof of a fault."
    )

    print(
        "\nSOH is referenced to each "
        "cell's own first"
    )

    print(
        "measured capacity, so a cell "
        "already partly aged"
    )

    print(
        "when first seen will read high "
        "throughout and its"
    )

    print(
        "early cycles may be flagged as "
        "apparent recovery."
    )

    print(
        "\nThresholds are multiples of a "
        "robust sigma computed"
    )

    print(
        "from four cells. They are set "
        "for this dataset and"
    )

    print(
        "would need re-deriving for a "
        "different chemistry or"
    )

    print(
        "a wider fleet."
    )

    print(
        "\nSaved results to",
        RESULT_DIR
    )


if __name__ == "__main__":

    main()
