"""Application settings.

All values are overridable through environment variables prefixed with
``BMS_`` (and an optional ``.env`` file). The defaults match the local
docker-compose stack so the backend boots without extra configuration.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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

    # --- CORS -------------------------------------------------------------
    # Browser origins allowed to call the API. The default is the common
    # local frontend dev servers; set ``BMS_CORS_ALLOW_ORIGINS`` (comma
    # separated, or "*" for any) before exposing the API beyond localhost.
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"
    cors_allow_credentials: bool = False

    def cors_origins(self) -> list[str]:
        """Parse the comma-separated origin list (``*`` passes through)."""
        origins = [part.strip() for part in self.cors_allow_origins.split(",")]
        return [origin for origin in origins if origin]

    # --- Database ---------------------------------------------------------
    # Async SQLAlchemy URL. The Alembic migration environment reads this
    # same value, so the app and migrations can never drift apart.
    # Port 5433 is the host publish of the compose Postgres (docker-compose
    # maps 5433:5432 to avoid colliding with a host Postgres on 5432).
    # Inside the compose network the backend overrides this with
    # postgresql+asyncpg://bms:bms@postgres:5432/bms.
    database_url: str = "postgresql+asyncpg://bms:bms@localhost:5433/bms"
    db_echo: bool = False
    db_pool_size: int = Field(default=10, gt=0)
    db_max_overflow: int = Field(default=20, ge=0)
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
    # The numeric settings below carry explicit lower bounds: several of
    # them feed ``asyncio.Queue(maxsize=...)``, where 0 silently means
    # *unbounded* — the opposite of the back-pressure documented above —
    # and a 0 monitor interval would busy-loop against the database.
    # Misconfiguration must fail at startup, not degrade silently.
    ingest_queue_max: int = Field(default=10000, gt=0)
    ingest_batch_size: int = Field(default=500, gt=0)
    ingest_flush_interval_s: float = Field(default=0.5, gt=0)
    # How far into the future a sample's ``measured_at`` may sit before it
    # is rejected as out of range. Without a bound, one far-future sample
    # pins the device's monotonic ``last_seen_at`` in the future forever
    # and the staleness rule can never fire for that device. Seven days is
    # generous for clock skew; backfill of *past* samples stays unbounded.
    telemetry_max_future_skew_s: float = Field(default=7 * 86400.0, gt=0)
    # Largest HTTP telemetry body accepted. A telemetry sample is a
    # handful of numbers; anything near a megabyte is a bug or an
    # attack, and without a bound the body is fully buffered and
    # parsed before any schema limit gets a say.
    telemetry_max_body_bytes: int = Field(default=1_048_576, gt=0)

    # --- Monitoring loop and alert rules (Phase 5) -----------------------
    # The loop is one asyncio task in the app lifespan: it aggregates any
    # device that gained samples since the last tick, then evaluates the
    # alert rules against the persisted state. Disabled by default in tests
    # so the background task never races a per-test event loop.
    monitor_enabled: bool = True
    monitor_interval_s: float = Field(default=60.0, gt=0)
    auto_aggregate: bool = True
    # A device with no sample for this long is flagged "device_stale"
    # (offline), matching the offline exporter's staleness notion.
    device_stale_after_s: float = Field(default=900.0, gt=0)

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
    # Optional operator key guarding every state-changing call the dashboard
    # makes (alert/anomaly acknowledge, resolve, actions, evaluate, cycle
    # re-aggregation and report generation). Device ingestion keeps its own
    # per-device credential. Unset keeps local development open; in a
    # production environment the app refuses to start without both keys.
    operator_api_key: str | None = None
    # Optional viewer key guarding every read (REST and the SSE stream) except
    # the bare ``/health`` liveness probe. The operator and admin keys also
    # grant read access. Unset keeps reads open for local development.
    viewer_api_key: str | None = None
    # Reject device secrets shorter than this at registration time. Long
    # enough to force a random value, short enough for a QR-code sticker.
    min_credential_length: int = Field(default=8, ge=1)

    # --- Report queue ------------------------------------------------------
    # Background builds for ``POST /reports`` with ``queued: true``.
    report_queue_max_pending: int = Field(default=20, gt=0)
    report_worker_concurrency: int = Field(default=1, gt=0)
    # A "running" report whose heartbeat is older than this is treated as
    # orphaned by a crashed process and re-queued. Heartbeats keep long builds
    # safe, so this bounds detection latency, not build length.
    report_running_stale_seconds: int = Field(default=900, gt=0)
    report_heartbeat_seconds: int = Field(default=60, gt=0)

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
    stream_queue_max: int = Field(default=256, gt=0)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}

    def access_control_problems(self) -> list[str]:
        """Missing write-protection keys that must block a production start."""
        if not self.is_production:
            return []
        problems = []
        if not self.admin_api_key:
            problems.append("BMS_ADMIN_API_KEY is not set")
        if not self.operator_api_key:
            problems.append("BMS_OPERATOR_API_KEY is not set")
        if not self.viewer_api_key:
            problems.append("BMS_VIEWER_API_KEY is not set")
        # Browsers reject wildcard origins combined with credentials, so
        # this combination can only ever be a misconfiguration — refuse it
        # at startup instead of relying on that browser behaviour.
        if self.cors_allow_credentials and "*" in self.cors_origins():
            problems.append(
                "BMS_CORS_ALLOW_ORIGINS must not contain '*' when "
                "BMS_CORS_ALLOW_CREDENTIALS is true"
            )
        return problems

    @property
    def mqtt_telemetry_topic(self) -> str:
        """Wildcard subscription for all device telemetry (FR-02)."""
        return f"{self.mqtt_topic_prefix}/+/telemetry"


@lru_cache
def get_settings() -> Settings:
    return Settings()
