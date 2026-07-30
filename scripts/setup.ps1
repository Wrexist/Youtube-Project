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
#
# Keep this file pure ASCII. Windows PowerShell 5.1 reads a .ps1 that has no BOM
# using the machine's ANSI code page, not UTF-8, so a single em dash inside a
# string is decoded as three bytes - and on a double-byte code page one of them
# swallows the closing quote, which surfaces as a pile of nonsense parser errors
# ("Unexpected token ')'") on lines that are perfectly valid. Same for the .cmd
# files at the repository root.

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

function Step($text) { Write-Host ""; Write-Host $text -ForegroundColor White }
function Note($text) { Write-Host "  $text" -ForegroundColor DarkGray }
function Die($text) { Write-Host "X $text" -ForegroundColor Red; exit 1 }

<#
Run an external program with its output captured, and hand back both the text and
the real exit code.

Not a convenience wrapper. `$ErrorActionPreference = "Stop"` together with a
redirected stderr is a trap in Windows PowerShell 5.1: any program that writes a
single line to stderr - a pip deprecation notice, an npm warning, a Python
banner - has that line turned into a NativeCommandError, which then terminates
the script regardless of the exit code. That is how a successful `py` invocation
killed setup with "NotSpecified: (Python 3.14.6 ...) NativeCommandError".

So every capturing call in this file goes through here, where the preference is
lifted for exactly the length of the call and the exit code is read back
explicitly.
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string] $Exe,
        [string[]] $Arguments = @(),
        [string] $WorkingDirectory
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if ($WorkingDirectory) { Push-Location $WorkingDirectory }
    # Pre-set, so a program that never launches at all cannot leave the previous
    # command's success code standing. 9009 is what cmd reports for "not found".
    $global:LASTEXITCODE = 9009
    try {
        # `2>&1` merges stderr in; the ForEach flattens the ErrorRecords that
        # merging produces down to their text, so callers get plain strings.
        $lines = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } catch {
        $lines = @("$_")
        $code = 9009
    } finally {
        if ($WorkingDirectory) { Pop-Location }
        $ErrorActionPreference = $previous
    }

    return @{ Lines = $lines; Text = ($lines -join [Environment]::NewLine); Code = $code }
}

# The tail of a captured run, for when it failed and the reason is in there.
function Show-Output($result, $count = 20) {
    if (-not $result) { return }
    $result.Lines | Select-Object -Last $count | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

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
function Offer($name, $why, $wingetId, $url, $headline) {
    if (-not $headline) { $headline = "$name is not installed." }
    Write-Host ""
    Write-Host "  $headline" -ForegroundColor Yellow
    Write-Host "  Studio needs it $why."
    Write-Host ""

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  Install it now with winget? [Y/n] " -ForegroundColor Cyan -NoNewline
        $answer = Read-Host
        if ($answer -eq "" -or $answer -match "^[Yy]") {
            Write-Host ""
            # `| Out-Host` so winget's progress goes to the console rather than
            # into this function's return value, which is a boolean the caller
            # branches on. Its exit code is not checked on purpose: winget reports
            # non-zero for "already installed" and for "installed, reboot pending"
            # alike, and the probe that follows is the answer that matters.
            winget install --id $wingetId --exact --accept-package-agreements --accept-source-agreements | Out-Host
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

# -- prerequisites -----------------------------------------------------------

Step "Checking prerequisites"

# 3.11 is the floor. The ceiling is not pedantry: several dependencies ship
# compiled wheels (ctranslate2 behind faster-whisper, scipy, pillow) and on a
# Python newer than those wheels exist for, pip falls back to building them from
# source - which on Windows means a missing C++ toolchain and several hundred
# lines of compiler errors instead of an install. So a supported interpreter is
# preferred over merely the newest one present.
$PyMin = [Version] "3.11"
$PyTooNew = [Version] "3.14"

# A probe file rather than `python -c`. Quoting a one-liner through PowerShell
# into a native program is fragile, and Python 3.14 ships a rewritten `py` (the
# Python install manager) whose argument handling differs from the old launcher's
# - under which this probe started an interactive interpreter and printed its
# banner instead of answering the question. A file has nothing left to misparse.
$ProbeBody = @(
    "import sys",
    "print('STUDIO_PY %d.%d.%d' % sys.version_info[:3])"
)
$ProbeScript = Join-Path ([IO.Path]::GetTempPath()) "studio-python-probe.py"
try {
    Set-Content -Path $ProbeScript -Encoding ASCII -Value $ProbeBody -ErrorAction Stop
} catch {
    # A locked-down or full TEMP is a real machine state, and the repository is
    # somewhere we are about to write far more than this anyway.
    $ProbeScript = Join-Path $Root "studio-python-probe.py"
    Set-Content -Path $ProbeScript -Encoding ASCII -Value $ProbeBody
}

# $null unless this really is a working interpreter that printed the sentinel.
# Deliberately not keyed on the exit code alone: the Microsoft Store shim named
# `python` exits 0 having done nothing at all.
function Get-PythonVersion($exe, $exeArgs) {
    $result = Invoke-Native $exe (@($exeArgs) + @($script:ProbeScript))
    $match = [regex]::Match($result.Text, "STUDIO_PY (\d+)\.(\d+)\.(\d+)")
    if (-not $match.Success) { return $null }
    return [Version] ("{0}.{1}.{2}" -f $match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value)
}

# Every interpreter on this machine that answers the probe, best candidate first.
function Find-Pythons {
    $candidates = @()

    # An explicit choice wins over everything, for the machine where none of the
    # discovery below finds the right one.
    if ($env:STUDIO_PYTHON) { $candidates += , @{ Cmd = $env:STUDIO_PYTHON; Args = @() } }

    $hasPy = [bool] (Get-Command py -ErrorAction SilentlyContinue)

    # Ask the launcher for a specific feature version before asking it for "the
    # newest", so a machine with both 3.12 and a brand-new 3.14 gets 3.12.
    if ($hasPy) {
        foreach ($v in @("3.13", "3.12", "3.11")) { $candidates += , @{ Cmd = "py"; Args = @("-$v") } }
    }

    # Straight off disk too, for when the launcher is absent or is a version whose
    # own CLI cannot be trusted.
    foreach ($v in @("313", "312", "311")) {
        foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", $env:ProgramFiles, "${env:ProgramFiles(x86)}", "C:\")) {
            if (-not $base) { continue }
            # Concatenated rather than Join-Path'd: Join-Path resolves the leading
            # element as a PSDrive and throws "a drive with the name 'C' does not
            # exist" on any host where it is absent, which is a hard stop in the
            # middle of a probe whose whole job is to tolerate things not existing.
            $exe = $base.TrimEnd("\") + "\Python$v\python.exe"
            if (Test-Path $exe) { $candidates += , @{ Cmd = $exe; Args = @() } }
        }
    }

    # Last: whatever is on PATH, at whatever version.
    if ($hasPy) { $candidates += , @{ Cmd = "py"; Args = @("-3") } }
    foreach ($c in @("python", "python3")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $candidates += , @{ Cmd = $c; Args = @() } }
    }

    $results = @()
    foreach ($c in $candidates) {
        $version = Get-PythonVersion $c.Cmd $c.Args
        if ($version) { $results += , @{ Cmd = $c.Cmd; Args = $c.Args; Version = $version } }
    }
    return $results
}

function Select-Supported($pythons) {
    return @($pythons | Where-Object { $_.Version -ge $script:PyMin -and $_.Version -lt $script:PyTooNew })[0]
}

$pythons = @(Find-Pythons)
$found = Select-Supported $pythons
$tooNew = @($pythons | Where-Object { $_.Version -ge $PyTooNew })[0]

if (-not $found) {
    $headline = $null
    if ($tooNew) {
        # Naming the version matters here. "Python is not installed" in front of
        # someone who just installed Python reads as a broken script.
        $headline = "The only Python here is $($tooNew.Version), which is newer than the engine's dependencies have Windows wheels for."
    }
    # Probed again after the install, rather than telling someone to start over:
    # `Offer` refreshes PATH, so the interpreter it just installed is findable in
    # this same run. It installs alongside, and changes nothing about 3.14.
    if (Offer "Python 3.12" "to run the render engine" "Python.Python.3.12" "https://www.python.org/downloads/" $headline) {
        $found = Select-Supported @(Find-Pythons)
    }
}
if (-not $found -and $tooNew) {
    Note "carrying on with Python $($tooNew.Version) - if pip fails building a wheel below, this is why"
    $found = $tooNew
}
if (-not $found) {
    Die "Python 3.11+ is still not available. Install it, then run this again."
}
$Python = $found.Cmd
$PythonArgs = $found.Args
$PythonLabel = "$Python $($PythonArgs -join ' ')"
Note "python $($found.Version) ($($PythonLabel.Trim()))"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Offer "Node.js 20+" "to run the web app" "OpenJS.NodeJS.LTS" "https://nodejs.org" | Out-Null
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Die "Node.js is still not available. Install it, then run this again."
}
$nodeProbe = Invoke-Native "node" @("-v")
$nodeMatch = [regex]::Match($nodeProbe.Text, "v(\d+)\.(\d+)\.(\d+)")
if (-not $nodeMatch.Success) {
    Show-Output $nodeProbe
    Die "node is on PATH but does not run - reinstall it from nodejs.org."
}
if ([int] $nodeMatch.Groups[1].Value -lt 20) {
    Die "Node $($nodeMatch.Groups[0].Value) is too old - 20+ is required. Update it from nodejs.org."
}
Note "node $($nodeMatch.Groups[0].Value)"

# npm ships with Node, but a PATH that has one without the other is a real state,
# and its failure mode otherwise is an unexplained stop three steps later.
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Die "npm is not on PATH even though node is. Reinstall Node from nodejs.org."
}

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Note "ffmpeg on PATH"
} else {
    # imageio-ffmpeg ships a binary, so this is a note rather than fatal.
    Note "ffmpeg not on PATH - will use the one bundled with imageio-ffmpeg"
}

# -- engine ------------------------------------------------------------------

Step "Setting up the engine"

$VenvDir = Join-Path $Root "apps\engine\.venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# An existing venv is reused, but only once it has been proven to still work.
# Both of the states checked for here are ones a previous failed run leaves
# behind, and both otherwise surface much later as something unrelated.
if (Test-Path $VenvPython) {
    $venvVersion = Get-PythonVersion $VenvPython @()
    if (-not $venvVersion) {
        Note "apps\engine\.venv does not run - rebuilding it"
        Remove-Item -Recurse -Force $VenvDir
    } elseif ($venvVersion -ge $PyTooNew -and $found.Version -lt $PyTooNew) {
        Note "apps\engine\.venv is on Python $venvVersion - rebuilding it on $($found.Version)"
        Remove-Item -Recurse -Force $VenvDir
    } else {
        Note "apps\engine\.venv exists (python $venvVersion)"
    }
} elseif (Test-Path $VenvDir) {
    # A .venv directory with no interpreter in it: a venv creation that died
    # halfway. `python -m venv` will not repair it, so remove it.
    Note "apps\engine\.venv is incomplete - rebuilding it"
    Remove-Item -Recurse -Force $VenvDir
}

if (-not (Test-Path $VenvPython)) {
    Note "creating apps\engine\.venv"
    $venv = Invoke-Native $Python (@($PythonArgs) + @("-m", "venv", ".venv")) -WorkingDirectory "apps\engine"
    if (-not (Test-Path $VenvPython)) {
        Show-Output $venv
        Die "venv creation did not produce $VenvPython"
    }
}

Note "installing Python dependencies (this is the slow part - a few minutes)"
$pip = Invoke-Native $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip")
if ($pip.Code -ne 0) { Show-Output $pip; Die "pip upgrade failed" }

$engine = Invoke-Native $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", ".[dev]") -WorkingDirectory "apps\engine"
if ($engine.Code -ne 0) {
    # 40 lines, because a wheel that failed to build buries the actual cause under
    # a long compiler transcript.
    Show-Output $engine 40
    Die "installing the engine failed"
}

# -- web ---------------------------------------------------------------------

Step "Setting up the web app"
Note "installing npm workspaces (also slow)"
$npm = Invoke-Native "npm" @("install", "--silent", "--no-fund", "--no-audit")
if ($npm.Code -ne 0) { Show-Output $npm 40; Die "npm install failed" }

# Tailwind v4 compiles CSS through native binaries, and npm records an optional
# dependency only for the platform that generated the lockfile - which was Linux.
# Windows was the platform this actually broke on: no win32 entry existed, the
# binary was never fetched, and the first page load 500'd with "Cannot find module
# '../lightningcss.win32-x64-msvc.node'". The root package.json now pins every
# variant, but a node_modules installed before that is still broken, so fix it here
# rather than letting the browser be the one to report it.
$toolchain = Invoke-Native "node" @("scripts\check-web-toolchain.mjs")
if ($toolchain.Code -ne 0) {
    Note "web dependencies are wrong for this platform - reinstalling from scratch"
    # Every one of them, not just the root. A clean install here hoists everything
    # and leaves no workspace node_modules; one that survives shadows the root copy
    # for anything inside that workspace, and deleting only the root leaves it in
    # place. That is how a machine ran Next 16 with a Next 10-era tree underneath,
    # and npm audit reported 107 findings against 14 on a clean tree.
    $reinstall = Invoke-Native "node" @("scripts\reinstall.mjs")
    if ($reinstall.Code -ne 0) { Show-Output $reinstall 40; Die "web dependencies are still wrong" }
} else {
    Note "web dependencies OK"
}

# -- config ------------------------------------------------------------------

Step "Configuration"

if (Test-Path ".env") {
    Note ".env already exists - leaving it alone"
} elseif (Test-Path ".env.example") {
    Copy-Item ".env.example" ".env"
    Note "created .env from .env.example"
} else {
    # Not fatal: every key in it is optional to boot, and the Setup screen writes
    # the file itself. Worth saying out loud, though, rather than dying on a
    # Copy-Item that names a file the operator has never heard of.
    Note ".env.example is missing - starting with no .env; add keys on the Setup screen"
}

New-Item -ItemType Directory -Force -Path "storage\bgm", "storage\fonts" | Out-Null
Note "storage\ ready"

# The schema is created on first boot too, but doing it here means the very first
# request is not the one that pays for it.
Step "Creating the database schema"
$env:STUDIO_PERSIST = "true"
$schema = Invoke-Native $VenvPython @("-c", "import asyncio; from engine import db; print(asyncio.run(db.ensure_schema()))") -WorkingDirectory "apps\engine"
# Checked, like every other native call here. `$ErrorActionPreference` does not
# stop on a non-zero exit from an external program, so a failed schema creation
# scrolled past and setup went on to run the tests and print "Setup complete" -
# on an install whose database does not exist.
if ($schema.Code -ne 0) {
    Show-Output $schema 40
    Die "could not create the database schema"
}
Show-Output $schema 1

# -- verify ------------------------------------------------------------------

Step "Running the test suite"
$env:STUDIO_PERSIST = "false"
# Captured whole, then summarised - only the last few lines are interesting when
# it passes, and all of it is when it does not. Reading the exit code from the
# captured run (rather than after piping pytest into a cmdlet) is what keeps a
# failing suite from scrolling past and setup going on to print "Setup complete",
# the one thing this step exists to stop.
$tests = Invoke-Native $VenvPython @("-m", "pytest", "-q") -WorkingDirectory "apps\engine"
Remove-Item Env:\STUDIO_PERSIST -ErrorAction SilentlyContinue

Show-Output $tests 3
if ($tests.Code -ne 0) {
    Write-Host ""
    Write-Host "The test suite failed. Full output:" -ForegroundColor Yellow
    $tests.Lines | ForEach-Object { Write-Host "  $_" }
    Die "the engine is not working on this machine - please open an issue with the output above"
}

Step "Adding a launcher"
# Never fatal - see the note in setup.sh.
$shortcut = Invoke-Native "node" @("scripts\install-shortcut.mjs")
Show-Output $shortcut 5

Remove-Item $ProbeScript -ErrorAction SilentlyContinue

Step "Checking what is still missing"
# The one native call deliberately left uncaptured: this prints a colour-coded
# checklist that the operator is meant to read, and capturing it would flatten
# that into grey text. Uncaptured stderr goes straight to the console and cannot
# raise the NativeCommandError that Invoke-Native exists to contain.
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
Write-Host "  (or Studio.cmd in this folder - same thing)"
Write-Host ""
if ($Doctor -eq 0) {
    Write-Host "  Your browser opens by itself. Type a topic and press Generate."
} else {
    Write-Host "  Your browser opens by itself, on the setup screen. Paste your"
    Write-Host "  keys in there - it says what each one unlocks and links to"
    Write-Host "  where to get it."
}
Write-Host ""
# `npm start` runs both halves. Kept here for when you want to restart one on its
# own - and the leading .\ is required, because PowerShell looks a bare `apps\...`
# up as a command name and fails with "The module 'apps' could not be loaded".
Write-Host "To run the two halves separately instead:" -ForegroundColor DarkGray
Write-Host "  npm run dev" -ForegroundColor DarkGray
Write-Host "  .\apps\engine\.venv\Scripts\python -m uvicorn engine.main:app --reload --port 8080" -ForegroundColor DarkGray
Write-Host ""
