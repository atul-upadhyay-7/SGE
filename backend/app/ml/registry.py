"""Model-registry metadata for the partial-window SOH model (FR-07).

The estimator (:mod:`app.ml.estimator`) scores one discharge; this module
describes *which* artifact produced that score, so ``model_registry`` and
every ``soh_predictions`` row can name a concrete, reproducible version.

Nothing here re-declares a feature name, target or dataset: the metadata is
read from the bundle the estimator loads (``models/soh_window_all.ubj``) and
its generated sidecar (``models/soh_window_all.json``), which is the same
source the ML contract document is rendered from. The backend therefore
cannot drift from the offline trainer.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"

if str(_SRC) not in sys.path:

    sys.path.insert(0, str(_SRC))

from app.db.models import ModelRegistry  # noqa: E402
from app.ml.estimator import DEFAULT_MODEL_PATH  # noqa: E402

# The registry identity of the served model. ``MODEL_NAME`` matches the
# artifact stem; the version is derived from the artifact's content so a
# retrained model is a new version with no code change.
MODEL_NAME = "soh_window_all"

# Bumped only when the meaning of an input changes (names, units, band),
# which is what makes historical predictions comparable.
FEATURE_SCHEMA_VERSION = "window-v1"

_ARTIFACT_PATH = DEFAULT_MODEL_PATH
_METADATA_PATH = DEFAULT_MODEL_PATH.with_suffix(".json")

_DATASET_VERSION_MAX = 64


def artifact_path() -> str:
    """Absolute path of the served artifact, as recorded in the registry."""
    return str(_ARTIFACT_PATH)


_VERSION_CACHE: tuple[int, int, str] | None = None


def model_version() -> str:
    """A reproducible version tag: the first 12 hex of the artifact hash.

    Cached on (mtime, size): this is called on every cycle aggregation,
    and re-reading + re-hashing the whole artifact each time put
    megabytes of synchronous I/O on the event loop for a value that only
    changes when the artifact is replaced.
    """
    global _VERSION_CACHE
    if not _ARTIFACT_PATH.is_file():
        raise FileNotFoundError(
            f"partial-window SOH model not found at {_ARTIFACT_PATH}. "
            "Train one with:  python src/train_window_soh.py"
        )
    stat = _ARTIFACT_PATH.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    if _VERSION_CACHE is not None and _VERSION_CACHE[:2] == key:
        return _VERSION_CACHE[2]
    digest = hashlib.sha256(_ARTIFACT_PATH.read_bytes()).hexdigest()
    _VERSION_CACHE = (*key, digest[:12])
    return _VERSION_CACHE[2]


def artifact_metadata() -> dict:
    """The generated sidecar (features, target, cells, library versions)."""
    if not _METADATA_PATH.is_file():
        raise FileNotFoundError(
            f"model metadata not found at {_METADATA_PATH}. "
            "Regenerate it with:  python src/train_window_soh.py"
        )
    return json.loads(_METADATA_PATH.read_text(encoding="utf-8"))


def registry_record() -> dict:
    """The row :func:`ensure_registered` writes for the served model."""
    metadata = artifact_metadata()
    trained_on = ",".join(metadata.get("trained_on", []))
    return {
        "model_name": MODEL_NAME,
        "model_version": model_version(),
        "artifact_path": artifact_path(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_dataset_version": trained_on[:_DATASET_VERSION_MAX] or None,
        "metrics": {"library_versions": metadata.get("library_versions", {})},
        "is_active": True,
    }


async def ensure_registered(session: AsyncSession) -> str:
    """Register the served model if absent, keyed on name + version.

    Idempotent: a re-run leaves the existing row untouched, so callers can
    invoke it on every aggregation without creating duplicates. Returns the
    model version that was ensured.
    """
    record = registry_record()
    await session.execute(
        pg_insert(ModelRegistry)
        .values(**record)
        .on_conflict_do_nothing(
            index_elements=["model_name", "model_version"]
        )
    )
    return record["model_version"]
