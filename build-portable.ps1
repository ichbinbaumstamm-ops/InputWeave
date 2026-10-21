[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*\.exe$')]
    [string]$OutputFileName = 'InputWeave-Test.exe'
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$RepositoryOutputDirectory = (Split-Path -Parent $ProjectRoot)
if ($OutputFileName -ceq 'InputWeave-Test.exe') {
    $OutputDirectory = [Environment]::GetFolderPath('DesktopDirectory')
    if ([string]::IsNullOrWhiteSpace($OutputDirectory) -or -not (Test-Path -LiteralPath $OutputDirectory)) {
        throw 'Could not resolve the current Windows Desktop folder for the local test build.'
    }
} else {
    $OutputDirectory = $RepositoryOutputDirectory
}
$OutputExe = [System.IO.Path]::GetFullPath((Join-Path $OutputDirectory $OutputFileName))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build'))
$StageRoot = [System.IO.Path]::GetFullPath((Join-Path $BuildRoot 'single-exe-stage'))
$TestBuildBackup = Join-Path $BuildRoot 'InputWeave-Test.previous'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Resources = Join-Path $ProjectRoot 'resources'

if ((Split-Path -Parent $OutputExe) -ne [System.IO.Path]::GetFullPath($OutputDirectory)) {
    throw "Unexpected output path: $OutputExe"
}
if ((Test-Path -LiteralPath $OutputExe) -and $OutputFileName -cne 'InputWeave-Test.exe') {
    throw "Output already exists. Move or rename it before rebuilding: $OutputExe"
}
if (-not (Test-Path -LiteralPath (Join-Path $Resources 'ViGEmBus_1.22.0_x64_x86_arm64.exe'))) {
    throw 'The bundled ViGEmBus installer is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Resources 'ViGEmBus-LICENSE.txt'))) {
    throw 'The bundled ViGEmBus license notice is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Resources 'HidHide_1.5.230_x64.exe'))) {
    throw 'The bundled HidHide installer is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Resources 'HidHide-LICENSE.txt'))) {
    throw 'The bundled HidHide license notice is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Resources 'vgamepad-LICENSE.txt'))) {
    throw 'The bundled vgamepad license notice is missing.'
}
$ViGEmHash = (Get-FileHash -LiteralPath (Join-Path $Resources 'ViGEmBus_1.22.0_x64_x86_arm64.exe') -Algorithm SHA256).Hash
if ($ViGEmHash -ne '89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A') {
    throw 'The bundled ViGEmBus installer does not match the verified official release asset.'
}
$ViGEmSignature = Get-AuthenticodeSignature -LiteralPath (Join-Path $Resources 'ViGEmBus_1.22.0_x64_x86_arm64.exe')
if ($ViGEmSignature.Status -ne 'Valid' -or $ViGEmSignature.SignerCertificate.Subject -notmatch 'Nefarius Software Solutions') {
    throw 'The bundled ViGEmBus installer signature is not valid or has an unexpected publisher.'
}
$HidHideHash = (Get-FileHash -LiteralPath (Join-Path $Resources 'HidHide_1.5.230_x64.exe') -Algorithm SHA256).Hash
if ($HidHideHash -ne 'F4BBBCB82E6258641B887C74BC81C4C5F66E4AA811808DFC304347687B7605F6') {
    throw 'The bundled HidHide installer does not match the verified official release asset.'
}
$HidHideSignature = Get-AuthenticodeSignature -LiteralPath (Join-Path $Resources 'HidHide_1.5.230_x64.exe')
if ($HidHideSignature.Status -ne 'Valid' -or $HidHideSignature.SignerCertificate.Subject -notmatch 'Nefarius Software Solutions') {
    throw 'The bundled HidHide installer signature is not valid or has an unexpected publisher.'
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw 'Python 3.11+ is required to build the portable app.' }
    $PythonVersion = & $PythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$PythonVersion -lt [version]'3.11') { throw "Python 3.11+ is required; found $PythonVersion." }
    & $PythonCommand.Source -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.11 build environment.' }
}

# vgamepad's setup.py can launch its own driver installer. InputWeave handles the
# signed driver installer on first run after checking its service and checksum.
$env:VGAMEPAD_SKIP_VIGEMBUS_INSTALL = 'true'
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip.' }
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned app dependencies.' }
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the build dependency.' }

# Never replace the local test EXE with source that fails its non-UI regression
# suite.  This covers profile persistence and mapping-dialog construction guards.
$PreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $ProjectRoot 'src'
    & $VenvPython -m unittest discover -s (Join-Path $ProjectRoot 'tests') -v
    if ($LASTEXITCODE -ne 0) { throw 'The InputWeave regression tests failed.' }
}
finally {
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}

if (Test-Path -LiteralPath $BuildRoot) {
    $resolvedBuildRoot = (Resolve-Path -LiteralPath $BuildRoot).Path
    if ($resolvedBuildRoot -ne $BuildRoot) { throw "Unexpected build path: $resolvedBuildRoot" }
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildRoot | Out-Null

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name InputWeave `
    --icon (Join-Path $Resources 'inputweave.ico') `
    --paths (Join-Path $ProjectRoot 'src') `
    --distpath $StageRoot `
    --workpath (Join-Path $BuildRoot 'pyinstaller-work') `
    --specpath $BuildRoot `
    --add-data "$(Join-Path $Resources 'ViGEmBus_1.22.0_x64_x86_arm64.exe');resources" `
    --add-data "$(Join-Path $Resources 'ViGEmBus-LICENSE.txt');resources" `
    --add-data "$(Join-Path $Resources 'HidHide_1.5.230_x64.exe');resources" `
    --add-data "$(Join-Path $Resources 'HidHide-LICENSE.txt');resources" `
    --add-data "$(Join-Path $Resources 'vgamepad-LICENSE.txt');resources" `
    --add-data "$(Join-Path $Resources 'inputweave.ico');resources" `
    --add-data "$(Join-Path $Resources 'inputweave-logo.png');resources" `
    --collect-submodules vgamepad.win `
    --collect-binaries vgamepad.win `
    --exclude-module vgamepad.lin `
    --hidden-import pygame._sdl2.controller `
    (Join-Path $ProjectRoot 'src\inputweave\main.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller did not finish successfully.' }

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$ReplacementFile = "$OutputExe.new"
try {
    Copy-Item -LiteralPath (Join-Path $StageRoot 'InputWeave.exe') -Destination $ReplacementFile -Force
    if (Test-Path -LiteralPath $OutputExe) {
        # Replace only the single local test build, and only after PyInstaller
        # completed. If Windows has the running EXE locked, the old build stays.
        [System.IO.File]::Replace($ReplacementFile, $OutputExe, $TestBuildBackup)
        Remove-Item -LiteralPath $TestBuildBackup -Force
    } else {
        Move-Item -LiteralPath $ReplacementFile -Destination $OutputExe
    }
} finally {
    if (Test-Path -LiteralPath $ReplacementFile) {
        Remove-Item -LiteralPath $ReplacementFile -Force
    }
}

Write-Host "Standalone app: $OutputExe"
