#!/usr/bin/env python
"""
check_numbers.py
----------------
Fail if a number in the generated regions of README.md,
CHANGES.md, docs/RESULTS.md, docs/SETUP.md or docs/MODEL_CARD.md is
not exactly what data/processed/report_numbers.json renders.

make_report.py writes that JSON: every number in a marked region is
a {{id}} placeholder rendered from the JSON's {"<id>": value}
mapping, and the placeholder templates are stored under the
reserved "_templates" key. Two independent checks run per region:

  (a) the region is re-rendered from the JSON (template plus
      values) and any difference fails — a hand-edited, stale or
      drifted number is caught here;
  (b) every number appearing in the region must be the rendering of
      some placeholder — a number typed straight into the document
      or into a template is caught here.

Numbers are no longer matched against CSV cells. The JSON is the
machine-readable contract between the CSVs and the documents:
make_report.py derives it from the CSVs (and fails if one is
missing, empty or lacks a metric), and verify.sh regenerates it in
a fresh clone before running this check.
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from report_render import (
    NUMBER,
    RenderError,
    TEMPLATES_KEY,
    numeric_renderings,
    placeholder_ids,
    render_into,
)

ROOT = Path(__file__).resolve().parent.parent

DOC_NAMES = [
    "README.md",
    "CHANGES.md",
    "docs/RESULTS.md",
    "docs/SETUP.md",
    "docs/MODEL_CARD.md",
]

DOCS = [ROOT / name for name in DOC_NAMES]

REGION = re.compile(
    r"<!-- make_report:([a-z_]+) -->(.*?)<!-- make_report:\1:end -->",
    re.DOTALL,
)


def fail(messages):
    print(f"NUMBER CHECK FAILURES ({len(messages)}):")
    for message in messages:
        print(f"  - {message}")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the numbers in the marked README, CHANGES and docs "
                    "regions against data/processed/report_numbers.json."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="check this checkout instead of the one the script lives in",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else ROOT
    docs = [root / name for name in DOC_NAMES]
    numbers_json = root / "data" / "processed" / "report_numbers.json"

    if not numbers_json.exists():
        print(
            f"MISSING: {numbers_json} — run scripts/make_report.py to "
            f"generate it from the CSVs"
        )
        return 1

    try:
        data = json.loads(numbers_json.read_text())
    except json.JSONDecodeError as exc:
        print(f"INVALID JSON in {numbers_json}: {exc}")
        return 1

    templates = data.get(TEMPLATES_KEY)
    if not isinstance(templates, dict) or not templates:
        print(
            f"INVALID JSON in {numbers_json}: no {TEMPLATES_KEY!r} region "
            f"templates, so the regions cannot be re-rendered"
        )
        return 1

    values = {key: value for key, value in data.items() if not key.startswith("_")}
    for key, value in sorted(values.items()):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            print(f"INVALID VALUE in {numbers_json}: {key!r} -> {value!r} is not a number")
            return 1

    failures = []
    regions = []

    for doc in docs:
        if not doc.exists():
            print(f"MISSING DOCUMENT: {doc}")
            return 1
        found = list(REGION.finditer(doc.read_text()))
        if not found:
            failures.append(f"{doc.name}: no marked regions found")
        for match in found:
            regions.append((doc.name, match.group(1), match.group(2)))

    if not regions:
        return fail(failures + ["no marked regions found in any document"])

    total_numbers = 0
    used_templates = set()

    for doc_name, name, inner in regions:
        if name not in templates:
            failures.append(
                f"{doc_name} region {name!r}: no {TEMPLATES_KEY!r} entry to render it from"
            )
            continue
        used_templates.add(name)

        try:
            expected, rendered_strings = render_into(templates[name], values)
        except RenderError as exc:
            failures.append(f"{doc_name} region {name!r}: {exc}")
            continue

        actual_lines = inner.strip().splitlines()
        expected_lines = expected.strip().splitlines()
        if actual_lines != expected_lines:
            diff = "\n".join(difflib.unified_diff(
                actual_lines,
                expected_lines,
                fromfile=f"{doc_name} region {name} (committed)",
                tofile=f"{doc_name} region {name} (rendered from JSON)",
                lineterm="",
            ))
            failures.append(
                f"{doc_name} region {name!r} differs from the JSON rendering:\n{diff}"
            )

        allowed = numeric_renderings(rendered_strings)
        tokens = NUMBER.findall(inner)
        total_numbers += len(tokens)
        unbacked = sorted({token for token in tokens if token not in allowed})
        if unbacked:
            failures.append(
                f"{doc_name} region {name!r}: numbers that are not a rendered "
                f"placeholder: {', '.join(unbacked)}"
            )

    for name in sorted(set(templates) - used_templates):
        failures.append(
            f"{TEMPLATES_KEY!r} has region {name!r} but no document contains its markers"
        )

    used_ids = set()
    for template in templates.values():
        used_ids.update(placeholder_ids(template))
    unused_values = sorted(set(values) - used_ids)
    if unused_values:
        failures.append(
            f"{numbers_json} has values no template uses: {', '.join(unused_values)}"
        )

    if failures:
        return fail(failures)

    print(
        f"all {total_numbers} numbers in {len(regions)} region(s) are rendered "
        f"placeholders of {len(values)} values from {numbers_json}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
