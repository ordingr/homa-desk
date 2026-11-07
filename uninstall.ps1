<#
Stops Assistant, removes its login shortcut and its Claude Code hook entries,
and asks before deleting the Assistant folder.
Run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1
#>
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$homeDir = if ($env:ASSISTANT_HOME) { $env:ASSISTANT_HOME } else { Join-Path $env:USERPROFILE '.assistant' }

$python = $null
foreach ($name in @('python', 'py')) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $python = $name; break }
}
if (-not $python) { Write-Host 'Python was not found on PATH; only the shortcut can be removed.' }

Push-Location $repo
try {
    if ($python) {
        & $python -m assistant shutdown
        if ($LASTEXITCODE -eq 2) {
            $pidFile = Join-Path $homeDir 'assistant.pid'
            if (Test-Path $pidFile) {
                $recorded = [int](Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
                if ($recorded) { Stop-Process -Id $recorded -ErrorAction SilentlyContinue }
            }
        }
        & $python -m assistant hooks remove
    }
}
finally {
    Pop-Location
}

$lnkPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Assistant.lnk'
if (Test-Path $lnkPath) {
    Remove-Item -LiteralPath $lnkPath
    Write-Host "Removed $lnkPath"
}

if (Test-Path $homeDir) {
    $answer = Read-Host "Delete $homeDir (settings, index, plugins, logs)? [y/N]"
    if ($answer -match '^[yY]') {
        Remove-Item -LiteralPath $homeDir -Recurse -Force
        Write-Host "Deleted $homeDir"
    }
    else {
        Write-Host "Kept $homeDir"
    }
}
