"""
test_mqtt_client.py
-------------------
Transport behaviour, with no broker.

paho's Client is replaced by a fake that records calls, and the
network thread is never started, so these tests cover the
adapter's own logic: payload parsing, rejection handling, topic
wiring, reconnect re-subscribe, and the retained publish.

The model is the in-memory fixture bundle, because what is
being tested is the plumbing, not accuracy.
"""

import json
import threading
import time

import pytest

from conftest import discharge_samples, make_bundle

import mqtt_client
from mqtt_client import MqttConfig, MqttSohClient
from soh_service import OK, SohService, WARMING_UP


class FakeMessage:
    def __init__(self, payload, topic="bms/X/telemetry"):

        if isinstance(payload, (dict, list)):

            payload = json.dumps(payload).encode("utf-8")

        elif isinstance(payload, str):

            payload = payload.encode("utf-8")

        self.payload = payload
        self.topic = topic


class FakeClient:
    """
    Stands in for paho.Client, recording what the adapter does.
    """

    def __init__(self):

        self.published = []
        self.subscribed = []
        self.connected_to = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self._will = None
        self.reconnect_delay = None

        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def will_set(self, topic, payload, qos=0, retain=False):

        self._will = (topic, payload, qos, retain)

    def will(self):
        """
        Mirrors paho, where will() returns the stored will.
        """

        return self._will

    def reconnect_delay_set(self, min_delay=1, max_delay=120):

        self.reconnect_delay = (min_delay, max_delay)

    def username_pw_set(self, username, password=None):

        self.credentials = (username, password)

    def connect(self, host, port):

        self.connected_to = (host, port)

        # paho calls on_connect from its network loop once the
        # connection is up. Firing it here keeps the fake
        # faithful, so the subscribe path is exercised by start()
        # rather than only when a test asks for it by hand.
        if self.on_connect is not None:

            self.fire_connect()

    def subscribe(self, topic, qos=0):

        self.subscribed.append((topic, qos))

        return (0, 1)

    def publish(self, topic, payload=None, qos=0, retain=False):

        self.published.append({
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain
        })

        return (0, 1)

    def loop_start(self):

        self.loop_started = True

    def loop_stop(self):

        self.loop_stopped = True

    def disconnect(self):

        self.disconnected = True

    # -- helpers the tests use to drive callbacks by hand ------
    def fire_connect(self, reason_code=None):

        if reason_code is None:

            class _Ok:

                is_failure = False

            reason_code = _Ok()

        self.on_connect(
            self, None, None, reason_code, None
        )

    def fire_disconnect(self):

        self.on_disconnect(self, None, None, 0, None)

    def last_payload(self):

        for message in reversed(self.published):

            if message["payload"] is None:

                continue

            return json.loads(message["payload"])

    def payloads(self):

        return [
            json.loads(m["payload"])
            for m in self.published
            if m["payload"] is not None
        ]


def make_client(cell="X", **kwargs):
    """
    Build an adapter with a fake transport and the fixture model.
    """

    config = MqttConfig(cell_id=cell, **kwargs)
    fake = FakeClient()

    adapter = MqttSohClient(
        config,
        service=SohService(
            bundle=make_bundle(), cell_id=cell
        ),
        client=fake
    )

    return adapter, fake, config


def telemetry(
    timestamp, voltage=3.5, current=-2.0, temperature=30.0
):

    return {
        "timestamp": timestamp,
        "voltage": voltage,
        "current": current,
        "temperature": temperature
    }


def send(adapter, fake, payload):

    """
    Deliver a payload through the registered callback, the way
    paho would.
    """

    fake.on_message(
        fake, None, FakeMessage(payload)
    )


def run_a_cycle(adapter, fake, start=0.0):
    """
    Push one complete discharge plus the charge that closes it.
    """

    clock = start

    for timestamp, voltage, current, temperature in (
        discharge_samples(n=200, start_time=start)
    ):

        send(adapter, fake, telemetry(
            timestamp, voltage, current, temperature
        ))

        clock = timestamp

    for i in range(5):

        clock += 3.0

        send(adapter, fake, telemetry(
            clock, 4.0, 1.5, 30.0
        ))

    return clock


# ----------------------------------------------------------------------
# Topics and client construction
# ----------------------------------------------------------------------
def test_topics_default_to_bms_cell():

    config = MqttConfig(cell_id="B0005")

    assert config.telemetry_topic == "bms/B0005/telemetry"
    assert config.soh_topic == "bms/B0005/soh"
    assert config.client_id == "soh-B0005"


def test_topics_can_be_overridden():

    config = MqttConfig(
        cell_id="B0005",
        root="fleet/plant1",
        telemetry_topic="in/custom",
        soh_topic="out/custom"
    )

    assert config.telemetry_topic == "in/custom"
    assert config.soh_topic == "out/custom"


def test_a_real_paho_client_is_built_when_none_is_given():

    # The adapter is only usable if this actually constructs, so
    # it is asserted rather than assumed. Needs no broker.
    config = MqttConfig(cell_id="Z1")

    adapter = MqttSohClient(
        config,
        service=SohService(
            bundle=make_bundle(), cell_id="Z1"
        )
    )

    assert adapter.client is not None
    assert adapter.client.on_connect is not None
    assert adapter.client.on_message is not None
    assert adapter.client.on_disconnect is not None


def test_a_last_will_is_registered():

    adapter, fake, _ = make_client()

    # paho 2.1 exposes no public getter for the stored will, so
    # the assertion runs against the fake rather than reaching
    # into paho's internals, which would break on any upgrade.
    topic, payload, qos, retain = fake.will()

    assert topic == "bms/X/soh"
    assert retain is True
    assert json.loads(payload)["status"] == "offline"


def test_credentials_are_passed_through():

    adapter, fake, _ = make_client(username="user", password="secret")

    assert fake.credentials == ("user", "secret")


# ----------------------------------------------------------------------
# Load order, the reason start() is written the way it is
# ----------------------------------------------------------------------
def test_the_model_is_loaded_before_the_broker_is_touched():

    calls = []

    class RecordingService(SohService):
        """
        Records construction so load order is observable.
        """

        def __init__(self, *args, **kwargs):

            calls.append("service")

            super().__init__(*args, **kwargs)

    class OrderingClient(FakeClient):

        def connect(self, host, port):

            calls.append("connect")
            super().connect(host, port)

        def subscribe(self, topic, qos=0):

            calls.append("subscribe")

            return super().subscribe(topic, qos)

    config = MqttConfig(cell_id="B0005")
    fake = OrderingClient()

    adapter = MqttSohClient(
        config, service=RecordingService(
            bundle=make_bundle(), cell_id="B0005"
        ), client=fake
    )

    adapter.start()

    try:

        assert calls == [
            "service", "connect", "subscribe"
        ], (
            "the model must be loaded before connecting and "
            "subscribing, so no telemetry arrives with nowhere "
            "to go"
        )

    finally:

        adapter.stop()

    assert fake.loop_started is True
    assert fake.loop_stopped is True
    assert fake.disconnected is True


def test_subscribe_uses_qos_1_so_no_sample_is_silently_lost():

    adapter, fake, _ = make_client()

    adapter.start()

    try:

        topic, qos = fake.subscribed[0]

        assert topic == "bms/X/telemetry"

        # At-least-once. A dropped sample is a hole in a
        # current trace, and a hole biases the capacity integral.
        assert qos == 1

    finally:

        adapter.stop()


# ----------------------------------------------------------------------
# Reconnect
# ----------------------------------------------------------------------
def test_a_reconnect_resubscribes():

    adapter, fake, _ = make_client()

    adapter.start()

    try:

        assert len(fake.subscribed) == 1

        fake.fire_disconnect()

        assert adapter.connected is False

        fake.fire_connect()

        assert adapter.connected is True

        # Without this, a broker restart silently ends the
        # stream and the device goes on looking healthy.
        assert len(fake.subscribed) == 2

    finally:

        adapter.stop()


def test_a_refused_connection_is_not_treated_as_connected():

    adapter, fake, _ = make_client()

    class _Refused:

        is_failure = True

    fake.fire_connect(_Refused())

    assert adapter.connected is False
    assert fake.subscribed == []


# ----------------------------------------------------------------------
# Payload handling
# ----------------------------------------------------------------------
def test_samples_are_ingested_and_counted():

    adapter, fake, _ = make_client()

    assert adapter.samples_accepted == 0
    assert adapter.service.read()["status"] == WARMING_UP

    for i in range(10):

        send(adapter, fake, telemetry(float(i)))

    assert adapter.samples_accepted == 10
    assert adapter.samples_rejected == 0


def test_a_complete_cycle_publishes_a_reading():

    adapter, fake, _ = make_client()

    run_a_cycle(adapter, fake)

    assert adapter.cycles_predicted == 1
    assert adapter.service.read()["soh"] is not None

    payloads = fake.payloads()

    assert payloads, "a cycle close must publish"

    last = payloads[-1]

    assert last["soh"] is not None
    assert last["status"] == OK
    assert last["cell_id"] == "X"
    assert last["cycles_seen"] == 1


def test_a_malformed_payload_never_raises():

    adapter, fake, _ = make_client()

    # These are all things a real device or bus will produce.
    for payload in [
        b"not json at all",
        b"\xff\xfe\x00binary",
        json.dumps([1, 2, 3]).encode(),
        json.dumps("a string").encode(),
        json.dumps({}).encode(),
        json.dumps({"timestamp": 1.0}).encode(),
    ]:

        fake.on_message(fake, None, FakeMessage(payload))

    # The point is that on_message swallows everything: it runs
    # on paho's network thread, where an exception stops
    # delivery for the whole process.
    assert adapter.samples_accepted == 0
    assert adapter.samples_rejected == 6


def test_a_missing_field_is_rejected_not_guessed():

    adapter, fake, _ = make_client()

    send(adapter, fake, {
        "timestamp": 1.0, "voltage": 3.5, "current": -2.0
    })

    assert adapter.samples_rejected == 1
    assert adapter.samples_accepted == 0


def test_a_non_numeric_field_is_rejected():

    adapter, fake, _ = make_client()

    for field in ["voltage", "current", "temperature",
                  "timestamp"]:

        payload = telemetry(1.0)
        payload[field] = "warm"

        send(adapter, fake, payload)

    assert adapter.samples_rejected == 4
    assert adapter.samples_accepted == 0


def test_a_missing_timestamp_is_refused_rather_than_assumed():

    adapter, fake, _ = make_client()

    send(adapter, fake, {
        "voltage": 3.5, "current": -2.0,
        "temperature": 30.0
    })

    # Assuming 1 s would fabricate discharge_duration_s and the
    # capacity integral, which are the model's most important
    # time-based inputs. Refusing is the honest option.
    assert adapter.samples_rejected == 1
    assert adapter.samples_accepted == 0


def test_numeric_strings_are_accepted():

    adapter, fake, _ = make_client()

    send(adapter, fake, {
        "timestamp": "1.0",
        "voltage": "3.5",
        "current": "-2.0",
        "temperature": "30.0"
    })

    # JSON from a microcontroller often carries everything as a
    # string, and rejecting that would be unhelpful.
    assert adapter.samples_accepted == 1
    assert adapter.samples_rejected == 0


def test_unknown_fields_are_ignored():

    adapter, fake, _ = make_client()

    payload = telemetry(1.0)
    payload["cell_id"] = "X"
    payload["rssi"] = -70
    payload["firmware"] = "2.1.0"

    send(adapter, fake, payload)

    assert adapter.samples_accepted == 1
    assert adapter.samples_rejected == 0


def test_a_bad_ambient_temperature_does_not_fail_the_sample():

    adapter, fake, _ = make_client()

    payload = telemetry(1.0)
    payload["ambient_temperature"] = "warm"

    send(adapter, fake, payload)

    # Ambient is one input of 21 and the model imputes missing
    # values, so a bad one is dropped rather than failing the
    # sample. The alternative is throwing away real telemetry
    # over an optional field.
    assert adapter.samples_accepted == 1
    assert adapter.samples_rejected == 0


# ----------------------------------------------------------------------
# Duplicates, which the tracker handles
# ----------------------------------------------------------------------
def test_a_duplicate_delivery_cannot_corrupt_a_cycle():

    adapter, fake, _ = make_client()

    # A QoS 1 broker may redeliver whenever it does not get an
    # ack in time. This is the scenario the tracker's timestamp
    # guard exists for.
    for timestamp, voltage, current, temperature in (
        discharge_samples(n=200)
    ):

        payload = telemetry(
            timestamp, voltage, current, temperature
        )

        send(adapter, fake, payload)
        send(adapter, fake, payload)

    clock = 1000.0

    for i in range(5):

        clock += 3.0

        send(adapter, fake, telemetry(
            clock, 4.0, 1.5, 30.0
        ))

    assert adapter.cycles_predicted == 1
    assert adapter.samples_out_of_order == 200

    row = adapter.service.tracker

    # A duplicate would put a zero-width step into the
    # trapezoidal capacity integral and take real charge out of
    # it. 200 samples, 3 s apart, 2 A is about 0.33 Ah; a
    # doubled-up integration drifts well clear of that.
    assert row.out_of_order_samples == 200

    result = adapter.service.read()

    assert result["soh"] is not None
    assert result["status"] == OK


def test_out_of_order_samples_are_surfaced_on_the_status_topic():

    adapter, fake, _ = make_client()

    run_a_cycle(adapter, fake, start=0.0)

    # Rewind the clock, which is what a restarted device does.
    send(adapter, fake, telemetry(5.0))
    send(adapter, fake, telemetry(6.0))

    # The counters only reach the status topic when something
    # publishes, which in production is the per-second loop.
    adapter._publish_status(force=True)

    last = fake.last_payload()

    assert last["samples_out_of_order"] == 2


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------
def test_a_failed_prediction_keeps_the_previous_reading():

    adapter, fake, _ = make_client()

    run_a_cycle(adapter, fake)

    good = adapter.service.read()["soh"]

    # Break the feature list the model is read from. The service
    # copies it at construction, so the bundle is the thing that
    # has to change, not service.features.
    adapter.service.bundle["features"] = ["does_not_exist"]

    clock = 10000.0

    for i in range(200):

        clock += 3.0

        send(adapter, fake, telemetry(
            clock, 3.5, -2.0, 30.0
        ))

    for i in range(5):

        clock += 3.0

        send(adapter, fake, telemetry(
            clock, 4.0, 1.5, 30.0
        ))

    state = adapter.service.read()

    # A stale reading is more useful to a device than none, but
    # the failure has to be visible in the status.
    assert state["soh"] == good
    assert state["status"] == "failed"
    assert "error" in state

    published = fake.last_payload()

    assert published["status"] == "failed"


# ----------------------------------------------------------------------
# Publishing
# ----------------------------------------------------------------------
def test_the_reading_is_published_retained():

    adapter, fake, _ = make_client()

    adapter._publish_status(force=True)

    message = fake.published[-1]

    assert message["topic"] == "bms/X/soh"
    assert message["retain"] is True
    assert message["qos"] == 0


def test_an_unchanged_reading_is_not_republished():

    adapter, fake, _ = make_client()

    run_a_cycle(adapter, fake)

    before = len(fake.published)

    for _ in range(5):

        adapter._publish_status()

    # The value only changes once every few thousand seconds, so
    # the per-second loop should not rewrite an identical
    # payload.
    assert len(fake.published) == before


def test_the_publish_loop_runs_and_stops_cleanly():

    adapter, fake, config = make_client()

    adapter.config.publish_interval_s = 0.02

    adapter.start()

    try:

        deadline = time.time() + 2.0

        while (
            len(fake.published) < 3
            and time.time() < deadline
        ):

            time.sleep(0.01)

        assert len(fake.published) >= 3

    finally:

        adapter.stop()

    # The thread must be gone, not left running after stop().
    assert adapter._publisher is None


def test_stop_flushes_a_trailing_discharge():

    adapter, fake, _ = make_client()

    for timestamp, voltage, current, temperature in (
        discharge_samples(n=200)
    ):

        send(adapter, fake, telemetry(
            timestamp, voltage, current, temperature
        ))

    # A discharge with no positive current after it cannot close
    # on its own, so without the flush a whole cycle is lost.
    assert adapter.service.read()["soh"] is None

    adapter.stop()

    assert adapter.cycles_predicted == 1
    assert adapter.service.read()["soh"] is not None
    assert fake.disconnected is True


def test_stop_clears_the_retained_will():

    adapter, fake, _ = make_client()

    adapter.start()

    try:

        adapter._publish_status(force=True)

    finally:

        adapter.stop()

    # A clean shutdown must not leave the offline message in
    # place of a real reading.
    cleared = fake.published[-1]

    assert cleared["payload"] is None
    assert cleared["retain"] is True


def test_status_reports_the_counters():

    adapter, fake, _ = make_client()

    run_a_cycle(adapter, fake)

    report = adapter.status()

    assert report["telemetry_topic"] == "bms/X/telemetry"
    assert report["soh_topic"] == "bms/X/soh"
    assert report["samples_accepted"] == 205
    assert report["cycles_predicted"] == 1
    assert report["publishes"] >= 1
    assert report["state"]["soh"] is not None


# ----------------------------------------------------------------------
# Concurrency
# ----------------------------------------------------------------------
def test_ingestion_and_reads_do_not_interleave():

    adapter, fake, _ = make_client()

    stop = threading.Event()
    errors = []

    def reader():

        while not stop.is_set():

            try:

                adapter.service.read()
                adapter._publish_status()

            except Exception as error:

                errors.append(error)
                return

    threads = [
        threading.Thread(target=reader)
        for _ in range(3)
    ]

    for thread in threads:

        thread.start()

    try:

        run_a_cycle(adapter, fake)

    finally:

        stop.set()

        for thread in threads:

            thread.join(timeout=2.0)

    # SohService is not thread-safe and the tracker assumes a
    # sample is the newest thing seen, so ingestion and reads are
    # serialised behind one lock rather than left to paho's
    # ordering.
    assert errors == []


# ----------------------------------------------------------------------
# The hot path budget
# ----------------------------------------------------------------------
def test_per_message_cost_is_well_inside_a_second():

    adapter, fake, _ = make_client()

    # Warm up.
    for i in range(100):

        send(adapter, fake, telemetry(float(i)))

    count = 3000
    payloads = [
        telemetry(1000.0 + i) for i in range(count)
    ]

    started = time.perf_counter()

    for payload in payloads:

        fake.on_message(fake, None, FakeMessage(payload))

    per_message_ms = (
        (time.perf_counter() - started)
        / count * 1000.0
    )

    # A device publishes once a second, so parse plus dispatch
    # has a 1000 ms budget. The adapter must not come close.
    assert per_message_ms < 1.0, (
        f"per-message cost {per_message_ms:.4f} ms"
    )
