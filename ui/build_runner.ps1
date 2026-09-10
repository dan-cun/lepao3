# Copyright (c) 2026 dan-cun. License: see LICENSE in repository root.
# LepaoRunner build script (PyInstaller onefile). ASCII only (codepage safe).
# Usage: powershell -ExecutionPolicy Bypass -File build_runner.ps1
$ErrorActionPreference = "Stop"
$UI = Split-Path -Parent $MyInvocation.MyCommand.Path
$PKG = Split-Path -Parent $UI
$ROOT = Split-Path -Parent $PKG
Set-Location $UI

Write-Host "[1/4] make icon ..."
py -3 make_icon.py
$icon = Join-Path $UI "icons\lepao.ico"

Write-Host "[2/4] locate bundle assets ..."
$addon = Get-ChildItem -Path $ROOT -Filter "mitm_lptiyu_token.py" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $addon) { throw "mitm addon not found under $ROOT" }
$template = Get-ChildItem -Path (Join-Path $PKG "data") -Filter "*035*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $template) { throw "track template not found under $PKG\data" }
$sample = Join-Path $PKG "config.sample.json"
$bundle = Join-Path $UI "bundle"
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
$bAddon = Join-Path $bundle "mitm_addon.py"
Copy-Item $addon.FullName $bAddon -Force
# License assets (LRL-1.0): bundle declaration files so the frozen exe can
# verify the shipped LICENSE hash and display attribution (LICENSE art.2/3).
$assets = @("LICENSE", "NOTICE") | ForEach-Object { Join-Path $PKG $_ }
foreach ($a in $assets) { if (-not (Test-Path $a)) { throw ("missing license asset: " + $a) } }

Write-Host "[3/4] PyInstaller onefile (1-3 min) ..."
$datas = @(
  ($sample + ";."),
  ($template.FullName + ";data"),
  ($bAddon + ";.")
)
foreach ($a in $assets) { $datas += ($a + ";.") }
$args_list = @(
  "--noconfirm", "--clean", "--onefile", "--windowed",
  "--name", "LepaoRunner",
  "--paths", $PKG,
  "--icon", $icon
)
foreach ($d in $datas) { $args_list += @("--add-data", $d) }
$args_list += (Join-Path $UI "runner_ui.py")
# Native stderr (PyInstaller INFO logs) must not be treated as errors.
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
py -3 -m PyInstaller @args_list 2>&1 | ForEach-Object { Write-Host "$_" }
$rc = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($rc -ne 0) { throw ("PyInstaller failed: " + $rc) }

Write-Host "[4/4] DONE"
Write-Host ("  EXE: " + (Join-Path $UI "dist\LepaoRunner.exe"))
Write-Host "  double click to run; rename the file freely. Portable data dir = <exe dir>\runtime"
Write-Host "  release hygiene: ship LICENSE/NOTICE/DISCLAIMER.md next to the exe (LRL-1.0 art.2)."
