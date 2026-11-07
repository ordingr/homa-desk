<#
Creates "Assistant.lnk" on the current user's Desktop. It starts Assistant without a console window.
Run:  powershell -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $candidate = if ($python) { Join-Path (Split-Path -Parent $python) 'pythonw.exe' } else { $null }
    if ($candidate -and (Test-Path $candidate)) { $pythonw = $candidate }
    else { throw "pythonw.exe not found (looked in $repo\.venv\Scripts and on PATH)." }
}

$lnkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Assistant.lnk'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = '-m assistant'
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = (Join-Path $repo 'assistant\assets\assistant.ico') + ',0'
$lnk.Description = 'Assistant ' + [char]0x2014 + ' alerts and file search'
$lnk.Save()
Write-Host "Created $lnkPath"
