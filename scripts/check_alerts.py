#!/usr/bin/env python
"""
check_alerts.py
---------------
Evaluate the provisioned Grafana alert rules against the live series
the dashboard datasource serves, and print whether each rule is
firing.

Why this exists
---------------
Grafana cannot run these rules itself. The datasource is
simpod-json-datasource, whose plugin.json declares
"alerting": false and "backend": false: it is a frontend-only
datasource, so Grafana's alerting engine reports
[plugin.unavailable] for every query (visible on the rule's health
page). Provisioned rules that can never be evaluated are not a test.

This script reproduces the rule exactly — query the target, reduce
to the last value, compare to the threshold — using the same JSON
API and the same bounds the rule file carries, so the condition is
exercised end to end even though Grafana's own engine cannot run it.

The rule spec is imported from scripts/make_alert_rules.py, so the
bound checked here is the bound written into the YAML.

Usage
  python scripts/check_alerts.py                 # evaluate live data
  python scripts/check_alerts.py --require-firing # exit 1 unless a rule fires
  python scripts/check_alerts.py --base http://localhost:8099
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_alert_rules import build_rules, trained_peak_temperature_c


DEFAULT_BASE = "http://localhost:8099"


def reduce_last(series):
    """Grafana's reduce(last): the final finite value of the series."""

    values = [v for v in series if v is not None]

    return float(values[-1]) if values else None


def evaluate(value, threshold, direction):
    """The threshold condition: value <threshold|>threshold."""

    if value is None:

        return False

    if direction == "lt":

        return value < threshold

    if direction == "gt":

        return value > threshold

    raise ValueError(f"unknown direction {direction!r}")


def fetch_series(target, base=DEFAULT_BASE, timeout=5.0):
    """
    Pull one datasource target from the JSON API and return its
    values in order. Raises on a transport error so a missing API is
    never mistaken for "no alert".
    """

    payload = json.dumps(
        {
            "range": {
                "from": "2000-01-01T00:00:00Z",
                "to": "2100-01-01T00:00:00Z",
            },
            "targets": [{"target": target, "refId": "A"}],
        }
    ).encode()

    request = urllib.request.Request(
        f"{base}/query",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:

        data = json.load(response)

    if not data:

        return []

    return [point[0] for point in data[0].get("datapoints", [])]


def check(rules, base=DEFAULT_BASE):
    """Return a list of (rule, value, firing) results."""

    results = []

    for rule in rules:

        series = fetch_series(rule["target"], base=base)
        value = reduce_last(series)
        firing = evaluate(value, rule["threshold"], rule["direction"])

        results.append((rule, value, firing))

    return results


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument(
        "--require-firing",
        action="store_true",
        help="exit non-zero if no rule is firing",
    )

    args = parser.parse_args(argv)

    rules = build_rules(trained_peak_temperature_c())

    try:

        results = check(rules, base=args.base)

    except Exception as exc:  # transport/parse failure, not "no alert"

        print(f"ERROR: could not query {args.base}: {exc}")
        return 2

    print(f"live series from {args.base}\n")

    print(f"{'rule':45} {'value':>10} {'bound':>10}  verdict")

    firing_any = False

    for rule, value, firing in results:

        shown = "n/a" if value is None else f"{value:.4f}"

        bound = f"{rule['threshold']:.4f} ({rule['direction']})"

        verdict = "FIRING" if firing else "normal"

        firing_any = firing_any or firing

        print(f"{rule['uid']:45} {shown:>10} {bound:>12}  {verdict}")

    if args.require_firing and not firing_any:

        print("\nno rule is firing")
        return 1

    return 0


if __name__ == "__main__":

    sys.exit(main())
