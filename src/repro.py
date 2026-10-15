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
"""

REPRO_DECIMALS = 10


def rounded(frame, decimals=REPRO_DECIMALS):
    """Return `frame` with every float column rounded to `decimals`."""

    return frame.round(decimals)
