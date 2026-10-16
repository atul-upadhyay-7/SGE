#!/usr/bin/env python
"""
grafana_alerts.py
-----------------
Read the alert rules back from Grafana's own alerting engine and
print each rule's health and state.

This replaces the old scripts/check_alerts.py. That script
re-implemented the rule logic against the JSON API because the
datasource plugin could not run alerts; it proved nothing about
Grafana. The rules now query the Prometheus datasource, so this
reads Grafana's alert-rule API:

    GET /api/prometheus/grafana/api/v1/rules

For each rule it prints `health` ("ok" when the last evaluation
succeeded) and `state` ("firing", "pending", "inactive", "nodata",
"error", ...). With --require-firing it exits non-zero unless at
least one rule is firing, so it can back a demo or a check.

Usage
  python scripts/grafana_alerts.py
  python scripts/grafana_alerts.py --require-firing
  python scripts/grafana_alerts.py --url http://localhost:3050 \\
      --user admin --password admin
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:3050"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"

RULES_PATH = "/api/prometheus/grafana/api/v1/rules"


def fetch_rules(url, user, password, timeout=10.0):
    """Return the parsed alert-rule payload from Grafana."""

    request = urllib.request.Request(url.rstrip("/") + RULES_PATH)

    if user is not None:

        token = base64.b64encode(
            f"{user}:{password}".encode()
        ).decode()

        request.add_header("Authorization", f"Basic {token}")

    with urllib.request.urlopen(request, timeout=timeout) as response:

        return json.load(response)


def iter_rules(payload):
    """Yield (group_name, rule_dict) for every rule Grafana returned."""

    groups = (payload.get("data") or {}).get("groups") or []

    for group in groups:

        for rule in group.get("rules") or []:

            yield group.get("name", ""), rule


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--require-firing",
        action="store_true",
        help="exit non-zero unless at least one rule is firing",
    )

    args = parser.parse_args(argv)

    try:

        payload = fetch_rules(args.url, args.user, args.password)

    except urllib.error.URLError as exc:

        print(f"ERROR: could not read {args.url}{RULES_PATH}: {exc}")
        return 2

    print(f"alert rules from {args.url}{RULES_PATH}\n")
    print(f"{'rule':45} {'health':>8}  state")

    firing_any = False

    for _, rule in iter_rules(payload):

        state = rule.get("state", "?")
        health = rule.get("health", "?")

        firing_any = firing_any or state == "firing"

        print(f"{rule.get('name', ''):45} {health:>8}  {state}")

    if args.require_firing and not firing_any:

        print("\nno rule is firing")
        return 1

    return 0


if __name__ == "__main__":

    sys.exit(main())
