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

    @property
    def mqtt_telemetry_topic(self) -> str:
        """Wildcard subscription for all device telemetry (FR-02)."""
        return f"{self.mqtt_topic_prefix}/+/telemetry"


@lru_cache
def get_settings() -> Settings:
    return Settings()
