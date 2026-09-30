"""
mqtt_client.py
--------------
MQTT transport for the streaming SOH service.

This file is deliberately thin. All the behaviour that has to
be correct already lives in CycleTracker and SohService and is
tested against the real .mat files without a broker. What is
left here is only the transport, and the rule for this module
is to not grow any logic into it that could be tested without
one. Message parsing, latching, staleness and the model all
belong behind SohService.

Topics
------
By default, for cell B0005:

    bms/B0005/telemetry   in    one sample per message
    bms/B0005/soh         out   the latched reading, per second

Telemetry in, SOH out, rather than one shared topic, so a
subscriber can consume the prediction without also having to
filter the raw sample stream, and so the prediction topic can
be retained and last-will-ed.

Why a retained, per-second publish of a latched value
------------------------------------------------------
The model cannot predict mid-cycle, so the reading only changes
once every 3000 to 3700 seconds. Publishing that change alone
would leave a subscriber with nothing to read in between, which
is no good to a device polling once a second.

So the value is republished every second, retained. Repeating a
constant is cheap on the wire and means a late subscriber gets
the current reading immediately on subscribe, with no need to
wait for the next boundary. That is what the retain flag is
for, and it is why the per-second publish is worth the traffic.

A subscriber that only cares about changes can watch for a
changed `cycle` field instead of a new message.

Load order
----------
The model is loaded before the broker connection is opened and
before the subscription is made. A cold load is about 1.7 s,
which is why it is done once here, at startup, rather than per
request. Starting the network loop before the model is ready
would mean accepting telemetry that arrives with nowhere to put
it.

Robustness
----------
Telemetry arrives from a device on a network that will drop,
reorder and duplicate messages, so:

  * A malformed payload is counted and skipped. It never
    raises out of the callback, because an exception on the
    network thread stops message delivery for the process.
  * A sample whose timestamp does not advance is dropped by the
    tracker. See the note on _last_timestamp in
    cycle_tracker.py: a duplicate would otherwise corrupt the
    capacity integral for that cycle.
  * A failed prediction is caught, reported on the status
    topic, and the previous good reading is kept.
  * Reconnects re-subscribe. Without that, a broker restart
    silently ends the stream, and the device goes on looking
    healthy.

Threading
---------
paho calls on_message on its network thread and the per-second
publish runs on a separate thread. SohService is not
thread-safe, and CycleTracker is single-threaded by design: a
sample has to be the newest thing seen, or the capacity integral
and the debounce counters are wrong. So sample ingestion and
reads are serialised behind one lock rather than relying on
paho's ordering.
"""

import json
import logging
import sys
import threading
import time
from pathlib import Path

# Put this directory and its parent on sys.path before any
# project import, so the module works both as a script and when
# imported as src.streaming.mqtt_client.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from soh_service import (
        FAILED,
        OK,
        STALE,
        WARMING_UP,
        SohService
    )
except ImportError:
    from .soh_service import (
        FAILED,
        OK,
        STALE,
        WARMING_UP,
        SohService
    )


LOGGER = logging.getLogger(__name__)


# How often the latched reading is republished. Matches the
# rate a device samples, so a subscriber reading once a second
# always finds something fresh.
DEFAULT_PUBLISH_INTERVAL_S = 1.0

# Telemetry QoS. At-least-once, because a dropped sample is a
# hole in a cycle's current trace and a hole biases the capacity
# integral. Duplicates are the cost of that guarantee and are
# handled by the tracker's timestamp guard.
TELEMETRY_QOS = 1

# The latched reading is republished every second and is
# retained, so at-most-once is enough. Losing one copy costs
# nothing, because the same value arrives a second later.
SOH_QOS = 0

# Fields accepted in a telemetry payload, mapped to the
# SohService argument each fills. Anything else in the payload
# is ignored, so a device can add fields without breaking this.
_SAMPLE_FIELDS = (
    ("voltage", "voltage"),
    ("current", "current"),
    ("temperature", "temperature")
)


class MqttConfig:
    """
    Connection and topic settings.
    """

    def __init__(
        self,
        cell_id,
        host="localhost",
        port=1883,
        telemetry_topic=None,
        soh_topic=None,
        root="bms",
        client_id=None,
        keepalive=30,
        username=None,
        password=None,
        publish_interval_s=DEFAULT_PUBLISH_INTERVAL_S
    ):

        self.cell_id = cell_id
        self.host = host
        self.port = int(port)

        base = f"{root}/{cell_id}"

        self.telemetry_topic = (
            telemetry_topic or f"{base}/telemetry"
        )
        self.soh_topic = soh_topic or f"{base}/soh"

        self.client_id = client_id or f"soh-{cell_id}"
        self.keepalive = int(keepalive)

        self.username = username
        self.password = password

        self.publish_interval_s = float(publish_interval_s)


class MqttSohClient:
    """
    Feed telemetry from MQTT, publish the latched SOH back.
    """

    def __init__(
        self,
        config,
        service=None,
        client=None
    ):

        self.config = config

        # Load the model before anything touches the network.
        if service is None:

            service = SohService(cell_id=config.cell_id)

        self.service = service

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._publisher = None

        # Counters. Published on the status topic so a fleet
        # operator can see a device dropping telemetry instead
        # of only seeing the device look quiet.
        self.samples_accepted = 0
        self.samples_rejected = 0
        self.samples_out_of_order = 0
        self.cycles_predicted = 0
        self.publishes = 0
        self.connected = False

        self._last_published = None

        self.client = (
            client if client is not None
            else self._build_client()
        )

        # Configure the transport here rather than inside
        # _build_client(). These are adapter decisions, not
        # transport-construction steps, and doing it here means an
        # injected client is configured and driven exactly like a
        # real one instead of the test having to remember to
        # attach the callbacks and the will.
        self._configure_transport(self.client)

    def _configure_transport(self, client):

        config = self.config

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        if config.username is not None:

            client.username_pw_set(
                config.username, config.password
            )

        # Last will: a subscriber that wants to know whether the
        # device is alive learns it from the retained message on
        # the SOH topic rather than by timing out. Without this a
        # device that loses power looks exactly like one that is
        # simply between cycles.
        client.will_set(
            config.soh_topic,
            json.dumps({
                "cell_id": config.cell_id,
                "status": "offline"
            }),
            qos=SOH_QOS,
            retain=True
        )

        # Reconnect with a backoff. A device on a flaky link
        # should retry rather than exit and take the service
        # down with it.
        client.reconnect_delay_set(
            min_delay=1, max_delay=60
        )

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------
    def _build_client(self):
        """
        Create the paho client.

        Only construction. Everything about how it is used, the
        callbacks, the credentials, the will and the reconnect
        backoff, is applied afterwards by
        _configure_transport().
        """

        try:

            import paho.mqtt.client as mqtt

        except ImportError as error:

            raise ImportError(
                "paho-mqtt is required for the MQTT adapter. "
                "Install it with:\n"
                "    pip install paho-mqtt\n"
                "The streaming core does not need it; only this "
                "transport does."
            ) from error

        return mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.client_id,
            clean_session=True
        )

    # ------------------------------------------------------------------
    # paho callbacks. These run on the network thread.
    # ------------------------------------------------------------------
    def _on_connect(
        self, client, userdata, flags, reason_code, properties
    ):

        if getattr(reason_code, "is_failure", False):

            LOGGER.error(
                "MQTT connect refused: %s", reason_code
            )

            self.connected = False

            return

        self.connected = True

        LOGGER.info(
            "Connected to %s:%s, subscribing to %s",
            self.config.host, self.config.port,
            self.config.telemetry_topic
        )

        # Subscribe here, not in start(), because a reconnect
        # gets on_connect again and would otherwise leave the
        # session subscribed to nothing while the service still
        # reports healthy.
        result, _ = client.subscribe(
            self.config.telemetry_topic,
            qos=TELEMETRY_QOS
        )

        if result != 0:

            LOGGER.error(
                "Subscribe to %s failed with code %s",
                self.config.telemetry_topic, result
            )

    def _on_disconnect(
        self, client, userdata, flags, reason_code, properties
    ):

        self.connected = False

        LOGGER.warning(
            "Disconnected from broker: %s", reason_code
        )

    def _on_message(self, client, userdata, message):

        """
        Handle one telemetry message.

        Never raises. This runs on paho's network thread, and an
        exception escaping here stops delivery for every
        subscription on that thread, so one bad payload from one
        device would take the whole process down.
        """

        try:

            self.handle_telemetry(message)

        except Exception:

            self.samples_rejected += 1

            LOGGER.exception(
                "Dropped a telemetry message on %s",
                self.config.telemetry_topic
            )

    # ------------------------------------------------------------------
    # Payload handling, separated from paho so it is testable
    # ------------------------------------------------------------------
    def handle_telemetry(self, message):
        """
        Parse and ingest one telemetry message.

        Returns the prediction dict when this sample closed a
        cycle, otherwise None.
        """

        try:

            payload = json.loads(
                message.payload.decode("utf-8")
            )

        except (ValueError, UnicodeDecodeError) as error:

            self.samples_rejected += 1

            LOGGER.warning(
                "Unparseable payload on %s: %s",
                self.config.telemetry_topic, error
            )

            return None

        if not isinstance(payload, dict):

            self.samples_rejected += 1

            LOGGER.warning(
                "Expected a JSON object, got %s",
                type(payload).__name__
            )

            return None

        sample = {}

        for key, argument in _SAMPLE_FIELDS:

            if key not in payload:

                self.samples_rejected += 1

                LOGGER.warning(
                    "Payload on %s is missing '%s'",
                    self.config.telemetry_topic, key
                )

                return None

            try:

                sample[argument] = float(
                    payload[key]
                )

            except (TypeError, ValueError):

                self.samples_rejected += 1

                LOGGER.warning(
                    "Payload field '%s' is not a number: %r",
                    key, payload[key]
                )

                return None

        # The device clock is preferred, because a device that
        # restarts still has a monotonic sample counter, whereas
        # a local clock would jump and the tracker would reject
        # everything after the jump.
        if "timestamp" in payload:

            try:

                timestamp = float(payload["timestamp"])

            except (TypeError, ValueError):

                self.samples_rejected += 1

                LOGGER.warning(
                    "Payload 'timestamp' is not a number: %r",
                    payload["timestamp"]
                )

                return None

        else:

            # No timestamp means the producer cannot say how far
            # apart its samples are, and every duration and the
            # capacity integral depend on that. Guessing one
            # second would fabricate the model's most important
            # time-based input, so the sample is refused instead.
            self.samples_rejected += 1

            LOGGER.warning(
                "Payload on %s has no 'timestamp'; "
                "refusing to guess the sample interval",
                self.config.telemetry_topic
            )

            return None

        if payload.get("ambient_temperature") is not None:

            try:

                sample["ambient_temperature"] = float(
                    payload["ambient_temperature"]
                )

            except (TypeError, ValueError):

                # Not fatal. Ambient is one input among 21 and
                # the model imputes missing values, so a bad one
                # is dropped rather than failing the sample.
                LOGGER.warning(
                    "Ignoring unparseable "
                    "ambient_temperature: %r",
                    payload["ambient_temperature"]
                )

        with self._lock:

            before = self.service.tracker.cycle_index

            try:

                prediction = self.service.add_sample(
                    timestamp, **sample
                )

            except Exception:

                # The service already recorded the failure and
                # kept the previous reading, so the right thing
                # here is to report it and carry on.
                self.samples_accepted += 1

                LOGGER.exception(
                    "Prediction failed at t=%s; keeping the "
                    "previous reading",
                    timestamp
                )

                self._publish_status(force=True)

                return None

            self.samples_accepted += 1

            self.samples_out_of_order = (
                self.service.tracker.out_of_order_samples
            )

            closed = (
                self.service.tracker.cycle_index > before
            )

        if closed and prediction is not None:

            self.cycles_predicted += 1

            LOGGER.info(
                "Cycle %s closed, soh %s (%s, %.3f ms)",
                prediction.get("cycle"),
                prediction.get("soh"),
                prediction.get("status"),
                prediction.get(
                    "predict_duration_ms", 0.0
                )
            )

            # Publish the new reading straight away rather than
            # waiting for the next tick, so a subscriber sees a
            # real update at the moment it happens.
            self._publish_status(force=True)

            return prediction

        return None

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def _publish_status(self, force=False):
        """
        Publish the current latched reading.

        Skips when nothing meaningful has changed, unless forced.
        The per-second loop passes force=False so a still value
        is not rewritten, and a cycle close passes force=True so
        the new number goes out at once.
        """

        with self._lock:

            state = self.service.read()

        # published_at is added for the subscriber, but it is
        # deliberately kept out of the comparison below. It
        # changes every call, so including it would make every
        # payload look different and defeat the skip entirely.
        state = dict(state)
        state["cell_id"] = self.config.cell_id
        state["samples_accepted"] = self.samples_accepted
        state["samples_rejected"] = self.samples_rejected
        state["samples_out_of_order"] = (
            self.samples_out_of_order
        )

        if not force and state == self._last_published:

            return

        body = json.dumps(
            {**state, "published_at": time.time()},
            default=str
        )

        try:

            self.client.publish(
                self.config.soh_topic,
                body,
                qos=SOH_QOS,
                retain=True
            )

        except Exception:

            LOGGER.exception(
                "Failed to publish to %s",
                self.config.soh_topic
            )

            return

        self._last_published = state
        self.publishes += 1

    def _publish_loop(self):

        interval = self.config.publish_interval_s

        while not self._stop.wait(interval):

            try:

                self._publish_status()

            except Exception:

                # One bad publish must not end the loop, or the
                # device would go quiet with no way to notice.
                LOGGER.exception(
                    "Publish loop iteration failed"
                )

    def _start_publisher(self):

        if self._publisher is not None:

            return

        self._stop.clear()

        self._publisher = threading.Thread(
            target=self._publish_loop,
            name="soh-publisher",
            daemon=True
        )

        self._publisher.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self):
        """
        Connect, subscribe and begin publishing.

        Safe to call once. The model is already loaded by this
        point, because the service is built in __init__.
        """

        LOGGER.info(
            "Connecting to %s:%s as %s",
            self.config.host, self.config.port,
            self.config.client_id
        )

        self.client.connect(
            self.config.host, self.config.port
        )

        # loop_start runs the network thread and returns, so
        # start() does not block the caller.
        self.client.loop_start()

        self._start_publisher()

    def stop(self, flush=True):
        """
        Stop publishing and disconnect.
        """

        self._stop.set()

        publisher = self._publisher

        if publisher is not None:

            publisher.join(
                timeout=self.config.publish_interval_s * 4
            )

            self._publisher = None

        if flush:

            # The last discharge of a session has no positive
            # current after it, so it can only be closed by
            # telling the tracker the stream ended. Skipping this
            # throws away a full cycle's worth of work.
            with self._lock:

                tail = self.service.flush()

            if tail is not None:

                self.cycles_predicted += 1

                self._publish_status(force=True)

        try:

            # Clear the retained will, so a clean shutdown does
            # not leave the offline message in place of a real
            # reading.
            self.client.publish(
                self.config.soh_topic,
                payload=None,
                qos=SOH_QOS,
                retain=True
            )

            self.client.loop_stop()

            self.client.disconnect()

        except Exception:

            LOGGER.exception(
                "Error during MQTT shutdown"
            )

        self.connected = False

    def __enter__(self):

        self.start()

        return self

    def __exit__(self, exc_type, exc, tb):

        self.stop()

        return False

    def status(self):
        """
        Everything worth knowing, for a local health check.
        """

        with self._lock:

            state = self.service.read()

        return {
            "connected": self.connected,
            "telemetry_topic": self.config.telemetry_topic,
            "soh_topic": self.config.soh_topic,
            "publish_interval_s": (
                self.config.publish_interval_s
            ),
            "samples_accepted": self.samples_accepted,
            "samples_rejected": self.samples_rejected,
            "samples_out_of_order": (
                self.samples_out_of_order
            ),
            "cycles_predicted": self.cycles_predicted,
            "publishes": self.publishes,
            "state": state
        }


def main():
    """
    Run one client against a broker until interrupted.
    """

    import argparse

    from soh_service import check_model_available

    parser = argparse.ArgumentParser(
        description=(
            "Stream battery telemetry from MQTT and publish "
            "the latched SOH."
        )
    )

    parser.add_argument("--cell", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--root", default="bms")

    parser.add_argument(
        "--telemetry-topic", default=None
    )
    parser.add_argument("--soh-topic", default=None)

    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)

    parser.add_argument(
        "--interval", type=float,
        default=DEFAULT_PUBLISH_INTERVAL_S
    )

    parser.add_argument(
        "--log-level", default="INFO"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(
            logging, args.log_level.upper(), logging.INFO
        ),
        format="%(asctime)s %(levelname)s %(name)s: "
               "%(message)s"
    )

    # Fail before connecting, so the operator is told the
    # artifact is the problem rather than watching a service
    # that can never produce a reading.
    ok, message = check_model_available()

    if not ok:

        LOGGER.error("SOH model unavailable: %s", message)

        raise SystemExit(1)

    config = MqttConfig(
        cell_id=args.cell,
        host=args.host,
        port=args.port,
        root=args.root,
        telemetry_topic=args.telemetry_topic,
        soh_topic=args.soh_topic,
        username=args.username,
        password=args.password,
        publish_interval_s=args.interval
    )

    LOGGER.info(
        "Telemetry: %s -> SOH: %s",
        config.telemetry_topic, config.soh_topic
    )

    client = MqttSohClient(config)

    with client:

        LOGGER.info(
            "Running. Publishes %s every %.1fs. Ctrl-C to stop.",
            config.soh_topic, config.publish_interval_s
        )

        try:

            while True:

                time.sleep(1.0)

        except KeyboardInterrupt:

            LOGGER.info("Stopping")


if __name__ == "__main__":

    main()
