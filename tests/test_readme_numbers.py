import subprocess
import sys
from pathlib import Path


def test_readme_numbers_are_backed_by_csvs():
    """Every number in the generated document regions must come from a CSV."""

    result = subprocess.run(
        [sys.executable, "scripts/check_numbers.py"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
