<#
.SYNOPSIS
  Builds the full-page web UI from web/ into a folder.
.DESCRIPTION
  Runs `npm ci && npm run build` in web/ and writes the bundle to -OutDir
  (default: assistant/static/app under the repo). Node is only needed here,
  at build time; the Assistant runtime serves the result as static files.
.EXAMPLE
  .\scripts\build-web.ps1 -OutDir $env:TEMP\app
#>
param(
  [string]$OutDir
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$web = Join-Path $repo "web"
if (-not $OutDir) { $OutDir = Join-Path $repo "assistant\static\app" }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)

Push-Location $web
try {
  npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed ($LASTEXITCODE)" }
  $env:ASSISTANT_WEB_OUT = $OutDir
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
}
finally {
  Pop-Location
}

Write-Host "Built web UI to $OutDir"
