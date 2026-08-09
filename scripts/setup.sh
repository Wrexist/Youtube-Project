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
# prerequisites are Python 3.11+ and Node 20+. ffmpeg is used if it is on PATH,
# but imageio-ffmpeg ships one, so it is not something to install first.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

bold=$(tput bold 2>/dev/null || echo "")
dim=$(tput dim 2>/dev/null || echo "")
red=$(tput setaf 1 2>/dev/null || echo "")
green=$(tput setaf 2 2>/dev/null || echo "")
reset=$(tput sgr0 2>/dev/null || echo "")

# Kept in step with scripts/setup.ps1: a numbered heading at four columns and its
# detail at seven, so both platforms produce the same shape of transcript and a
# screenshot from either one is worth the same in an issue. The counter earns its
# place on the three steps that print nothing for minutes at a time — "3/8" is the
# difference between waiting and wondering whether it has hung.
STEP_NUMBER=0
STEP_TOTAL=8

step() {
  STEP_NUMBER=$((STEP_NUMBER + 1))
  echo
  echo "  ${dim}${STEP_NUMBER}/${STEP_TOTAL}${reset} ${bold}$1${reset}"
}
note() { echo "       ${dim}$1${reset}"; }
# A step that finished, with what it cost. Only where the wait was long enough
# that finishing is itself news. $2 is elapsed seconds, and is optional.
good() {
  if [ -n "${2:-}" ]; then
    echo "       ${green}ok${reset} ${dim}$1  $(elapsed "$2")${reset}"
  else
    echo "       ${green}ok${reset} ${dim}$1${reset}"
  fi
}
elapsed() {
  if [ "$1" -lt 60 ]; then echo "${1}s"; else printf '%dm %02ds\n' $(($1 / 60)) $(($1 % 60)); fi
}
die()  { echo; echo "  ${red}✗  $1${reset}" >&2; exit 1; }

echo
echo "  ${bold}Studio${reset}"
echo "  ${dim}Setting up. A few minutes, mostly downloads — you can leave it.${reset}"

# Windows puts the interpreter somewhere else; everything below is otherwise
# identical, so resolve it once rather than branching throughout.
VENV_BIN="apps/engine/.venv/bin"
[ -d "apps/engine/.venv/Scripts" ] && VENV_BIN="apps/engine/.venv/Scripts"

# ── prerequisites ───────────────────────────────────────────────────────────

step "Checking prerequisites"

# Name the command that installs it, not just the thing that is missing.
# "Install Python 3.11 or newer" is a web search and a decision; `brew install
# python@3.12` is a copy and a paste. Which one is right depends on the machine,
# so it is worked out here rather than left to the reader.
howto() {
  local what="$1"
  if [ "$(uname -s)" = "Darwin" ]; then
    if command -v brew >/dev/null; then
      case "$what" in
        python) echo "brew install python@3.12" ;;
        node)   echo "brew install node" ;;
      esac
    else
      echo "install Homebrew from https://brew.sh first, then re-run this"
    fi
  elif command -v apt >/dev/null; then
    case "$what" in
      python) echo "sudo apt update && sudo apt install -y python3 python3-venv" ;;
      node)   echo "sudo apt update && sudo apt install -y nodejs npm   # or: https://nodejs.org" ;;
    esac
  elif command -v dnf >/dev/null; then
    case "$what" in
      python) echo "sudo dnf install -y python3" ;;
      node)   echo "sudo dnf install -y nodejs" ;;
    esac
  elif command -v pacman >/dev/null; then
    case "$what" in
      python) echo "sudo pacman -S python" ;;
      node)   echo "sudo pacman -S nodejs npm" ;;
    esac
  else
    case "$what" in
      python) echo "https://www.python.org/downloads/" ;;
      node)   echo "https://nodejs.org" ;;
    esac
  fi
}

missing() {
  echo "${red}✗ $1${reset}" >&2
  echo >&2
  echo "  Install it with:" >&2
  echo >&2
  echo "    ${bold}$(howto "$2")${reset}" >&2
  echo >&2
  echo "  Then run this script again." >&2
  exit 1
}

command -v python3 >/dev/null || missing "Python 3.11+ is required to run the render engine." python
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 11) else 0)')
[ "$PY_OK" = "1" ] || missing "Python $(python3 -V | cut -d' ' -f2) is too old — 3.11+ is required." python
note "python3 $(python3 -V | cut -d' ' -f2)"

command -v node >/dev/null || missing "Node.js 20+ is required to run the web app." node
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 20 ] || missing "Node $(node -v) is too old — 20+ is required." node
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

note "installing Python dependencies (the slow one)"
started=$SECONDS
"$ROOT/$VENV_BIN/python" -m pip install --quiet --upgrade pip
(cd apps/engine && "$ROOT/$VENV_BIN/python" -m pip install --quiet -e ".[dev]")
good "Python dependencies" $((SECONDS - started))

# ── web ─────────────────────────────────────────────────────────────────────

step "Setting up the web app"
note "installing npm workspaces"
# `--loglevel=error`, not `--silent` — the same fix setup.ps1 carries, for the
# same reason. Silent suppresses npm's own error reporting along with its
# progress, so a failed install here printed nothing at all before `set -e` took
# the script down. Checked: a 404 on a missing package produces no output under
# --silent and the full `npm error 404` block under --loglevel=error.
started=$SECONDS
npm install --loglevel=error --no-fund --no-audit
good "npm workspaces" $((SECONDS - started))

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
  good "web dependencies rebuilt for this platform"
else
  good "platform binaries"
fi

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
print('       ' + asyncio.run(db.ensure_schema()))
")

# ── verify ──────────────────────────────────────────────────────────────────

step "Running the test suite"
# Captured, not piped straight through. `... | tail -3` makes the pipeline's exit
# status tail's, which is always 0 — so a failing suite scrolled three lines past
# and setup went on to print "Setup complete", the one thing this step exists to
# stop. (The same bug was in setup.ps1.)
started=$SECONDS
set +e
TEST_OUTPUT=$(cd apps/engine && STUDIO_PERSIST=false "$ROOT/$VENV_BIN/python" -m pytest -q 2>&1)
TESTS=$?
set -e
if [ $TESTS -ne 0 ]; then
  echo "$TEST_OUTPUT" | tail -3 | sed 's/^/       /'
  echo
  echo "       ${bold}The test suite failed.${reset} Full output:"
  echo "$TEST_OUTPUT" | sed 's/^/       /'
  echo
  die "the engine is not working on this machine — please open an issue with the output above"
fi
# pytest's own summary line, which already reads as a sentence: "868 passed, 2
# skipped in 144.10s". Preferred over a count of our own, because a suite that
# passed with skips should say so.
good "$(echo "$TEST_OUTPUT" | grep -E 'passed|no tests ran' | tail -1)" $((SECONDS - started))

step "Adding a launcher"
# Never fatal: a missing shortcut is a small inconvenience, and a setup script
# that aborts over one having already installed everything is a much bigger one.
# Re-indented into this script's gutter — install-shortcut.mjs prints its own
# two spaces, which is right when it is run on its own and wrong here.
node scripts/install-shortcut.mjs 2>&1 | sed 's/^ *//; s/^/       /' || true

step "Checking what is still missing"
set +e
"$ROOT/$VENV_BIN/python" apps/engine/scripts/doctor.py
DOCTOR=$?
set -e

# One rule below the checklist, so "what is left to do" is visually separate from
# "here is the thing you came for". Without it the doctor's list and the next step
# run together into one wall at the exact moment someone stops reading.
echo
echo "  ${dim}------------------------------------------------------------------${reset}"
echo
if [ $DOCTOR -eq 0 ]; then
  echo "  ${green}Setup complete.${reset} Nothing is missing."
else
  echo "  ${green}Setup complete.${reset} The items marked ✗ above still need an API key."
  echo "  ${dim}You can add them on the Setup screen, or see SETUP.md.${reset}"
fi
echo
# Setup used to end here, having installed everything and never said how to start
# it. The next command is the whole point of having run this one.
echo "  ${bold}Start Studio${reset}"
if [ "$(uname -s)" = "Darwin" ]; then
  echo "    Open ${bold}Studio${reset} from your Applications folder."
else
  echo "    Double-click the ${bold}Studio${reset} launcher on your Desktop."
fi
echo "    ${dim}(or run ${bold}npm start${reset}${dim} here — same thing)${reset}"
echo
if [ $DOCTOR -ne 0 ]; then
  echo "    ${dim}Your browser opens by itself, on the setup screen. Paste your keys${reset}"
  echo "    ${dim}in there — it says what each one unlocks and links to where to get it.${reset}"
else
  echo "    ${dim}Your browser opens by itself. Type a topic and press Generate.${reset}"
fi
echo
