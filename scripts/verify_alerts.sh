#!/usr/bin/env bash
#
# verify_alerts.sh — prove alerts fire *and* are delivered.
#
# Brings up the compose stack with the demo profile (the NASA replay
# with injected faults), waits for Grafana's own alert API to report a
# firing rule, then waits for the provisioned contact point to deliver
# the notification to the local webhook receiver (alert-sink). Tears
# the stack down on exit.
#
# The firing rule is only the first half; the recorded payload in
# logs/alerts_received.jsonl is the second half. A rule that fires and
# is not delivered anywhere is half a notification path, so this script
# fails unless both happen.
#
# Usage:
#   bash scripts/verify_alerts.sh
#
# Environment:
#   PDM_URL        Grafana base URL (default: http://localhost:3050)
#   PYTHON         helper interpreter (default: python3)
#   PDM_WAIT_FIRE  seconds to wait for a firing rule (default: 120)
#   PDM_WAIT_HOOK  seconds to wait for a delivered payload (default: 90)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
PYTHON="${PYTHON:-python3}"
URL="${PDM_URL:-http://localhost:3050}"
WAIT_FIRE="${PDM_WAIT_FIRE:-120}"
WAIT_HOOK="${PDM_WAIT_HOOK:-90}"

LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/alerts_received.jsonl"

cleanup() {

    echo
    echo ">>> tearing the stack down"

    ( cd "$ROOT" && docker compose --profile demo down -v >/dev/null 2>&1 ) || true
}

trap cleanup EXIT

echo "============================================================"
echo "verify_alerts.sh"
echo "  root    : $ROOT"
echo "  grafana : $URL"
echo "  python  : $($PYTHON --version 2>&1)"
echo "============================================================"

mkdir -p "$LOG_DIR"
rm -f "$LOG_FILE"

echo
echo ">>> bringing up the stack with the demo profile (faults injected)"
( cd "$ROOT" && docker compose --profile demo up -d --build --force-recreate )

echo
echo ">>> waiting for a firing alert (Grafana's own alert API, ${WAIT_FIRE}s budget)"
fired=0
SECONDS=0

while [ "$SECONDS" -lt "$WAIT_FIRE" ]; do

    if "$PYTHON" "$HERE/grafana_alerts.py" --url "$URL" --require-firing \
        >/dev/null 2>&1; then

        fired=1
        break
    fi

    sleep 2
done

if [ "$fired" -ne 1 ]; then

    echo "    ERROR: no rule reported firing within ${WAIT_FIRE}s"
    echo "    last Grafana rule state:"
    "$PYTHON" "$HERE/grafana_alerts.py" --url "$URL" || true
    exit 1
fi

"$PYTHON" "$HERE/grafana_alerts.py" --url "$URL" --require-firing

echo
echo ">>> waiting for delivery to the webhook receiver (${WAIT_HOOK}s budget)"
delivered=0
SECONDS=0

while [ "$SECONDS" -lt "$WAIT_HOOK" ]; do

    if [ -s "$LOG_FILE" ]; then

        delivered=1
        break
    fi

    sleep 2
done

if [ "$delivered" -ne 1 ]; then

    echo "    ERROR: a rule fired but no notification was delivered within ${WAIT_HOOK}s"
    echo "    alert-sink logs:"
    ( cd "$ROOT" && docker compose logs --no-color alert-sink 2>&1 | tail -n 20 ) || true
    exit 1
fi

echo
echo ">>> delivered webhook payload (last of $(wc -l < "$LOG_FILE") line(s) in logs/alerts_received.jsonl)"
tail -n 1 "$LOG_FILE" | "$PYTHON" -m json.tool

echo
echo "============================================================"
echo "verify_alerts.sh: ALERT FIRED AND DELIVERED TO WEBHOOK"
echo "============================================================"
