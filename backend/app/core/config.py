"""Application settings.

All values are overridable through environment variables prefixed with
``BMS_`` (and an optional ``.env`` file). The defaults match the local
docker-compose stack so the backend boots without extra configuration.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BMS_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "BMS Telemetry & SoH Backend"
    app_version: str = "0.1.0"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- Database ---------------------------------------------------------
    # Async SQLAlchemy URL. The Alembic migration environment reads this
    # same value, so the app and migrations can never drift apart.
    # Port 5433 is the host publish of the compose Postgres (docker-compose
    # maps 5433:5432 to avoid colliding with a host Postgres on 5432).
    # Inside the compose network the backend overrides this with
    # postgresql+asyncpg://bms:bms@postgres:5432/bms.
    database_url: str = "postgresql+asyncpg://bms:bms@localhost:5433/bms"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_s: int = 1800
    # Tests set this to avoid reusing a pooled connection across the
    # per-test event loops that pytest-asyncio creates.
    db_use_nullpool: bool = False

    # --- MQTT -------------------------------------------------------------
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str = "bms-backend"
    mqtt_topic_prefix: str = "bms"
    mqtt_qos: int = 1
    mqtt_keepalive: int = 30
    mqtt_reconnect_min_s: float = 1.0
    mqtt_reconnect_max_s: float = 60.0

    # --- Validation ranges (FR-04) ---------------------------------------
    # Configurable bounds for the target chemistry / hardware. Values are
    # deliberately wide; tighten per deployment. They gate "impossible"
    # measurements, not unusual-but-real ones.
    cell_voltage_min_v: float = 1.5
    cell_voltage_max_v: float = 4.5
    pack_voltage_min_v: float = 5.0
    pack_voltage_max_v: float = 60.0
    current_min_a: float = -200.0
    current_max_a: float = 200.0
    temperature_min_c: float = -40.0
    temperature_max_c: float = 100.0
    max_schema_version: int = 1

    # "Unusual but valid" thresholds. A sample beyond these is still stored
    # but flagged, so operators can triage without losing data (FR-04).
    cell_imbalance_warn_v: float = 0.10

    # --- Ingestion pipeline (FR-02) --------------------------------------
    # The MQTT consumer must never wait on Postgres. Validated samples are
    # handed to a bounded in-memory queue and written in batches by a
    # dedicated writer task. The bound makes back-pressure explicit: when the
    # queue is full the sample is dropped with a counted, logged reason
    # rather than growing memory without limit.
    ingest_queue_max: int = 10000
    ingest_batch_size: int = 500
    ingest_flush_interval_s: float = 0.5

    # --- Monitoring loop and alert rules (Phase 5) -----------------------
    # The loop is one asyncio task in the app lifespan: it aggregates any
    # device that gained samples since the last tick, then evaluates the
    # alert rules against the persisted state. Disabled by default in tests
    # so the background task never races a per-test event loop.
    monitor_enabled: bool = True
    monitor_interval_s: float = 60.0
    auto_aggregate: bool = True
    # A device with no sample for this long is flagged "device_stale"
    # (offline), matching the offline exporter's staleness notion.
    device_stale_after_s: float = 900.0

    # Rule thresholds. The SoH retirement bound is the value the offline
    # alert rules use (scripts/make_alert_rules.py: RETIRE_SOH_PCT); the
    # temperature bound is the maximum peak temperature the SoH model trained
    # on (data/processed/nasa_ml_dataset_clean.csv, column temperature_max),
    # so "out of range" means "outside the model's operational range".
    alert_soh_retire_pct: float = 80.0
    alert_temperature_max_c: float = 63.0242156686

    # --- Security (Phase 6) ----------------------------------------------
    # Optional admin key guarding device registration and credential
    # rotation. Unset (the default) keeps those endpoints open, which is what
    # local development and the compose stack expect; set it before exposing
    # the API beyond a trusted network.
    admin_api_key: str | None = None
    # Reject device secrets shorter than this at registration time. Long
    # enough to force a random value, short enough for a QR-code sticker.
    min_credential_length: int = 8

    # --- RUL / EOL (Phase 7) ---------------------------------------------
    # How many of a device's newest predicted SoH values feed the
    # reference-trajectory match. Matches SMOOTH_WINDOW in
    # src/rul_trajectory.py, so serving and evaluation use the same window.
    rul_history_window: int = 10
    # Reference SoH-versus-discharge curves exported by src/train_rul.py.
    # Resolved relative to the repo root, like the SoH model artifacts.
    rul_reference_path: str | None = None

    # --- Live stream (Phase 8) -------------------------------------------
    # The SSE endpoint and the in-process event bus behind it. On by
    # default; a deployment that only polls the REST API can turn it off to
    # avoid holding subscriber connections open.
    stream_enabled: bool = True
    # Per-subscriber buffer for live events. A slow reader drops its oldest
    # events rather than applying back-pressure to ingestion.
    stream_queue_max: int = 256

    @property
    def mqtt_telemetry_topic(self) -> str:
        """Wildcard subscription for all device telemetry (FR-02)."""
        return f"{self.mqtt_topic_prefix}/+/telemetry"


@lru_cache
def get_settings() -> Settings:
    return Settings()
