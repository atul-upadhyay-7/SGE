# Logging your own 18650 cells for the pipeline

The pipeline is developed on the NASA `.mat` cells, but the
partial-window SOH model is defined on per-sample measurements you can
log yourself. This is what to record so `src/load_data.py` and
`src/streaming/cycle_tracker.py` can build the same features the model
was trained on.

## Sampling

- Log at **1 Hz or faster** — one row per second per cell. Faster is
  fine: the model integrates current over time, so a finer step only
  improves `vw_capacity_ah` and `discharge_duration_s`.
- Timestamps must be **monotonic and in seconds** within a record. The
  tracker rejects a sample whose timestamp does not advance, and the
  offline capacity integral is `|current| × dt`, so a repeated or
  backwards timestamp silently drops charge.
- Keep **one file (or one stream) per cell**.

## First: three full reference discharges at a fixed current

Before ageing the cell, run **three or more full discharges at one
fixed current** and log them. The cell-relative features are each
window quantity divided by the cell's own opening median, and the model
refuses a cell with fewer than three. The first five valid discharges
set the baseline (`WINDOW_REFERENCE_WINDOW` in `src/features.py`), so
three is the floor and five is what the NASA cells use.

Discharge at a current inside the model's trained band — **2.0 A to
4.0 A** (`WINDOW_TRAIN_CURRENT_LOW_A` / `WINDOW_TRAIN_CURRENT_HIGH_A`).
The per-amp normalisation removes the rate difference inside that band,
but the model has no training data outside it.

## Then: age the cell

After the reference discharges, keep cycling and keep logging every
discharge. SOH is emitted once per **completed discharge** — several
features are only defined at the cycle boundary — so the model scores
each full discharge, not each second. SOH moves slowly, about
`6e-5` points per second, which is why a cycle-level output is the
right grain.

## CSV columns

One row per sample:

| Column | Unit | Meaning |
|---|---|---|
| `timestamp` | seconds | sample time, monotonically increasing |
| `voltage` | volts | cell terminal voltage |
| `current` | amperes | **discharge is negative**, charge positive |
| `temperature` | degrees Celsius | cell (or nearest-surface) temperature |
| `ambient_temperature` | degrees Celsius | optional; constant per test if you log it |

This is the same schema `src/streaming/replay.py` feeds the live
tracker (`voltage`, `current`, `temperature`, `time`) and the same four
arrays `src/load_data.py` reads from the NASA `.mat` files. Any logger
that writes those columns one row per second works. If your logger
writes `time` instead of `timestamp`, rename the column (or map it)
before ingesting.

## What each discharge must contain

Each discharge must cross the trained window, **4.0 V down to 3.4 V**
(`WINDOW_VOLTAGE_HIGH` / `WINDOW_VOLTAGE_LOW`). A discharge that does
not reach 3.4 V, or is measured over a different span, is **refused
rather than scored** — the model has no training data there. Everything
the model consumes is measured inside that window: time inside it,
charge delivered, mean voltage, and the dQ/dV peak.

## What you cannot log yet

Influx of full-discharge signals — duration, end voltage, the internal
resistance proxy — is what the **offline** benchmark uses; it is not an
online estimator input. The deployment surface is the window model
above, which needs only the four columns and the crossing of the
trained window.
