"""
test_alert_delivery.py
----------------------
Grafana can evaluate a rule to `firing` and still deliver nothing: a
firing rule only becomes a notification if a contact point exists and
a notification policy routes to it. These tests hold the wiring that
makes the firing rule in scripts/verify_alerts.sh end in an actual
delivery:

  * the compose stack provisions a webhook contact point (alert-sink,
    a local mock receiver that records each notification);
  * a notification policy routes to that exact contact point by name;
  * the receiver runs in docker-compose.yml and writes to the logs/
    volume the check inspects.

The files are read as text; PyYAML is not a project dependency.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ALERTING = REPO / "docker" / "grafana" / "provisioning" / "alerting"
CONTACT_POINTS = ALERTING / "contact_points.yml"
POLICIES = ALERTING / "notification_policies.yml"

COMPOSE = REPO / "docker-compose.yml"
RECEIVER = REPO / "docker" / "webhook_receiver.py"

RECEIVER_NAME = "battery-pdm-webhook"


def read(path):
    return path.read_text()


def test_the_webhook_receiver_exists():
    assert RECEIVER.is_file(), "the mock webhook receiver is missing"


def test_the_contact_point_is_a_webhook_to_the_receiver():
    text = read(CONTACT_POINTS)

    assert f"name: {RECEIVER_NAME}" in text
    assert "type: webhook" in text
    assert "url: http://alert-sink:9000/" in text


def test_the_policy_routes_to_that_contact_point():
    text = read(POLICIES)

    assert f"receiver: {RECEIVER_NAME}" in text, (
        "no notification policy routes to the webhook contact point, so a "
        "firing rule would still be delivered nowhere"
    )


def test_compose_runs_the_receiver_and_mounts_logs():
    text = read(COMPOSE)

    assert "alert-sink:" in text
    assert "python docker/webhook_receiver.py" in text
    assert "./logs:/app/logs" in text
