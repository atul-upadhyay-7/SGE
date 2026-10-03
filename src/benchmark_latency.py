"""
benchmark_latency.py
--------------------
Measure the SOH inference path and write the numbers to
data/processed/soh_latency_benchmark.csv.

What is measured, and why each matters:

    cold_model_load       joblib load of the artifact. Paid
                          once at service start (~1.7 s is
                          expected), never per request.
    warm_predict_1        one cycle row, model already loaded.
    warm_predict_100      batch of 100 rows.
    warm_predict_1000     batch of 1000 rows.
    feature_extraction    samples -> one cycle feature row
                          through CycleTracker.
    end_to_end_cycle      one cycle's samples -> tracker ->
                          assemble -> predict.

The benchmark uses the real artifact and the real
assemble_features path, not a mock, so the number reflects
what the streaming service actually does.
"""

import sys
import time
from pathlib import Path

import pandas as pd

# This file sits in src/, and the cycle tracker in
# src/streaming/. Put both on sys.path before any project
# import so the module works as a script and as an import,
# matching how streaming/soh_service.py bootstraps itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent / "streaming"),
)

import predict
from cycle_tracker import CycleTracker
from causal import CausalFeatureTracker
from predict import (
    assemble_features,
    load_soh_model,
    predict_soh,
)


PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

OUTPUT = PROCESSED_DIR / "soh_latency_benchmark.csv"

DATASET = PROCESSED_DIR / "nasa_ml_dataset.csv"


def _sample_rows(n):

    df = pd.read_csv(DATASET)

    return df.head(n).copy()


def _time(fn, repeats=5):

    # Best of `repeats`, because the first call in a process
    # pays interpreter and numpy warm-up costs that a running
    # service has already paid.
    best = float("inf")

    for _ in range(repeats):

        started = time.perf_counter()

        fn()

        best = min(best, time.perf_counter() - started)

    return best


def measure():

    results = {}

    # Cold load: clear the cache so this is a genuine load.
    predict.clear_model_cache()

    started = time.perf_counter()

    bundle = load_soh_model()

    results["cold_model_load_ms"] = (
        time.perf_counter() - started
    ) * 1000

    features = list(bundle["features"])

    # Warm single row.
    row = _sample_rows(1)

    matrix = assemble_features(row, features)

    results["warm_predict_1_ms"] = _time(
        lambda: bundle["model"].predict(matrix)
    ) * 1000

    # Warm one-row at the public API level, including
    # assemble_features, which is the realistic per-cycle call.
    results["warm_predict_soh_1_ms"] = _time(
        lambda: predict_soh(bundle, row)
    ) * 1000

    for n in (100, 1000):

        batch = _sample_rows(n)

        batch_matrix = assemble_features(
            batch, features
        )

        results[f"warm_predict_{n}_ms"] = _time(
            lambda m=batch_matrix: bundle["model"].predict(m),
            repeats=3,
        ) * 1000

    # End-to-end cycle: samples -> tracker -> row -> predict.
    def one_cycle():

        tracker = CycleTracker(cell_id="BENCH")

        row_out = None

        for i in range(200):

            row_out = tracker.add_sample(
                timestamp=float(i) * 3.0,
                voltage=3.6 - 0.004 * i,
                current=-2.0,
                temperature=30.0 + 0.01 * i,
            )

        if row_out is None:

            row_out = tracker.flush()

        if row_out is not None:

            # same past-only features the service adds
            row_out = {
                **row_out,
                **CausalFeatureTracker().update(row_out),
            }

            predict_soh(bundle, row_out)

    started = time.perf_counter()

    one_cycle()

    results["end_to_end_cycle_ms"] = (
        time.perf_counter() - started
    ) * 1000

    # Feature extraction alone, one cycle of samples.
    def extract_only():

        tracker = CycleTracker(cell_id="BENCH")

        for i in range(200):

            tracker.add_sample(
                timestamp=float(i) * 3.0,
                voltage=3.6 - 0.004 * i,
                current=-2.0,
                temperature=30.0 + 0.01 * i,
            )

        tracker.flush()

    results["feature_extraction_ms"] = _time(
        extract_only, repeats=3
    ) * 1000

    return results


def write(results):

    frame = pd.DataFrame(
        [
            {"stage": key, "milliseconds": value}
            for key, value in results.items()
        ]
    ).round(4)

    frame.to_csv(OUTPUT, index=False)

    return frame


def main():

    results = measure()

    frame = write(results)

    print("\n========== LATENCY BENCHMARK ==========\n")

    print(frame.to_string(index=False))

    warm = results["warm_predict_soh_1_ms"]

    print(
        f"\nWarm single-row prediction: {warm:.3f} ms"
    )

    if warm > 100:

        print(
            "WARNING: warm prediction exceeds 100 ms, "
            "which is far above the real-time budget for a "
            "cycle-level service."
        )

    else:

        print(
            "Warm prediction is comfortably inside the "
            "one-second cycle budget."
        )

    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":

    main()
