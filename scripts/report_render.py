#!/usr/bin/env python
"""
report_render.py
----------------
The placeholder machinery shared by make_report.py and
check_numbers.py.

A report region is generated as a template in which every number is
a placeholder, plus a JSON mapping of every placeholder id to the
raw value that was read from a CSV:

    {
      "_templates": {"results": "| Coulomb | ... | {{soh_coulomb_mae:.2f}} |"},
      "soh_coulomb_mae": 0.7289
    }

Rendering substitutes each placeholder with format(value, spec), or
str(value) when the placeholder carries no spec. Keys starting with
"_" are reserved for structure (currently only "_templates", which
holds each region's placeholder template) and are never numbers.

check_numbers.py re-renders each marked region of README.md,
CHANGES.md and the docs/ documents from the JSON and fails on any
difference, and fails when a region contains a number that no
placeholder produced, so a number can only reach the documents
through this rendering.
"""

import re

# {{id}} or {{id:format_spec}}, e.g. {{rul_mean_mae:.1f}} or {{n:03d}}.
PLACEHOLDER = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)(?::([^{}]*))?\}\}")

# A number not glued to a letter or another digit on either side, so
# cell ids (B0005), group ids (G1) and ordinals (10th) are ignored.
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")

TEMPLATES_KEY = "_templates"


class RenderError(ValueError):
    """A template cannot be rendered from the JSON values."""


def render_into(template, values):
    """
    Render `template` from `values`.

    Returns (text, rendered_strings) where rendered_strings is the
    set of strings the placeholders produced, so a caller can tell
    which numbers in the text are placeholder renderings.
    """

    pieces = []
    rendered = set()
    pos = 0

    for match in PLACEHOLDER.finditer(template):
        pieces.append(template[pos:match.start()])
        pid, spec = match.group(1), match.group(2)

        if pid not in values:
            raise RenderError(f"placeholder {pid!r} has no value in the JSON")

        value = values[pid]

        if spec is None:
            text = str(value)
        else:
            try:
                text = format(value, spec)
            except (ValueError, TypeError) as exc:
                raise RenderError(
                    f"placeholder {pid!r}: cannot render {value!r} "
                    f"with spec {spec!r} ({exc})"
                ) from exc

        pieces.append(text)
        rendered.add(text)
        pos = match.end()

    pieces.append(template[pos:])
    return "".join(pieces), rendered


def render(template, values):
    return render_into(template, values)[0]


def placeholder_ids(template):
    return {match.group(1) for match in PLACEHOLDER.finditer(template)}


def numeric_renderings(rendered_strings):
    """The subset of renderings that are numbers."""

    return {text for text in rendered_strings if NUMBER.fullmatch(text)}
