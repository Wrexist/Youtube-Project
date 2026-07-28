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

# Tailwind v4 compiles CSS through native binaries, and npm records an optional
# dependency only for the platform that generated the lockfile. A node_modules
# left over from before the root package.json pinned every variant is still
# broken, and the symptom is a 500 on the first page load naming a .node file
# nobody recognises. Catch it here, where it can be fixed silently.
if ! node scripts/check-web-toolchain.mjs >/dev/null 2>&1; then
  note "web dependencies are wrong for this platform — reinstalling from scratch"
  # Every one of them, not just the root. A clean install here hoists everything
  # and leaves no workspace node_modules; one that survives shadows the root copy
  # for anything inside that workspace, and deleting only the root leaves it in
  # place. That is how a machine ran Next 16 with a Next 10-era tree underneath it.
  node scripts/reinstall.mjs || die "web dependencies are still wrong"
fi
note "web dependencies OK"

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
# Captured, not piped straight through. `... | tail -3` makes the pipeline's exit
# status tail's, which is always 0 — so a failing suite scrolled three lines past
# and setup went on to print "Setup complete", the one thing this step exists to
# stop. (The same bug was in setup.ps1.)
set +e
TEST_OUTPUT=$(cd apps/engine && STUDIO_PERSIST=false "$ROOT/$VENV_BIN/python" -m pytest -q 2>&1)
TESTS=$?
set -e
echo "$TEST_OUTPUT" | tail -3 | sed 's/^/  /'
if [ $TESTS -ne 0 ]; then
  echo
  echo "${bold}The test suite failed.${reset} Full output:"
  echo "$TEST_OUTPUT" | sed 's/^/  /'
  echo
  echo "The engine is not working on this machine — please open an issue with the above."
  exit 1
fi

step "Checking what is still missing"
set +e
"$ROOT/$VENV_BIN/python" apps/engine/scripts/doctor.py
DOCTOR=$?
set -e

echo
if [ $DOCTOR -eq 0 ]; then
  echo "${green}${bold}Setup complete and nothing is missing.${reset}"
else
  echo "${bold}Setup complete.${reset} The items marked ✗ above need an API key."
fi
echo
# Setup used to end here, having installed everything and never said how to start
# it. The next command is the whole point of having run this one.
echo "${bold}Next:${reset}"
echo "  ${bold}npm start${reset}"
echo
if [ $DOCTOR -ne 0 ]; then
  echo "  then open ${bold}http://localhost:3000/setup${reset} and paste your keys in."
  echo "  The screen says what each one unlocks and links to where to get it."
else
  echo "  then open ${bold}http://localhost:3000${reset} and type a topic."
fi
echo
