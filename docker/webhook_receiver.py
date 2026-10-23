"""
webhook_receiver.py
-------------------
Local mock webhook receiver for Grafana alert notifications.

Why this exists
---------------
Grafana evaluates the battery alert rules itself, but a rule that
fires and is not delivered anywhere is only half a notification path.
This tiny stdlib-only server is the destination of the provisioned
contact point in the compose stack: Grafana posts each notification
here, and the receiver appends one JSON line per delivery to
logs/alerts_received.jsonl so a run can prove the firing alert
actually left Grafana.

Grafana posts an Alertmanager-style JSON body (status, alerts[],
groupLabels, ...). The receiver records the parsed payload verbatim
plus a received-at timestamp and the request path; it does not
validate the shape.

Usage
  python docker/webhook_receiver.py                  # 0.0.0.0:9000
  PDM_WEBHOOK_PORT=9000 python docker/webhook_receiver.py
  PDM_WEBHOOK_LOG=/app/logs/alerts_received.jsonl python docker/webhook_receiver.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9000
DEFAULT_LOG = ROOT / "logs" / "alerts_received.jsonl"


def record(log_path, path, body):
    """Append one JSON line for a received payload, and print a summary."""

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:

        payload = json.loads(body.decode("utf-8"))

    except (ValueError, UnicodeDecodeError):

        payload = None

    entry = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "payload": payload,
    }

    if payload is None:

        entry["raw"] = body.decode("utf-8", "replace")

    with log_path.open("a", encoding="utf-8") as handle:

        handle.write(json.dumps(entry) + "\n")

    alerts = (payload or {}).get("alerts") or []

    names = sorted(
        alert.get("labels", {}).get("alertname", "?") for alert in alerts
    )

    print(
        f"[webhook] received {len(alerts)} alert(s) on {path}: "
        f"{', '.join(names) if names else 'no alertname'} -> {log_path}",
        flush=True,
    )

    return entry


class Handler(BaseHTTPRequestHandler):

    log_path = DEFAULT_LOG

    def _reply(self, code, body, content_type):

        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (http.server's name)

        length = int(self.headers.get("Content-Length", 0) or 0)
        # Cap the body: this receiver is unauthenticated inside the
        # compose network, and an unbounded read lets any neighbour fill
        # the alert log (and the disk behind it). Grafana alert payloads
        # are a few KB; 1 MiB is generous.
        if length > 1_048_576:
            self._reply(413, b"payload too large\n", "text/plain; charset=utf-8")
            return
        body = self.rfile.read(length) if length else b""

        record(self.log_path, self.path, body)

        self._reply(200, b"ok\n", "text/plain; charset=utf-8")

    def do_GET(self):  # noqa: N802

        # Readback of everything received so far, as newline-delimited
        # JSON, so a check can inspect deliveries without the file.
        path = Path(self.log_path)
        body = path.read_bytes() if path.exists() else b""

        self._reply(200, body, "application/x-ndjson")

    def log_message(self, *args):  # keep the request log to our own line

        pass


def main(argv=None):

    argv = sys.argv[1:] if argv is None else argv

    host = os.environ.get("PDM_WEBHOOK_HOST", DEFAULT_HOST)
    port = int(os.environ.get("PDM_WEBHOOK_PORT", DEFAULT_PORT))
    log_path = Path(os.environ.get("PDM_WEBHOOK_LOG", DEFAULT_LOG))

    Handler.log_path = log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((host, port), Handler)

    print(
        f"alert webhook receiver on http://{host}:{port}/ (log={log_path})",
        flush=True,
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        pass

    finally:

        server.server_close()


if __name__ == "__main__":

    main()
