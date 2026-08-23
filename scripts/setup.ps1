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

# -FullTests runs the entire engine suite instead of the smoke check. See the
# "Checking the engine runs here" step for why that is not the default.
param(
    [switch]$FullTests
)

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

# -- how this looks ----------------------------------------------------------
#
# Everything is drawn in a single gutter: a numbered heading at four columns,
# its detail at seven, so the eye has one edge to follow down a screen that
# takes minutes to fill. The step counter matters more than it looks - most of
# the runtime is three steps that print nothing while they work, and "3/8" is
# the difference between waiting and wondering whether it has hung.
#
# ASCII only, including the marks. See the note at the top of the file: this
# script is read using the machine's ANSI code page, so a tick or a box-drawing
# character is not a rendering question, it is a parse error waiting for the
# wrong locale.

$script:StepNumber = 0
$script:StepTotal = 8

function Step($text) {
    $script:StepNumber++
    Write-Host ""
    Write-Host ("  {0}/{1} " -f $script:StepNumber, $script:StepTotal) -ForegroundColor DarkGray -NoNewline
    Write-Host $text -ForegroundColor White
}

function Note($text) { Write-Host "       $text" -ForegroundColor DarkGray }

# A step that finished, with what it cost. Only used where the wait was long
# enough that finishing is itself news.
function Done($text, $elapsed) {
    Write-Host "       ok " -ForegroundColor Green -NoNewline
    Write-Host $text -ForegroundColor DarkGray -NoNewline
    if ($elapsed) { Write-Host ("  " + (Format-Elapsed $elapsed)) -ForegroundColor DarkGray -NoNewline }
    Write-Host ""
}

function Die($text) {
    Write-Host ""
    Write-Host "  X  $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Format-Elapsed([TimeSpan] $span) {
    if ($span.TotalSeconds -lt 60) { return "{0}s" -f [int] $span.TotalSeconds }
    return "{0}m {1:00}s" -f [int] $span.TotalMinutes, $span.Seconds
}

# Whether it is worth animating anything at all. A redirected stream - a CI log,
# `> setup.log`, anything piping this into a file - records every frame of a
# spinner as literal text, so the carriage returns that make it an animation on
# a console make it unreadable there instead.
$script:Animate = -not [Console]::IsOutputRedirected

function Show-Header {
    Write-Host ""
    Write-Host "  Studio" -ForegroundColor Cyan
    Write-Host "  Setting up. A few minutes, mostly downloads - you can leave it." -ForegroundColor DarkGray
}

<#
Resolve a command name to a real executable, stepping around PowerShell shims.

Not defensive PATH paranoia - one specific, load-bearing bug. Node installs
`npm.ps1` next to `npm.cmd`, and PowerShell resolves a bare `npm` to the *.ps1*
because scripts outrank applications. That shim does not read `$args`. It
reconstructs the command line from the caller's own source text:

    $NPM_ARGS = $COMMAND.Substring($MyInvocation.InvocationName.Length).Trim()
    Invoke-Expression "& `"$NODE_EXE`" `"$NPM_CLI_JS`" $NPM_ARGS"

Every call in this file goes through `& $Exe @Arguments`. The shim chopped
`"npm".Length` - three characters - off the front of that literal text, leaving
`Exe @Arguments`, and passed it on. So npm was invoked as `npm Exe install ...`
and answered:

    Unknown command: "Exe"

followed by this script's own "npm install failed", on a machine where npm, Node
and the lockfile were all perfectly fine. Nothing about the arguments could have
fixed it; the shim never saw them.

Preferring the Application entry avoids the whole mechanism - npm.cmd takes its
arguments the ordinary way. Extension order matters too: Node also installs an
extensionless `npm` (a bash script) that Windows cannot execute at all.
#>
$script:ExeCache = @{}
function Resolve-Exe([string] $name) {
    if ($script:ExeCache.ContainsKey($name)) { return $script:ExeCache[$name] }

    # Already a path - there is nothing to disambiguate, and Get-Command on a
    # path would only hand back what we gave it.
    $resolved = $name
    if ($name -notmatch "[\\/]") {
        $apps = @(
            Get-Command $name -All -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandType -eq "Application" }
        )
        if ($apps.Count -gt 0) {
            $preferred = @(".exe", ".com", ".cmd", ".bat")
            $best = $apps | Sort-Object @{ Expression = {
                $i = $preferred.IndexOf([IO.Path]::GetExtension($_.Source).ToLowerInvariant())
                if ($i -lt 0) { 99 } else { $i }
            } } | Select-Object -First 1
            $resolved = $best.Source
        }
    }

    $script:ExeCache[$name] = $resolved
    return $resolved
}

<#
Run an external program with its output captured, and hand back both the text and
the real exit code.

Not a convenience wrapper. `$ErrorActionPreference = "Stop"` together with a
redirected stderr is a trap in Windows PowerShell 5.1: any program that writes a
single line to stderr - a pip deprecation notice, an npm warning, a Python
banner - has that line turned into a NativeCommandError, which then terminates
the script regardless of the exit code. That is how a successful `py` invocation
killed setup with "NotSpecified: (Python 3.14.6 ...) NativeCommandError".

So every capturing call in this file goes through here, on a runspace of its own
where the preference is `Continue` from the start and the exit code is read back
explicitly.

The runspace is also what makes the waiting visible. Three of these calls -
pip, npm, pytest - account for nearly all of the runtime and print nothing at
all while they work, because their output is being captured. Run inline that is
several silent minutes, which is indistinguishable from a hang; run on a
runspace, this thread is free to draw a spinner and a clock against it. The
program itself is still invoked exactly as it was, `& $path @Arguments 2>&1`,
so none of the behaviour above changes - only who is watching it.
#>
$script:NativeWorker = @'
param($Path, $Arguments, $Directory)

# Not inherited from the caller: a fresh runspace starts at the default, which
# is what this needs anyway. Stated rather than assumed, because the whole
# NativeCommandError trap turns on it.
$ErrorActionPreference = "Continue"

if ($Directory) {
    # Checked rather than attempted. Running the program in the wrong directory
    # and reporting "npm install failed" is a lie; this is the truth.
    if (-not (Test-Path -LiteralPath $Directory)) {
        return @{ Lines = @("cannot enter $Directory"); Code = 9009 }
    }
    Set-Location -LiteralPath $Directory
}

# Pre-set, so a program that never launches at all cannot leave the previous
# command's success code standing. 9009 is what cmd reports for "not found".
$global:LASTEXITCODE = 9009
try {
    # `2>&1` merges stderr in; the ForEach flattens the ErrorRecords that
    # merging produces down to their text, so callers get plain strings.
    $lines = @(& $Path @Arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
} catch {
    $lines = @("$_")
    $code = 9009
}
return @{ Lines = $lines; Code = $code }
'@

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string] $Exe,
        [string[]] $Arguments = @(),
        [string] $WorkingDirectory,
        # Present only for the slow ones. Naming the activity is what turns the
        # spinner from decoration into information.
        [string] $Activity
    )

    # Resolved to a real executable first - see Resolve-Exe. Calling the name
    # directly is what let npm's PowerShell shim read the call site as its own
    # argument list.
    $path = Resolve-Exe $Exe
    $directory = if ($WorkingDirectory) { Join-Path $Root $WorkingDirectory } else { $null }

    $started = Get-Date
    $shell = [PowerShell]::Create()
    try {
        [void] $shell.AddScript($script:NativeWorker)
        [void] $shell.AddArgument($path)
        [void] $shell.AddArgument($Arguments)
        [void] $shell.AddArgument($directory)

        $handle = $shell.BeginInvoke()
        Wait-Native $handle $Activity $started
        $output = $shell.EndInvoke($handle)
    } catch {
        return @{ Lines = @("$_"); Text = "$_"; Code = 9009; Elapsed = ((Get-Date) - $started) }
    } finally {
        $shell.Dispose()
    }

    # The worker's single return value, however the collection wrapped it.
    $result = @($output | Where-Object { $_ -is [hashtable] })[-1]
    if (-not $result) {
        $message = "$Exe produced no result"
        return @{ Lines = @($message); Text = $message; Code = 9009; Elapsed = ((Get-Date) - $started) }
    }

    $lines = @($result.Lines)
    return @{
        Lines   = $lines
        Text    = ($lines -join [Environment]::NewLine)
        Code    = $result.Code
        Elapsed = ((Get-Date) - $started)
    }
}

<#
Block until the call finishes, drawing a spinner and a clock if there is anyone
to watch it.

The line is rewritten in place with a carriage return and cleared on the way
out, so the finished step gets to print its own one-line summary over the top
rather than leaving a dead progress line in the transcript.
#>
function Wait-Native($handle, $activity, $started) {
    if (-not $activity -or -not $script:Animate) {
        [void] $handle.AsyncWaitHandle.WaitOne()
        return
    }

    $frames = @("|", "/", "-", "\")
    $i = 0
    while (-not $handle.IsCompleted) {
        $line = "  {0}  {1}  {2}" -f $frames[$i % $frames.Count], $activity, (Format-Elapsed ((Get-Date) - $started))
        Write-Host ("`r     " + $line.PadRight(70)) -ForegroundColor DarkGray -NoNewline
        $i++
        # Slow enough not to burn a core drawing four characters, fast enough to
        # read as motion rather than as a stutter.
        [void] $handle.AsyncWaitHandle.WaitOne(130)
    }
    Write-Host ("`r" + (" " * 78) + "`r") -NoNewline
}

# The tail of a captured run, for when it failed and the reason is in there.
function Show-Output($result, $count = 20) {
    if (-not $result) { return }
    $result.Lines | Select-Object -Last $count | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
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

Show-Header

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
    } elseif ($venvVersion -lt $PyMin) {
        # The floor as well as the ceiling. A venv left behind on 3.9 answers the
        # probe perfectly well and is below the ceiling, so without this it is
        # reused and the failure lands several minutes later as a requires-python
        # error out of pip, which reads like a broken dependency rather than an
        # interpreter this project never supported.
        Note "apps\engine\.venv is on Python $venvVersion, below $PyMin - rebuilding it on $($found.Version)"
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

$pip = Invoke-Native $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip") -Activity "updating pip"
if ($pip.Code -ne 0) { Show-Output $pip; Die "pip upgrade failed" }

$engine = Invoke-Native $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", ".[dev]") -WorkingDirectory "apps\engine" -Activity "installing Python dependencies (the slow one)"
if ($engine.Code -ne 0) {
    # 40 lines, because a wheel that failed to build buries the actual cause under
    # a long compiler transcript.
    Show-Output $engine 40
    Die "installing the engine failed"
}
Done "Python dependencies" $engine.Elapsed

# -- web ---------------------------------------------------------------------

Step "Setting up the web app"
# `--loglevel=error`, not `--silent`. Silent suppresses npm's own error reporting
# as well as its progress, so a failed install printed forty lines of nothing
# through Show-Output. Checked: a 404 on a missing package produces no output at
# all under --silent, and the full `npm error 404` block under --loglevel=error.
$npm = Invoke-Native "npm" @("install", "--loglevel=error", "--no-fund", "--no-audit") -Activity "installing npm workspaces"
if ($npm.Code -ne 0) { Show-Output $npm 40; Die "npm install failed" }
Done "npm workspaces" $npm.Elapsed

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
    $reinstall = Invoke-Native "node" @("scripts\reinstall.mjs") -Activity "reinstalling web dependencies"
    if ($reinstall.Code -ne 0) { Show-Output $reinstall 40; Die "web dependencies are still wrong" }
    Done "web dependencies rebuilt for this platform" $reinstall.Elapsed
} else {
    Done "platform binaries"
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

if ($FullTests) { Step "Running the full test suite" } else { Step "Checking the engine runs here" }
$env:STUDIO_PERSIST = "false"
# A smoke check by default, not the whole suite.
#
# This step answers one question - "is the engine broken on THIS machine" - and
# the full suite is the wrong instrument for it. It takes thirteen minutes, which
# is most of the install, and all but a few seconds of that is business logic
# that cannot vary by machine. What does vary is covered here and in the doctor
# below: Python and its packages import, ffmpeg and MoviePy actually compose a
# file, fonts resolve, settings bind, SQLite is reachable.
#
# The cost of running everything was not just time. A single environment-sensitive
# assertion - one test that built a timestamp from local time rather than UTC -
# failed on any machine east of UTC and aborted the whole install with "the engine
# is not working on this machine", on an install that was in fact fine. A gate
# that fails for reasons unrelated to the thing it guards teaches people to ignore
# it, and this one is not ignorable: it stops setup dead.
#
# Developers who want the regression suite pass -FullTests, and the line printed
# at the end says so.
$suiteArgs = if ($FullTests) { @("-m", "pytest", "-q") } else {
    @("-m", "pytest", "-q",
      "tests/test_compose_bake.py",      # MoviePy + ffmpeg + fonts, end to end
      "tests/test_settings_are_wired.py", # .env actually reaches the settings object
      "tests/test_setup.py")              # the screen the operator lands on next
}
$activity = if ($FullTests) { "running the full test suite" } else { "checking the engine" }
# Captured whole, then summarised - only the last few lines are interesting when
# it passes, and all of it is when it does not. Reading the exit code from the
# captured run (rather than after piping pytest into a cmdlet) is what keeps a
# failing suite from scrolling past and setup going on to print "Setup complete",
# the one thing this step exists to stop.
$tests = Invoke-Native $VenvPython $suiteArgs -WorkingDirectory "apps\engine" -Activity $activity
Remove-Item Env:\STUDIO_PERSIST -ErrorAction SilentlyContinue

if ($tests.Code -ne 0) {
    Show-Output $tests 3
    Write-Host ""
    Write-Host "       The checks failed. Full output:" -ForegroundColor Yellow
    $tests.Lines | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
    Die "the engine is not working on this machine - please open an issue with the output above"
}
# pytest's own summary line, minus the parts this line already carries. It says
# "877 passed, 3 skipped, 1374 warnings in 121.48s (0:02:01)"; the duration is
# printed alongside anyway, and a warning count is not news on a suite that
# passed. What is left - "877 passed, 3 skipped" - is preferred over a count of
# our own, because a suite that passed with skips should say so.
$summary = @($tests.Lines | Where-Object { $_ -match "passed|no tests ran" })[-1]
$summary = ($summary -replace "\s+in\s+[\d.]+s.*$", "") -replace ",?\s*\d+ warnings?", ""
$summary = ($summary -replace "\s+", " ").Trim()
# A pytest that changes its summary wording should not blank this line out. The
# exit code is what decided we are here; the summary only decorates it.
if (-not $summary) { $summary = "the test suite passed" }
if (-not $FullTests) { $summary = "$summary - full suite: setup.ps1 -FullTests" }
Done $summary $tests.Elapsed

Step "Adding a launcher"
# Never fatal - see the note in setup.sh.
$shortcut = Invoke-Native "node" @("scripts\install-shortcut.mjs")
# Trimmed and re-indented rather than passed to Show-Output, which preserves
# leading whitespace on purpose (a pytest traceback is unreadable without it).
# The script prints its own two-space gutter for the sh installer, and stacking
# that on top of this one's seven left the only line of the step hanging.
$shortcut.Lines | Select-Object -Last 5 | ForEach-Object { Note $_.Trim() }

Remove-Item $ProbeScript -ErrorAction SilentlyContinue

Step "Checking what is still missing"
# The one native call deliberately left uncaptured: this prints a colour-coded
# checklist that the operator is meant to read, and capturing it would flatten
# that into grey text. Uncaptured stderr goes straight to the console and cannot
# raise the NativeCommandError that Invoke-Native exists to contain.
& $VenvPython (Join-Path $Root "apps\engine\scripts\doctor.py")
$Doctor = $LASTEXITCODE

# One rule below the checklist, so "what is left to do" is visually separate
# from "here is the thing you came for". Without it the doctor's list and the
# next step run together into one wall at the exact moment someone stops reading.
Write-Host ""
Write-Host ("  " + ("-" * 66)) -ForegroundColor DarkGray
Write-Host ""
if ($Doctor -eq 0) {
    Write-Host "  Setup complete." -ForegroundColor Green -NoNewline
    Write-Host " Nothing is missing."
} else {
    Write-Host "  Setup complete." -ForegroundColor Green -NoNewline
    # Deliberately does not name the mark. The doctor prints a real tick and
    # cross; this file is pure ASCII and cannot, and "the items marked X" in
    # front of a list marked with something else is worse than not saying.
    Write-Host " The items listed above still need an API key."
    Write-Host "  You can add them on the Setup screen, or see SETUP.md." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Start Studio" -ForegroundColor White
Write-Host "    Double-click the " -NoNewline
Write-Host "Studio" -ForegroundColor Cyan -NoNewline
Write-Host " shortcut on your Desktop."
Write-Host "    (or Studio.cmd in this folder - same thing)" -ForegroundColor DarkGray
Write-Host ""
if ($Doctor -eq 0) {
    Write-Host "    Your browser opens by itself. Type a topic and press Generate." -ForegroundColor DarkGray
} else {
    Write-Host "    Your browser opens by itself, on the setup screen. Paste your keys" -ForegroundColor DarkGray
    Write-Host "    in there - it says what each one unlocks and links to where to get it." -ForegroundColor DarkGray
}
Write-Host ""
# `npm start` runs both halves. Kept here for when you want to restart one on its
# own - and the leading .\ is required, because PowerShell looks a bare `apps\...`
# up as a command name and fails with "The module 'apps' could not be loaded".
Write-Host "  To run the two halves separately instead:" -ForegroundColor DarkGray
Write-Host "    npm run dev" -ForegroundColor DarkGray
Write-Host "    .\apps\engine\.venv\Scripts\python -m uvicorn engine.main:app --reload --port 8080" -ForegroundColor DarkGray
Write-Host ""
