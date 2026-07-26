#!/usr/bin/env bash
#
# One command to get from a fresh clone to a running app.
#
#   ./scripts/setup.sh
#
# Idempotent: safe to re-run after pulling, and it will only redo what changed.
# It sets up everything that does not need a human. What is left afterwards is
# API keys, and the doctor at the end tells you exactly which.
#
# No Docker required. The engine defaults to SQLite, so the only hard
# prerequisites are Python 3.11+, Node 20+, and ffmpeg.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

bold=$(tput bold 2>/dev/null || echo "")
dim=$(tput dim 2>/dev/null || echo "")
red=$(tput setaf 1 2>/dev/null || echo "")
green=$(tput setaf 2 2>/dev/null || echo "")
reset=$(tput sgr0 2>/dev/null || echo "")

step() { echo; echo "${bold}$1${reset}"; }
note() { echo "  ${dim}$1${reset}"; }
die()  { echo "${red}✗ $1${reset}" >&2; exit 1; }

# Windows puts the interpreter somewhere else; everything below is otherwise
# identical, so resolve it once rather than branching throughout.
VENV_BIN="apps/engine/.venv/bin"
[ -d "apps/engine/.venv/Scripts" ] && VENV_BIN="apps/engine/.venv/Scripts"

# ── prerequisites ───────────────────────────────────────────────────────────

step "Checking prerequisites"

command -v python3 >/dev/null || die "python3 not found. Install Python 3.11 or newer."
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 11) else 0)')
[ "$PY_OK" = "1" ] || die "Python $(python3 -V | cut -d' ' -f2) is too old — 3.11+ is required."
note "python3 $(python3 -V | cut -d' ' -f2)"

command -v node >/dev/null || die "node not found. Install Node 20 or newer."
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 20 ] || die "Node $(node -v) is too old — 20+ is required."
note "node $(node -v)"

if command -v ffmpeg >/dev/null; then
  note "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
else
  # imageio-ffmpeg ships a binary, so this is a warning rather than fatal.
  note "ffmpeg not on PATH — will fall back to the one bundled with imageio-ffmpeg"
fi

# ── engine ──────────────────────────────────────────────────────────────────

step "Setting up the engine"

if [ ! -d "apps/engine/.venv" ]; then
  note "creating apps/engine/.venv"
  (cd apps/engine && python3 -m venv .venv)
  VENV_BIN="apps/engine/.venv/bin"
  [ -d "apps/engine/.venv/Scripts" ] && VENV_BIN="apps/engine/.venv/Scripts"
else
  note "apps/engine/.venv exists"
fi

note "installing Python dependencies (this is the slow part)"
"$ROOT/$VENV_BIN/python" -m pip install --quiet --upgrade pip
(cd apps/engine && "$ROOT/$VENV_BIN/python" -m pip install --quiet -e ".[dev]")

# ── web ─────────────────────────────────────────────────────────────────────

step "Setting up the web app"
note "installing npm workspaces"
npm install --silent

# ── config ──────────────────────────────────────────────────────────────────

step "Configuration"

if [ -f ".env" ]; then
  note ".env already exists — leaving it alone"
else
  cp .env.example .env
  note "created .env from .env.example"
fi

mkdir -p storage/bgm storage/fonts
note "storage/ ready"

# The schema is created on first boot too, but doing it here means the very
# first request is not the one that pays for it.
step "Creating the database schema"
(cd apps/engine && "$ROOT/$VENV_BIN/python" -c "
import asyncio, os
os.environ.setdefault('STUDIO_PERSIST', 'true')
from engine import db
print('  ' + asyncio.run(db.ensure_schema()))
")

# ── verify ──────────────────────────────────────────────────────────────────

step "Running the test suite"
(cd apps/engine && STUDIO_PERSIST=false "$ROOT/$VENV_BIN/python" -m pytest -q 2>&1 | tail -3)

step "Checking what is still missing"
set +e
"$ROOT/$VENV_BIN/python" apps/engine/scripts/doctor.py
DOCTOR=$?
set -e

echo
if [ $DOCTOR -eq 0 ]; then
  echo "${green}${bold}Setup complete and nothing is missing.${reset}"
else
  echo "${bold}Setup complete.${reset} The items marked ✗ above need an API key —"
  echo "see ${bold}SETUP.md${reset} for where to get each one. Nothing else needs doing."
fi
echo
