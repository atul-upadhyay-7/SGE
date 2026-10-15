"""
test_readme_numbers.py
----------------------
check_numbers.py must pass on the committed documents, and must
fail when a number inside a marked region is tampered with.

The checker is loaded and invoked in its own module (not through
`sys.executable`): some environments report a `sys.executable` that
is not the interpreter running the tests, and shelling out to it
would silently run something else and pass vacuously.

The failure tests copy README.md, CHANGES.md and
data/processed/report_numbers.json into a temporary checkout and run
the checker with --root, so the real documents are never modified.
"""

import importlib.util
import json
import random
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# check_numbers.py imports report_render by bare module name, the
# way it does when run as a script from scripts/.
sys.path.insert(0, str(REPO / "scripts"))

CHECKER_SPEC = importlib.util.spec_from_file_location(
    "check_numbers", REPO / "scripts" / "check_numbers.py"
)

# Same token definition as scripts/report_render.NUMBER: a number
# not glued to a letter or another digit, so cell ids (B0005), group
# ids (G1) and ordinals (10th) are ignored.
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


def run_check(capsys, root=None):
    checker = importlib.util.module_from_spec(CHECKER_SPEC)
    CHECKER_SPEC.loader.exec_module(checker)

    argv = ["--root", str(root)] if root is not None else []
    code = checker.main(argv)
    out = capsys.readouterr().out

    # A vacuous pass is worse than a failure: the checker must have
    # said something about regions, whether it passed or failed.
    assert "region" in out
    return code, out


def make_root(tmp_path):
    """A minimal checkout: the two documents and the numbers JSON."""

    root = tmp_path / "checkout"
    (root / "data" / "processed").mkdir(parents=True)
    shutil.copy(REPO / "README.md", root / "README.md")
    shutil.copy(REPO / "CHANGES.md", root / "CHANGES.md")
    shutil.copy(
        REPO / "data" / "processed" / "report_numbers.json",
        root / "data" / "processed" / "report_numbers.json",
    )
    return root


def edit_region(text, name, transform):
    """Apply `transform` to the inner text of region `name`."""

    start = f"<!-- make_report:{name} -->"
    end = f"<!-- make_report:{name}:end -->"
    pre, rest = text.split(start, 1)
    inner, post = rest.split(end, 1)
    return pre + start + transform(inner) + end + post


def bump(token):
    """A token that differs from `token` in its last digit."""

    if "." in token:
        head, decimals = token.split(".")
        return f"{head}.{int(decimals) + 1:0{len(decimals)}d}"
    return str(int(token) + 1)


def test_committed_documents_pass(capsys):
    code, out = run_check(capsys)

    assert code == 0, out
    assert "rendered placeholders" in out


def test_edited_number_fails(capsys, tmp_path):
    root = make_root(tmp_path)
    readme = root / "README.md"
    text = readme.read_text()

    edited = {}

    def bump_first_number(inner):
        match = NUMBER.search(inner)
        assert match, "no number found in the results region"
        edited["token"] = match.group()
        return inner[: match.start()] + bump(match.group()) + inner[match.end():]

    readme.write_text(edit_region(text, "results", bump_first_number))

    code, out = run_check(capsys, root)

    assert code == 1, out
    assert bump(edited["token"]) in out


def test_random_fake_number_fails(capsys, tmp_path):
    root = make_root(tmp_path)
    readme = root / "README.md"
    text = readme.read_text()

    rng = random.Random(20261016)
    fake = f"{rng.uniform(1000, 9999):.4f}"
    while fake in text:
        fake = f"{rng.uniform(1000, 9999):.4f}"

    readme.write_text(
        edit_region(text, "results", lambda inner: inner + f"\n| fake metric | {fake} |\n")
    )

    code, out = run_check(capsys, root)

    assert code == 1, out
    assert fake in out


def test_number_typed_into_template_fails(capsys, tmp_path):
    """
    A number pasted into both the document and the template passes
    the re-render diff, so only the "no bare numbers" rule can catch
    it. This is the check that makes hand-typed numbers impossible
    even when someone keeps the JSON consistent.
    """

    root = make_root(tmp_path)
    readme = root / "README.md"
    numbers_json = root / "data" / "processed" / "report_numbers.json"
    data = json.loads(numbers_json.read_text())

    fake = "7311.8008"
    snippet = f"\n| typed by hand | {fake}"

    readme.write_text(
        edit_region(readme.read_text(), "results",
                    lambda inner: inner.rstrip("\n") + snippet + "\n")
    )

    data["_templates"]["results"] += snippet
    numbers_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    code, out = run_check(capsys, root)

    assert code == 1, out
    assert fake in out
    assert "not a rendered placeholder" in out
    assert "differs from the JSON rendering" not in out
