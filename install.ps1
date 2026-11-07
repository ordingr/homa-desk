<#
Installs Assistant for the current user. No elevation, no service, no executable.
Run:  powershell -ExecutionPolicy Bypass -File install.ps1 [-Hooks] [-NoStart] [-NoShortcut]
#>
param(
    [switch]$Hooks,
    [switch]$NoStart,
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Python {
    foreach ($candidate in @(@('py', '-3'), @('python'))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        $out = & $exe @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return [string]$out }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host 'Python 3.10 or newer was not found on PATH.'
    Write-Host 'Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this script again.'
    exit 1
}
Write-Host "Using $python"

Push-Location $repo
try {
    & $python -m pip install --user --only-binary :all: -r (Join-Path $repo 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Host 'pip install failed; see the output above.'; exit 1 }

    & $python -m assistant --init
    if ($LASTEXITCODE -ne 0) { Write-Host 'Initialising the Assistant folder failed.'; exit 1 }
    & $python -m assistant --doctor

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($Hooks -or $claude) {
        $install = $Hooks
        if (-not $Hooks) {
            $answer = Read-Host 'Claude Code found. Show the hook entries Assistant would add to its settings? [y/N]'
            $install = ($answer -match '^[yY]')
            if (-not $install) { Write-Host 'Skipped. Later: python -m assistant hooks install' }
        }
        if ($install) {
            & $python -m assistant hooks install --dry-run
            $confirm = Read-Host 'Add these entries to ~/.claude/settings.json (a backup is written first)? [y/N]'
            if ($confirm -match '^[yY]') { & $python -m assistant hooks install }
        }
    }

    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = $python }
    $startup = [Environment]::GetFolderPath('Startup')
    $lnkPath = Join-Path $startup 'Assistant.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '-m assistant'
    $lnk.WorkingDirectory = $repo
    $lnk.Description = 'Assistant'
    $lnk.Save()

    if (-not $NoShortcut) {
        & (Join-Path $repo 'scripts\install-shortcut.ps1')
    }

    if (-not $NoStart) {
        Start-Process -FilePath $pythonw -ArgumentList '-m', 'assistant' -WorkingDirectory $repo
        Write-Host 'Assistant started. Look for the round icon at the bottom-right.'
    }
}
finally {
    Pop-Location
}

$homeDir = if ($env:ASSISTANT_HOME) { $env:ASSISTANT_HOME } else { Join-Path $env:USERPROFILE '.assistant' }
Write-Host ''
Write-Host "Settings, token, logs:  $homeDir"
Write-Host "Starts at login via:    $lnkPath"
Write-Host 'Remove everything with: powershell -ExecutionPolicy Bypass -File uninstall.ps1'
