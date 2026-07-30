# One command to get from a fresh clone to a running app, on Windows.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
#
# The `-ExecutionPolicy Bypass` is not optional advice: Windows blocks unsigned
# scripts by default, and without it this fails with "running scripts is disabled
# on this system" before it does anything.
#
# Idempotent: safe to re-run after pulling, and it only redoes what changed. It
# does everything that does not need a human; what is left is API keys, and the
# doctor at the end names exactly which.
#
# No Docker required. The engine defaults to SQLite, so the only hard
# prerequisites are Python 3.11+ and Node 20+.

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

function Step($text) { Write-Host ""; Write-Host $text -ForegroundColor White }
function Note($text) { Write-Host "  $text" -ForegroundColor DarkGray }
function Die($text) { Write-Host "X $text" -ForegroundColor Red; exit 1 }

<#
Offer to install a missing prerequisite, rather than only naming it.

A brand-new Windows machine has neither Python nor Node, and "install it from
python.org and tick Add Python to PATH" is three decisions and a wrong-answer
trap in one sentence. `winget` ships with Windows 10 1709+ and Windows 11, so on
any machine likely to run this it is already there and does the whole thing
correctly.

Asked, never assumed: installing software is the operator's call. Declining is a
normal answer and leaves the manual instructions on screen.
#>
function Offer($name, $why, $wingetId, $url) {
    Write-Host ""
    Write-Host "  $name is not installed." -ForegroundColor Yellow
    Write-Host "  Studio needs it $why."
    Write-Host ""

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  Install it now with winget? [Y/n] " -ForegroundColor Cyan -NoNewline
        $answer = Read-Host
        if ($answer -eq "" -or $answer -match "^[Yy]") {
            Write-Host ""
            winget install --id $wingetId --exact --accept-package-agreements --accept-source-agreements
            # winget updates the persisted environment, not this process's copy of
            # it, so the freshly installed executable is not on PATH here until we
            # re-read it. Without this the next probe fails on software that was
            # just installed successfully.
            $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
            $user = [Environment]::GetEnvironmentVariable("Path", "User")
            $env:Path = "$machine;$user"
            return $true
        }
    } else {
        Write-Host "  winget is not available on this machine, so install it by hand:"
    }

    Write-Host ""
    Write-Host "    $url" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Then double-click 'Install Studio.cmd' again."
    return $false
}

# ── prerequisites ───────────────────────────────────────────────────────────

Step "Checking prerequisites"

# `python3` is the Unix name; Windows installs `python`, and the Store shim named
# `python` exits without doing anything, so `py` is tried first where present.
$probe = "import sys; print(sys.version_info[:2] >= (3, 11))"

function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        # `-3` for the launcher, nothing for the others. Never name this `$args` —
        # that is an automatic variable and assigning it breaks the call.
        $prefix = if ($candidate -eq "py") { @("-3") } else { @() }
        $result = & $candidate @prefix "-c" $script:probe 2>$null
        if ($LASTEXITCODE -eq 0 -and "$result" -eq "True") {
            return @{ Cmd = $candidate; Args = $prefix }
        }
    }
    return $null
}

$found = Find-Python
if (-not $found) {
    # Probed again after the install, rather than telling someone to start over:
    # `Offer` refreshes PATH, so the interpreter it just installed is findable in
    # this same run.
    if (Offer "Python 3.11+" "to run the render engine" "Python.Python.3.12" "https://www.python.org/downloads/") {
        $found = Find-Python
    }
}
if (-not $found) {
    Die "Python 3.11+ is still not available. Install it, then run this again."
}
$Python = $found.Cmd
$PythonArgs = $found.Args
$PyVersion = & $Python @PythonArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Note "python $PyVersion"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Offer "Node.js 20+" "to run the web app" "OpenJS.NodeJS.LTS" "https://nodejs.org" | Out-Null
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Die "Node.js is still not available. Install it, then run this again."
}
$NodeMajor = [int](node -p "process.versions.node.split('.')[0]")
if ($NodeMajor -lt 20) {
    Die "Node $(node -v) is too old - 20+ is required. Update it from nodejs.org."
}
Note "node $(node -v)"

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Note "ffmpeg on PATH"
} else {
    # imageio-ffmpeg ships a binary, so this is a note rather than fatal.
    Note "ffmpeg not on PATH - will use the one bundled with imageio-ffmpeg"
}

# ── engine ──────────────────────────────────────────────────────────────────

Step "Setting up the engine"

$VenvPython = Join-Path $Root "apps\engine\.venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Note "creating apps\engine\.venv"
    Push-Location apps\engine
    & $Python @PythonArgs -m venv .venv
    Pop-Location
    if (-not (Test-Path $VenvPython)) { Die "venv creation did not produce $VenvPython" }
} else {
    Note "apps\engine\.venv exists"
}

Note "installing Python dependencies (this is the slow part)"
& $VenvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Die "pip upgrade failed" }

Push-Location apps\engine
& $VenvPython -m pip install --quiet -e ".[dev]"
$installed = $LASTEXITCODE
Pop-Location
if ($installed -ne 0) { Die "installing the engine failed" }

# ── web ─────────────────────────────────────────────────────────────────────

Step "Setting up the web app"
Note "installing npm workspaces"
npm install --silent
if ($LASTEXITCODE -ne 0) { Die "npm install failed" }

# Tailwind v4 compiles CSS through native binaries, and npm records an optional
# dependency only for the platform that generated the lockfile — which was Linux.
# Windows was the platform this actually broke on: no win32 entry existed, the
# binary was never fetched, and the first page load 500'd with "Cannot find module
# '../lightningcss.win32-x64-msvc.node'". The root package.json now pins every
# variant, but a node_modules installed before that is still broken, so fix it here
# rather than letting the browser be the one to report it.
node scripts\check-web-toolchain.mjs *> $null
if ($LASTEXITCODE -ne 0) {
    Note "web dependencies are wrong for this platform - reinstalling from scratch"
    # Every one of them, not just the root. A clean install here hoists everything
    # and leaves no workspace node_modules; one that survives shadows the root copy
    # for anything inside that workspace, and deleting only the root leaves it in
    # place. That is how a machine ran Next 16 with a Next 10-era tree underneath,
    # and npm audit reported 107 findings against 14 on a clean tree.
    node scripts\reinstall.mjs
    if ($LASTEXITCODE -ne 0) { Die "web dependencies are still wrong" }
} else {
    Note "web dependencies OK"
}

# ── config ──────────────────────────────────────────────────────────────────

Step "Configuration"

if (Test-Path ".env") {
    Note ".env already exists - leaving it alone"
} else {
    Copy-Item ".env.example" ".env"
    Note "created .env from .env.example"
}

New-Item -ItemType Directory -Force -Path "storage\bgm", "storage\fonts" | Out-Null
Note "storage\ ready"

# The schema is created on first boot too, but doing it here means the very first
# request is not the one that pays for it.
Step "Creating the database schema"
Push-Location apps\engine
$env:STUDIO_PERSIST = "true"
& $VenvPython -c "import asyncio; from engine import db; print('  ' + asyncio.run(db.ensure_schema()))"
# Checked, like every other native call here. `$ErrorActionPreference` does not
# stop on a non-zero exit from an external program, so a failed schema creation
# scrolled past and setup went on to run the tests and print "Setup complete" —
# on an install whose database does not exist.
$Schema = $LASTEXITCODE
Pop-Location
if ($Schema -ne 0) { Die "could not create the database schema" }

# ── verify ──────────────────────────────────────────────────────────────────

Step "Running the test suite"
Push-Location apps\engine
$env:STUDIO_PERSIST = "false"
# Captured, not piped straight to the host. `... | Select-Object -Last 3` leaves
# `$LASTEXITCODE` describing Select-Object rather than pytest, so a failing suite
# scrolled three lines past and setup went on to print "Setup complete" — the one
# thing this step exists to stop.
$TestOutput = & $VenvPython -m pytest -q 2>&1
$Tests = $LASTEXITCODE
Pop-Location
Remove-Item Env:\STUDIO_PERSIST -ErrorAction SilentlyContinue

$TestOutput | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
if ($Tests -ne 0) {
    Write-Host ""
    Write-Host "The test suite failed. Full output:" -ForegroundColor Yellow
    $TestOutput | ForEach-Object { Write-Host "  $_" }
    Die "the engine is not working on this machine - please open an issue with the output above"
}

Step "Adding a launcher"
# Never fatal — see the note in setup.sh.
node scripts\install-shortcut.mjs

Step "Checking what is still missing"
& $VenvPython (Join-Path $Root "apps\engine\scripts\doctor.py")
$Doctor = $LASTEXITCODE

Write-Host ""
if ($Doctor -eq 0) {
    Write-Host "Setup complete and nothing is missing." -ForegroundColor Green
} else {
    Write-Host "Setup complete." -NoNewline
    Write-Host " The items marked X above need an API key - see SETUP.md."
}

Write-Host ""
Write-Host "Next:"
Write-Host "  Double-click the Studio shortcut on your Desktop." -ForegroundColor Cyan
Write-Host "  (or Studio.cmd in this folder — same thing)"
Write-Host ""
if ($Doctor -eq 0) {
    Write-Host "  Your browser opens by itself. Type a topic and press Generate."
} else {
    Write-Host "  Your browser opens by itself, on the setup screen. Paste your"
    Write-Host "  keys in there — it says what each one unlocks and links to"
    Write-Host "  where to get it."
}
Write-Host ""
# `npm start` runs both halves. Kept here for when you want to restart one on its
# own — and the leading .\ is required, because PowerShell looks a bare `apps\...`
# up as a command name and fails with "The module 'apps' could not be loaded".
Write-Host "To run the two halves separately instead:" -ForegroundColor DarkGray
Write-Host "  npm run dev" -ForegroundColor DarkGray
Write-Host "  .\apps\engine\.venv\Scripts\python -m uvicorn engine.main:app --reload --port 8080" -ForegroundColor DarkGray
Write-Host ""
