#!/usr/bin/env bash
#
# verify.sh — the merge gate: reproducible end-to-end verification.
#
# Clones the committed repository into a temporary directory (it does
# NOT copy the working tree, so only committed state is verified),
# downloads and checksums the NASA dataset, runs the pipeline, runs
# the test suite, checks that every number in README.md, CHANGES.md,
# docs/RESULTS.md, docs/SETUP.md and docs/MODEL_CARD.md is a
# placeholder rendered from data/processed/report_numbers.json, and
# checks that those documents are in sync with the generator.
#
# It also runs the parts of the gate that previously lived only in CI:
#   * ruff over the whole tree,
#   * the backend suite against a throwaway Postgres container,
#   * frontend install, unit tests, typecheck and production build.
#
# Any failing step exits non-zero. A green run is required before
# merging (see CONTRIBUTING.md); AI-generated pull requests follow the
# same rule.
#
# Usage:
#   bash scripts/verify.sh
#
# Prerequisites for the full gate: Python 3.12, Docker (for the
# throwaway Postgres), Node.js 20+ and npm.
#
# Environment:
#   PYTHON         interpreter to use (default: python3). It must have
#                  the project dependencies installed. The other
#                  components are installed into temporary virtualenvs.
#   PDM_REPO       repository to clone (default: this checkout's own
#                  path, so the committed HEAD is verified). Set to a
#                  URL to verify a remote instead.
#   RUFF_VERSION   ruff pin for the lint stage (default: 0.17.2, the
#                  version CI uses).
#   PG_IMAGE       Postgres image for the backend stage (default:
#                  postgres:16-alpine, the image CI uses).
#   VERIFY_FULL    set to 0 to skip the ruff/backend/frontend stages.
#                  This is for fast local iteration only: a skip run is
#                  NOT a merge-gate pass and must not be cited as one.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(dirname "$HERE")"
PYTHON="${PYTHON:-python3}"
REPO="${PDM_REPO:-$SOURCE_ROOT}"
RUFF_VERSION="${RUFF_VERSION:-0.17.2}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
VERIFY_FULL="${VERIFY_FULL:-1}"

WORK="$(mktemp -d)"
PG_CONTAINER=""
cleanup() {
    if [ -n "$PG_CONTAINER" ]; then
        docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
    echo "    ERROR: $*" >&2
    exit 1
}

echo "============================================================"
echo "verify.sh"
echo "  repository : $REPO"
echo "  python     : $($PYTHON --version 2>&1)"
echo "  workdir    : $WORK"
if [ "$VERIFY_FULL" = "1" ]; then
    echo "  gate       : full (ruff + backend + frontend)"
else
    echo "  gate       : PARTIAL (VERIFY_FULL=0) — NOT a merge-gate run"
fi
echo "============================================================"

echo
echo ">>> cloning committed repository"
git clone --quiet "$REPO" "$WORK/clone"
cd "$WORK/clone"
echo "    HEAD: $(git rev-parse --short HEAD)"

echo
echo ">>> downloading and checksumming the NASA dataset"
bash scripts/download_dataset.sh

echo
echo ">>> running the pipeline (skip training)"
"$PYTHON" run_pipeline.py --skip-train

echo
echo ">>> checking the pipeline left the working tree clean"
if [ -z "$(git status --porcelain)" ]; then
    echo "    git status is clean after the pipeline"
else
    echo "    ERROR: the pipeline changed or created tracked files:"
    git --no-pager status --porcelain
    exit 1
fi

echo
echo ">>> running the test suite"
"$PYTHON" -m pytest -q

echo
echo ">>> running the backend partial-window estimator tests"
# --noconftest: the backend's app-level conftest imports the full
# FastAPI app (and its MQTT dependency), which this pure-ML test does
# not need and this interpreter does not install.
(
    cd backend
    "$PYTHON" -m pytest -q --noconftest tests/test_window_estimator.py
)

echo
echo ">>> checking numbers in README.md, CHANGES.md and docs/"
"$PYTHON" scripts/check_numbers.py

echo
echo ">>> checking generated documents are in sync"
DOCS=(README.md CHANGES.md docs/RESULTS.md docs/SETUP.md docs/MODEL_CARD.md)
if git diff --quiet -- "${DOCS[@]}" data/processed/report_numbers.json; then
    echo "    ${DOCS[*]} and report_numbers.json match the generator output"
else
    echo "    ERROR: committed documents differ from generated output:"
    git --no-pager diff --stat -- "${DOCS[@]}" data/processed/report_numbers.json
    exit 1
fi

if [ "$VERIFY_FULL" = "1" ]; then
    echo
    echo ">>> ruff"
    RUFF_VENV="$WORK/ruff-venv"
    "$PYTHON" -m venv "$RUFF_VENV" \
        || fail "could not create a virtualenv for ruff (install python3-venv)"
    "$RUFF_VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
        "ruff==$RUFF_VERSION" \
        || fail "could not install ruff==$RUFF_VERSION"
    "$RUFF_VENV/bin/ruff" check .

    echo
    echo ">>> backend tests (throwaway Postgres container)"
    command -v docker >/dev/null 2>&1 \
        || fail "docker is required for the backend stage (throwaway Postgres)"
    BACKEND_VENV="$WORK/backend-venv"
    "$PYTHON" -m venv "$BACKEND_VENV" \
        || fail "could not create a virtualenv for the backend (install python3-venv)"
    "$BACKEND_VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
        -r backend/requirements.txt \
        || fail "could not install backend/requirements.txt"
    PG_CONTAINER="pdm-verify-pg-$$"
    docker run -d --rm --name "$PG_CONTAINER" \
        -e POSTGRES_USER=bms \
        -e POSTGRES_PASSWORD=bms \
        -e POSTGRES_DB=bms \
        -p 127.0.0.1::5432 \
        "$PG_IMAGE" >/dev/null \
        || fail "could not start the throwaway Postgres container ($PG_IMAGE)"
    PG_PORT="$(docker port "$PG_CONTAINER" 5432/tcp | head -n1 | sed 's/.*://')"
    [ -n "$PG_PORT" ] || fail "could not resolve the throwaway Postgres port"
    for _ in $(seq 1 60); do
        if docker exec "$PG_CONTAINER" pg_isready -U bms -d bms >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    docker exec "$PG_CONTAINER" pg_isready -U bms -d bms >/dev/null 2>&1 \
        || fail "throwaway Postgres did not become ready"
    export BMS_DATABASE_URL="postgresql+asyncpg://bms:bms@127.0.0.1:${PG_PORT}/bms"
    export BMS_REQUIRE_DB=true
    export BMS_MQTT_ENABLED=false
    export BMS_MONITOR_ENABLED=false
    (
        cd backend
        "$BACKEND_VENV/bin/python" -m alembic upgrade head
        "$BACKEND_VENV/bin/python" -m alembic check
        "$BACKEND_VENV/bin/python" -m pytest -ra
    )
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    PG_CONTAINER=""

    echo
    echo ">>> frontend unit tests, typecheck and build"
    command -v npm >/dev/null 2>&1 \
        || fail "npm is required for the frontend stage (install Node.js 20+)"
    (
        cd frontend
        npm ci --no-audit --no-fund
        npm test
        npm run typecheck
        npm run build
    )
else
    echo
    echo ">>> SKIPPED ruff, backend and frontend stages (VERIFY_FULL=0)"
    echo "    This is a partial run: it is NOT a merge-gate pass."
fi

echo
echo "============================================================"
echo "verify.sh: ALL CHECKS PASSED"
echo "============================================================"
