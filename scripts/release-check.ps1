[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Stop-ReleaseCheck([string]$Message) {
    Write-Error "RELEASE BLOCKED: $Message"
    exit 1
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCommand) {
    Stop-ReleaseCheck 'Git is not installed or is not on PATH.'
}

$gitleaksCommand = Get-Command gitleaks -ErrorAction SilentlyContinue
if (-not $gitleaksCommand) {
    Stop-ReleaseCheck 'Gitleaks is not installed or is not on PATH. Install the Windows release from https://github.com/gitleaks/gitleaks/releases and rerun this check.'
}

# Gitleaks launches Git as a child process. Make the resolved Git directory
# explicit so this also works in restricted shells that expose Git only through
# PowerShell command discovery.
$gitDirectory = Split-Path -Parent $gitCommand.Source
$env:Path = "$gitDirectory;$env:Path"

$repoRoot = git rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    Stop-ReleaseCheck 'Run this script from inside the InputWeave Git repository.'
}

Set-Location -LiteralPath $repoRoot
$readmePath = Join-Path $repoRoot 'README.md'
$configPath = Join-Path $repoRoot '.gitleaks.toml'

foreach ($requiredPath in @($readmePath, $configPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        Stop-ReleaseCheck "Required file is missing: $([IO.Path]::GetFileName($requiredPath))"
    }
}

$readme = Get-Content -LiteralPath $readmePath -Raw
$requiredSections = @('## Single-file build', '## Run from source', '## Use', '## Development', '## Release security check')
$missingSections = @($requiredSections | Where-Object { $readme -notmatch [regex]::Escape($_) })
if ($missingSections.Count -gt 0) {
    Stop-ReleaseCheck "README.md is missing required sections: $($missingSections -join ', ')."
}

$requiredCommands = @('python -m pip install -r requirements.txt', 'python src/inputweave/main.py', './scripts/release-check.ps1')
$missingCommands = @($requiredCommands | Where-Object { $readme -notmatch [regex]::Escape($_) })
if ($missingCommands.Count -gt 0) {
    Stop-ReleaseCheck "README.md is missing setup or release commands: $($missingCommands -join ', ')."
}

if ($readme -match '(?im)^\s*(TODO|TBD|REPLACE_ME|PROJECT NAME)\b') {
    Stop-ReleaseCheck 'README.md contains an unresolved template placeholder.'
}

$scanRoot = Join-Path ([IO.Path]::GetTempPath()) "inputweave-release-scan-$([guid]::NewGuid().ToString('N'))"
try {
    New-Item -ItemType Directory -Path $scanRoot | Out-Null
    $candidateFiles = @(git -C $repoRoot ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        Stop-ReleaseCheck 'Git could not enumerate tracked and non-ignored files for scanning.'
    }

    foreach ($relativePath in $candidateFiles) {
        if (-not $relativePath) {
            continue
        }

        $sourcePath = Join-Path $repoRoot $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            continue
        }

        $targetPath = Join-Path $scanRoot $relativePath
        $targetDirectory = Split-Path -Parent $targetPath
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
    }

    Write-Host 'Scanning tracked and non-ignored files, including untracked project files...'
    & $gitleaksCommand.Source dir --redact --config $configPath $scanRoot
    if ($LASTEXITCODE -ne 0) {
        Stop-ReleaseCheck 'The file scan found a secret or failed. Review findings locally; do not paste secret values into chat.'
    }
}
finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $scanRootFull = [IO.Path]::GetFullPath($scanRoot)
    if ($scanRootFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $scanRootFull)) {
        Remove-Item -LiteralPath $scanRootFull -Recurse -Force
    }
}

$head = git rev-parse --verify HEAD 2>$null
if ($LASTEXITCODE -eq 0 -and $head) {
    Write-Host 'Scanning Git history...'
    & $gitleaksCommand.Source git --redact --config $configPath $repoRoot
    if ($LASTEXITCODE -ne 0) {
        Stop-ReleaseCheck 'The Git history scan found a secret or failed. Review findings locally; do not paste secret values into chat.'
    }
}
else {
    Write-Host 'No Git commit exists yet; history scan will run after the first commit.' -ForegroundColor Yellow
}

Write-Host 'PASS: README and available secret scans completed. Before pushing, commit the reviewed changes and rerun this check so Git history is scanned.' -ForegroundColor Green
exit 0
