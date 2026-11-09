<#
Rasterises homa/assets/icon.svg and icon-muted.svg into the PNG tiles, badge PNGs and homa.ico the
overlay loads at runtime. Needs node, Microsoft Edge and playwright-core (a node_modules folder that has it:
set PLAYWRIGHT_MODULES, else ~/node_modules). Run when the SVGs change and commit the output:
  powershell -ExecutionPolicy Bypass -File scripts\render-icons.ps1
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$assets = Join-Path $repo 'homa\assets'
$modules = if ($env:PLAYWRIGHT_MODULES) { $env:PLAYWRIGHT_MODULES } else { Join-Path $env:USERPROFILE 'node_modules' }
if (-not (Test-Path (Join-Path $modules 'playwright-core'))) { throw "playwright-core not found in $modules" }
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }

$work = Join-Path ([IO.Path]::GetTempPath()) ('homa-icons-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$driver = Join-Path $work 'render.cjs'
@'
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const [assets, work] = process.argv.slice(2);
const BASE = 60;
const SCALES = [1.0, 1.25, 1.5, 1.75, 2.0, 3.0];
const ICO = [16, 24, 32, 48, 64, 256];
const RED = "#E5352B";
const svgUrl = (name) => "data:image/svg+xml;base64," + fs.readFileSync(path.join(assets, name)).toString("base64");

async function shot(page, html, size, file, transparent) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<html><body style="margin:0;background:transparent">${html}</body></html>`);
  await page.waitForLoadState("load");
  await page.screenshot({ path: file, omitBackground: transparent,
                          clip: { x: 0, y: 0, width: size, height: size } });
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const s of SCALES) {
    const px = Math.round(BASE * s);
    for (const name of ["icon", "icon-muted"]) {
      const img = `<img src="${svgUrl(name + ".svg")}" width="${px}" height="${px}" style="display:block">`;
      await shot(page, img, px, path.join(assets, `${name}_${px}.png`), false);
    }
    const b = Math.round(px * 0.42);
    const ring = Math.max(2, Math.round(b * 0.09));
    for (const key of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "plus"]) {
      const text = key === "plus" ? "9+" : key;
      const font = Math.round(b * (text.length > 1 ? 0.46 : 0.6));
      const html = `<div style="box-sizing:border-box;width:${b}px;height:${b}px;border-radius:50%;background:${RED};` +
        `border:${ring}px solid #fff;color:#fff;font:700 ${font}px 'Segoe UI',Arial,sans-serif;` +
        `display:flex;align-items:center;justify-content:center;padding-bottom:${Math.round(b * 0.04)}px">${text}</div>`;
      await shot(page, html, b, path.join(assets, `badge_${key}_${px}.png`), true);
    }
  }
  for (const px of ICO) {
    const html = `<div style="width:${px}px;height:${px}px;border-radius:${Math.round(px * 0.22)}px;overflow:hidden">` +
      `<img src="${svgUrl("icon.svg")}" width="${px}" height="${px}" style="display:block"></div>`;
    await shot(page, html, px, path.join(work, `ico_${px}.png`), true);
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
'@ | Set-Content -Path $driver -Encoding UTF8

try {
    $env:NODE_PATH = $modules
    & node $driver $assets $work
    if ($LASTEXITCODE -ne 0) { throw 'node render failed' }
    $frames = @(16, 24, 32, 48, 64, 256) | ForEach-Object { Join-Path $work "ico_$_.png" }
    & $python (Join-Path $repo 'scripts\pack_ico.py') (Join-Path $assets 'homa.ico') @frames
    if ($LASTEXITCODE -ne 0) { throw 'ico packing failed' }
    Write-Host "Wrote icons to $assets"
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
