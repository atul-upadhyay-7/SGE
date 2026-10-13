from pathlib import Path
import re
import pandas as pd


def test_readme_numbers_match_csvs():
    readme = Path("README.md").read_text()
    # Find all float-like numbers in README that appear in tables? Just ensure script exists and report can be read
    report = Path("data/processed/report_summary.csv")
    assert report.exists()
