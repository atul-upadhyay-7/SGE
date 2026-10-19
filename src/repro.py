"""
repro.py
--------
Reproducibility helpers shared by the pipeline stages.

The pipeline rewrites the committed CSVs under data/processed/ on
every run, and verify.sh fails when a rerun leaves git status dirty.
A rerun on one machine reproduces bit-identical floats, but a
different machine or thread count can flip the last digits of a
floating-point summation. Rounding every written float to
REPRO_DECIMALS (10) makes the committed files stable against that:
the documents render at most 3 decimals, so rounding is far below
the precision any number is quoted at.

Usage at a write site:

    rounded(frame).to_csv(path, index=False)

DataFrame.round only touches float columns, so integer counts,
strings and timestamps pass through unchanged.

A metric column that can hold a value outside a sane range is built
with `stable_metric` instead: it rounds the float and replaces an
out-of-range value with the word UNSTABLE, so a jitter-dominated
number never reaches a committed file. Round the value before it is
mixed with the flag, because round() on the resulting object column
can no longer reach it.
"""

import math

REPRO_DECIMALS = 10

# A metric this far outside a sane range is not a measurement. A
# regression that lands here has extrapolated on inputs orders of
# magnitude past its training range (an unbounded linear model fed a
# near-zero baseline, say), so the exact number is dominated by
# floating-point noise and is not stable across machines or thread
# counts. Writing it as a number would make a committed CSV fail the
# verify.sh clean-tree check on another box, so it is written as the
# word UNSTABLE instead of a raw float.
R2_UNSTABLE_BELOW = -1000.0
MAE_UNSTABLE_ABOVE_PCT = 100.0

UNSTABLE = "unstable"


def rounded(frame, decimals=REPRO_DECIMALS):
    """Return `frame` with every float column rounded to `decimals`."""

    return frame.round(decimals)


def stable_metric(value, low=None, high=None, decimals=REPRO_DECIMALS):
    """
    One written metric: rounded to `decimals`, or UNSTABLE when it falls
    outside [low, high].

    Rounding is what `rounded` does to a whole frame; this is the
    per-value form for a column that mixes floats with the UNSTABLE
    flag, where `DataFrame.round` cannot reach the floats in the now
    object-typed column. A NaN passes through unchanged.
    """

    if value is None:
        return value

    if isinstance(value, float) and math.isnan(value):
        return value

    if low is not None and value < low:
        return UNSTABLE

    if high is not None and value > high:
        return UNSTABLE

    return round(float(value), decimals)
