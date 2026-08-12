#
# Install or update Autodarts on Windows.
#
#   $env:AUTODARTS_BASE_URL = 'https://YOUR-PAGES/updates'
#   irm https://YOUR-PAGES/updates/installers/install-windows.ps1 | iex
#
# The Windows counterpart of install-pi.sh, and deliberately the same shape.
# Produces the identical layout on both platforms, so the in-app updater,
# launcher.py and backend/paths.py behave the same everywhere:
#
#   %LOCALAPPDATA%\Autodarts\
#     .autodarts-root    launcher.py    runtime\   (the interpreter)
#     app\               config\        staging\   logs\
#
# The interpreter is provisioned here rather than shipped. If this machine has
# a usable Python, a venv is built from it exactly as the Pi does. If it does
# not - which is the normal state of a Windows box - python.org's embeddable
# distribution is downloaded and used instead, so there is still nothing for
# the recipient to install first. launcher.py already looks for both layouts
# (runtime\Scripts\python.exe and runtime\python.exe), so either works.
#
# This replaced an Inno Setup installer that bundled a ~200 MB interpreter into
# a Setup.exe. Once OpenCV and numpy left backend/requirements.txt, nothing
# left in it needs a compiler or a large wheel, so building the environment on
# the target machine became both simpler to maintain and a far smaller
# download - and it removed Inno Setup from the release process entirely.
#
# Safe to re-run: it replaces app\ and runtime\ and never touches config\.
# Re-running is also a perfectly good way to update, though the Updates page
# in the app is the easier one.

param(
    # Defaults come from the environment so the script works both saved to disk
    # and piped straight into iex, where there is no way to pass arguments.
    [string] $BaseUrl = $env:AUTODARTS_BASE_URL,
    [string] $Channel = $env:AUTODARTS_CHANNEL,
    [string] $Target  = $env:AUTODARTS_HOME
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Invoke-WebRequest renders a progress bar for every chunk on PowerShell 5.1,
# which makes a multi-megabyte download take minutes. WebClient is used for the
# payload instead; this covers anything that slips through.
$ProgressPreference = 'SilentlyContinue'

# Windows 10 builds still in the wild default to TLS 1.0, which GitHub Pages and
# S3 both refuse - producing an "underlying connection was closed" error that
# says nothing about the actual cause.
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# The embeddable interpreter used when the machine has no Python of its own.
# Pinned rather than "latest" so every install of a given release gets the same
# interpreter, and matched to what the old bundled runtime shipped.
$PythonVersion = '3.11.9'
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'

# The oldest Python the backend runs on. FastAPI and the `X | None` annotations
# throughout the codebase need 3.10.
$MinimumPython = [Version]'3.10'

# pip is deliberately kept, unlike the bundled runtime this replaced.
#
# That runtime was stripped of pip to cut ~25 MB from a download every recipient
# had to fetch, and the cost was that a release needing a new dependency could
# not be applied to an existing install at all - it required a whole new
# installer (hence `min_runtime` in the manifest). Now that the runtime is built
# on the target machine, the 25 MB is local disk rather than bandwidth, and
# keeping pip means a re-run of this script installs new dependencies into the
# existing runtime instead of rebuilding it from scratch. Nothing in the app
# ever invokes pip itself, on either platform.


function Say  { param([string] $Message) Write-Host "`n$Message" -ForegroundColor White }
function Info { param([string] $Message) Write-Host "  $Message" }
function Die  {
    param([string] $Message)
    Write-Host "`nerror: $Message" -ForegroundColor Red
    exit 1
}

# The leading comma in `return , $bytes` is load-bearing throughout this script.
# PowerShell enumerates an array returned from a function into the pipeline, which
# would turn a byte[] into one pipeline object per byte: catastrophically slow for
# a multi-megabyte blob, arriving as Object[] rather than byte[], and - for an
# empty file - collapsing to $null. Wrapping it in a single-element array means
# the byte[] itself comes back out whole.
function Get-Bytes {
    param([string] $Url)
    $client = New-Object Net.WebClient
    try   { return , $client.DownloadData($Url) }
    finally { $client.Dispose() }
}

function Get-Text {
    param([string] $Url)
    return [Text.Encoding]::UTF8.GetString((Get-Bytes $Url))
}

function Expand-Blob {
    <#
        A blob's real bytes, whether or not the host already expanded it.

        Blobs are stored gzipped under a .gz key. Most static hosts serve that
        verbatim, but some - and some CDNs - recognise the extension and send
        Content-Encoding: gzip, at which point the HTTP client decompresses
        transparently and this receives the expanded bytes already. Both are
        correct behaviour by the host, so this has to cope with either or the
        same release installs from one host and fails from another.
        backend/update/client.py `_decompress` does exactly this, for exactly
        this reason.

        Sniffing the magic number is safe rather than a guess: the caller checks
        the SHA-256 of whatever comes back against the manifest, so a wrong
        branch here cannot pass verification.
    #>
    param([byte[]] $Data)
    if ($Data.Length -lt 2 -or $Data[0] -ne 0x1f -or $Data[1] -ne 0x8b) {
        return , $Data
    }
    # $source, not $input: $input is an automatic variable in PowerShell and
    # assigning to it inside a function silently breaks the pipeline.
    $source = New-Object IO.MemoryStream(, $Data)
    $gzip = New-Object IO.Compression.GzipStream($source, [IO.Compression.CompressionMode]::Decompress)
    $output = New-Object IO.MemoryStream
    try {
        $gzip.CopyTo($output)
        return , $output.ToArray()
    }
    finally {
        $gzip.Dispose(); $source.Dispose(); $output.Dispose()
    }
}

function Get-Sha256Hex {
    param([byte[]] $Data)
    # An empty payload file is legitimate (an empty __init__.py, say), and its
    # digest still has to be checked - so $null is coerced to an empty array
    # rather than reaching ComputeHash, where it matches both the byte[] and
    # Stream overloads and throws.
    if ($null -eq $Data) { $Data = New-Object byte[] 0 }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        # BitConverter, not a per-byte pipeline: the same reason as Get-Bytes.
        return [BitConverter]::ToString($sha.ComputeHash($Data)).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Invoke-Checked {
    param([string] $Exe, [string[]] $Arguments, [string] $What)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Die "$What failed (exit $LASTEXITCODE)" }
}


# ------------------------------------------------------------------ arguments

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    Die @"
Set AUTODARTS_BASE_URL to your update URL, e.g.

    `$env:AUTODARTS_BASE_URL = 'https://darren-darts.github.io/autodarts-updates'
    irm `$env:AUTODARTS_BASE_URL/installers/install-windows.ps1 | iex
"@
}
if ([string]::IsNullOrWhiteSpace($Channel)) { $Channel = 'stable' }
if ([string]::IsNullOrWhiteSpace($Target))  { $Target = Join-Path $env:LOCALAPPDATA 'Autodarts' }

$BaseUrl = $BaseUrl.TrimEnd('/')
$Runtime = Join-Path $Target 'runtime'

Say "Installing Autodarts to $Target"

# ------------------------------------------------------------------- layout

foreach ($name in 'config', 'staging', 'logs') {
    New-Item -ItemType Directory -Force -Path (Join-Path $Target $name) | Out-Null
}
[IO.File]::WriteAllText(
    (Join-Path $Target '.autodarts-root'),
    "This file marks an installed Autodarts tree. Do not delete it.`r`n"
)

# ------------------------------------------------------- fetch the payload
# Pulled through the *same* signed manifest the in-app updater uses, rather
# than a separately built artefact. One publishing path means a fresh install
# and an updated install of the same version are byte-for-byte identical.
#
# Signature verification happens in the app, which has the trusted public keys
# compiled in. Here every file is checked against the hash the manifest
# declares, which catches a truncated or corrupted download - the same posture
# install-pi.sh takes.

Say "Fetching the current $Channel release"

try {
    $channelInfo = Get-Text "$BaseUrl/channels/$Channel.json" | ConvertFrom-Json
}
catch {
    Die "Could not reach $BaseUrl - check the URL and that it is publicly readable.`n         $($_.Exception.Message)"
}
$version = $channelInfo.version
Info "version $version"

$manifest = Get-Text "$BaseUrl/releases/$version/manifest.json" | ConvertFrom-Json

$incoming = Join-Path $Target 'app.new'
if (Test-Path $incoming) { Remove-Item $incoming -Recurse -Force }
New-Item -ItemType Directory -Force -Path $incoming | Out-Null

$total = $manifest.files.Count
$index = 0
foreach ($entry in $manifest.files) {
    $index++
    $relative = [string] $entry.path

    # A manifest is fetched over the network, so its paths are not trusted to
    # stay inside the target: an absolute path or a `..` component would write
    # anywhere on the disk.
    if ($relative -match '^[\\/~]' -or $relative -match '(^|/)\.\.(/|$)' -or $relative.Contains('\') -or $relative -match '^[A-Za-z]:') {
        Die "refusing unsafe path in manifest: $relative"
    }

    $digest = [string] $entry.sha256
    $data = Expand-Blob (Get-Bytes "$BaseUrl/blobs/$($digest.Substring(0,2))/$digest.gz")
    if ((Get-Sha256Hex $data) -ne $digest) { Die "checksum mismatch for $relative" }

    $destination = Join-Path $incoming ($relative -replace '/', '\')
    New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
    [IO.File]::WriteAllBytes($destination, $data)

    Write-Host "`r  $index/$total files" -NoNewline
}
Write-Host ''

$previous = Join-Path $Target 'app.previous'
$appDir = Join-Path $Target 'app'
if (Test-Path $previous) { Remove-Item $previous -Recurse -Force }
if (Test-Path $appDir)   { Move-Item $appDir $previous }
Move-Item $incoming $appDir
if (Test-Path $previous) { Remove-Item $previous -Recurse -Force }

$requirements = Join-Path $appDir 'backend\requirements.txt'
if (-not (Test-Path $requirements)) { Die "the release payload has no backend\requirements.txt" }


# ------------------------------------------------------------------ runtime

function Find-SystemPython {
    <#
        An absolute python.exe of at least $MinimumPython, or $null.

        Returns sys.executable rather than the command name, so everything
        afterwards is pinned to the interpreter that was actually probed - `py`
        can resolve differently once PATH changes.
    #>
    $candidates = @(
        @{ Exe = 'py';      Arguments = @('-3') },
        @{ Exe = 'python';  Arguments = @() },
        @{ Exe = 'python3'; Arguments = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if (-not $command) { continue }

        # Windows ships stub executables under WindowsApps that do nothing but
        # open the Microsoft Store. Probing one pops the Store over the top of
        # the install, so they are skipped by location.
        if ($command.Source -and $command.Source -like '*\WindowsApps\*') {
            Info "ignoring the Microsoft Store stub at $($command.Source)"
            continue
        }

        # No quotes of any kind inside the -c snippet. PowerShell 5.1 mangles
        # embedded double quotes when handing an argument to a native
        # executable, which silently turned this probe into a SyntaxError - so
        # every real Python looked absent and the embeddable interpreter was
        # downloaded onto machines that already had one. platform.python_version()
        # avoids needing a format string at all.
        $probe = @($candidate.Arguments) + @('-c', 'import sys, platform; print(platform.python_version()); print(sys.executable)')
        try   { $output = & $candidate.Exe @probe }
        catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $output -or @($output).Count -lt 2) { continue }

        # A pre-release interpreter reports e.g. "3.14.0rc1", which [Version]
        # cannot parse - so only the numeric leading part is compared.
        $reported = (@($output)[0]).Trim()
        $numeric = [regex]::Match($reported, '^\d+(\.\d+)*').Value
        if (-not $numeric) { continue }
        $found = [Version] $numeric
        $executable = (@($output)[1]).Trim()
        if ($found -lt $MinimumPython) {
            Info "ignoring Python $found at $executable (need $MinimumPython or newer)"
            continue
        }
        if (-not (Test-Path $executable)) { continue }
        Info "found Python $found at $executable"
        return $executable
    }
    return $null
}

function Install-VenvRuntime {
    <# The Pi's approach: a venv from the machine's own Python. #>
    param([string] $SystemPython)

    $python = Join-Path $Runtime 'Scripts\python.exe'
    if (-not (Test-Path $python)) {
        if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
        Invoke-Checked $SystemPython @('-m', 'venv', $Runtime) 'creating the virtual environment'
    }
    if (-not (Test-Path $python)) { Die "the virtual environment has no $python" }

    Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet') 'upgrading pip'
    Invoke-Checked $python @('-m', 'pip', 'install', '-r', $requirements, '--quiet') 'installing dependencies'
    return $python
}

function Install-EmbeddedRuntime {
    <#
        No Python on the machine, so ship one.

        The embeddable distribution is a complete, relocatable interpreter meant
        for exactly this. Two adjustments make it usable, both of which
        tools/build_runtime.py needed for the same reason:

          * pythonXY._pth disables site-packages, so pip installs succeed and
            then nothing can be imported.
          * pip is absent and has to be bootstrapped once.

        A venv is deliberately NOT built on top of it - the embeddable
        distribution has neither venv nor ensurepip.
    #>
    $python = Join-Path $Runtime 'python.exe'

    # An existing embeddable runtime is reused, so a re-run only has to install
    # whatever requirements.txt now asks for rather than re-downloading the
    # interpreter and every wheel.
    if (Test-Path $python) {
        Info 'reusing the existing interpreter'
        Invoke-Checked $python @(
            '-m', 'pip', 'install', '--no-warn-script-location',
            '--only-binary', ':all:', '-r', $requirements, '--quiet'
        ) 'installing dependencies'
        return $python
    }

    $temp = Join-Path ([IO.Path]::GetTempPath()) "autodarts-runtime-$PID"
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        Info "no Python found - fetching the embeddable interpreter $PythonVersion"
        $archive = Join-Path $temp 'python-embed.zip'
        [IO.File]::WriteAllBytes($archive, (Get-Bytes $EmbedUrl))

        if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
        Expand-Archive -Path $archive -DestinationPath $Runtime -Force

        foreach ($pth in Get-ChildItem (Join-Path $Runtime 'python*._pth')) {
            $text = [IO.File]::ReadAllText($pth.FullName)
            $text = $text.Replace('#import site', 'import site')
            if ($text -notlike '*Lib\site-packages*') { $text += "Lib\site-packages`r`n" }
            [IO.File]::WriteAllText($pth.FullName, $text)
        }

        Info 'bootstrapping pip'
        $getPip = Join-Path $temp 'get-pip.py'
        [IO.File]::WriteAllBytes($getPip, (Get-Bytes $GetPipUrl))
        Invoke-Checked $python @($getPip, '--no-warn-script-location', '--quiet') 'bootstrapping pip'

        Info 'installing dependencies'
        Invoke-Checked $python @(
            '-m', 'pip', 'install', '--no-warn-script-location',
            '--only-binary', ':all:', '-r', $requirements, '--quiet'
        ) 'installing dependencies'

        return $python
    }
    finally {
        Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Say 'Building the Python environment'
$systemPython = Find-SystemPython
if ($systemPython) { $runtimePython = Install-VenvRuntime $systemPython }
else               { $runtimePython = Install-EmbeddedRuntime }

# What the runtime provides, so the updater can gate on it: `min_runtime` in a
# release manifest is compared against `version` here. It is what stops an
# update needing a new pip dependency from being applied onto a runtime that
# does not have it (see backend/update/client.py runtime_version).
$runtimeReport = (& $runtimePython @('-c', 'import platform; print(platform.python_version())') | Select-Object -First 1)
$requirementsHash = Get-Sha256Hex ([IO.File]::ReadAllBytes($requirements))
[IO.File]::WriteAllText(
    (Join-Path $Runtime 'RUNTIME.json'),
    (@{
        version = '1.0.0'
        python = [string] $runtimeReport
        platform = 'win-x64'
        requirements_sha256 = $requirementsHash
    } | ConvertTo-Json)
)
Info "runtime ready: Python $runtimeReport"

# ----------------------------------------------------------------- launcher
# launcher.py sits outside app\ because it is the process that swaps app\
# during an update and so cannot be part of what is swapped. That also means an
# in-app update can never replace it, which is why a copy lives beside the
# releases for a re-run of this script to pick up.

Say 'Installing the launcher'
[IO.File]::WriteAllBytes((Join-Path $Target 'launcher.py'), (Get-Bytes "$BaseUrl/installers/launcher.py"))

# ---------------------------------------------------------------- shortcuts
# pythonw.exe, not python.exe: the launcher is a background process, and a
# console window sitting behind the browser looks broken to a non-technical
# user, who will eventually close it and stop the app mid-game.

Say 'Creating shortcuts'
$pythonw = Join-Path (Split-Path $runtimePython -Parent) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $runtimePython }
$icon = Join-Path $Target 'app\frontend\dist\favicon.ico'
$launcher = Join-Path $Target 'launcher.py'

$shell = New-Object -ComObject WScript.Shell
foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    if (-not $directory) { continue }
    $path = Join-Path $directory 'Autodarts.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = """$launcher"""
    $link.WorkingDirectory = $Target
    $link.Description = 'Autodarts'
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Save()
    Info $path
}

Say "Installed Autodarts $version"
Info 'start:   the Autodarts shortcut on your desktop'
Info "logs:    $Target\logs"
Info 'open:    http://localhost:8000'
Info ''
Info 'Updates from here on are done in the app itself, on the Updates page.'
