<#
  build.ps1 -- one command: compile BaModProbe and drop it into <GameDir>\Mods\.

  Usage
      powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -GameDir "D:\...\broken_arrow"
      powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -NoCopy      # compile only
      powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Configuration Debug

  Game directory resolution (first hit wins)
      1. -GameDir parameter
      2. $env:BA_MOD_MAKER_GAME
      3. D:\Steam\steamapps\common\broken_arrow

  Safety: this script writes exactly two things --
      * the build output under tools\probe_mod\bin\
      * <GameDir>\Mods\BaModProbe.dll
  It never touches Mods\Maps, Mods\Scenarios or Mods\Units (the game creates those itself).

  ASCII-only on purpose: Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI.
#>
[CmdletBinding()]
param(
  [string]$GameDir,
  [ValidateSet('Release', 'Debug')][string]$Configuration = 'Release',
  [switch]$NoCopy,
  [switch]$SkipColumnCheck,
  [switch]$SkipCastCheck
)
$ErrorActionPreference = 'Stop'

$Proj  = $PSScriptRoot
$Tools = Split-Path $Proj -Parent
$Repo  = Split-Path $Tools -Parent

if (-not $GameDir) { $GameDir = $env:BA_MOD_MAKER_GAME }
if (-not $GameDir) { $GameDir = 'D:\Steam\steamapps\common\broken_arrow' }
$GameDir = (Resolve-Path -LiteralPath $GameDir).Path

Write-Host "== BaModProbe build ==" -ForegroundColor Cyan
Write-Host "   game dir : $GameDir"
Write-Host "   project  : $Proj"

$net6  = Join-Path $GameDir 'MelonLoader\net6'
$proxy = Join-Path $GameDir 'MelonLoader\Il2CppAssemblies'

# ---- preflight: every reference must exist before we blame the compiler -------------
$missing = @()
foreach ($f in 'MelonLoader.dll', '0Harmony.dll', 'Il2CppInterop.Runtime.dll') {
  if (-not (Test-Path (Join-Path $net6 $f))) { $missing += (Join-Path $net6 $f) }
}
if (-not $missing.Contains((Join-Path $GameDir 'MelonLoader\Il2CppAssemblies\Il2CppBrokenArrow.dll'))) {
  if (-not (Test-Path (Join-Path $proxy 'Il2CppBrokenArrow.dll'))) {
    $missing += (Join-Path $proxy 'Il2CppBrokenArrow.dll')
  }
}
if ($missing.Count -gt 0) {
  Write-Host "PREFLIGHT FAILED -- missing reference assemblies:" -ForegroundColor Red
  foreach ($m in $missing) { Write-Host "   $m" -ForegroundColor Red }
  Write-Host ""
  Write-Host "If the proxy assemblies are absent, launch the game once through MelonLoader." -ForegroundColor Yellow
  Write-Host "MelonLoader's Il2CppAssemblyGenerator writes them into:" -ForegroundColor Yellow
  Write-Host "   $proxy" -ForegroundColor Yellow
  exit 2
}
$nProxy = (Get-ChildItem $proxy -Filter *.dll | Measure-Object).Count
Write-Host "   proxies  : $nProxy dll in $proxy"

# ---- column contract: keep _work/columns.json in step with DataBaseCompiled --------
$columnsJson = Join-Path $Proj '_work\columns.json'
if (-not $SkipColumnCheck) {
  $checker = Join-Path $Proj 'tools\check_columns.py'
  $py = Join-Path $Repo '.venv\Scripts\python.exe'
  if ((Test-Path $checker) -and (Test-Path $py)) {
    Write-Host "== column contract check (tools/check_columns.py) ==" -ForegroundColor Cyan
    & $py $checker
    if ($LASTEXITCODE -ne 0) {
      Write-Host "COLUMN CHECK FAILED -- the dump column set does not resolve against the row models." -ForegroundColor Red
      Write-Host "Re-run tools\dump_types.ps1, then tools\check_columns.py, and fix before shipping." -ForegroundColor Yellow
      exit 3
    }
  } else {
    Write-Host "   (skipped: check_columns.py or .venv python not found)" -ForegroundColor Yellow
  }
}

# ---- build -------------------------------------------------------------------------
Write-Host "== dotnet build ($Configuration) ==" -ForegroundColor Cyan
& dotnet build (Join-Path $Proj 'BaModProbe.csproj') -c $Configuration -p:GameDir="$GameDir" -v minimal
if ($LASTEXITCODE -ne 0) { Write-Host "BUILD FAILED (rc=$LASTEXITCODE)" -ForegroundColor Red; exit $LASTEXITCODE }

$dll = Join-Path $Proj ("bin\$Configuration\BaModProbe.dll")
if (-not (Test-Path $dll)) { Write-Host "build reported success but $dll is missing" -ForegroundColor Red; exit 4 }
$bytes = (Get-Item $dll).Length
Write-Host "   built    : $dll ($bytes bytes)"

# ---- IL gate: the freshly built assembly must not cast an Il2Cpp proxy collection to a
#      managed collection abstraction. This is the bug that made the first live run return
#      24/24 empty tables, and a compiler cannot catch it. Gate BEFORE deploying, and only
#      deploy if the ruler passes (its own positive control must go red first).
if (-not $SkipCastCheck) {
  Write-Host "== IL gate: no managed-collection cast on Il2Cpp proxies ==" -ForegroundColor Cyan
  $castRuler = Join-Path $Proj 'tools\check_no_il2cpp_ienumerable_cast.ps1'
  $castProbe = Join-Path $Proj 'tests\CastProbe\bin\Release\net6.0\CastProbe.dll'
  if (-not (Test-Path $castProbe)) {
    Write-Host "   building the positive-control assembly (tests/CastProbe)" -ForegroundColor Yellow
    & dotnet build (Join-Path $Proj 'tests\CastProbe\CastProbe.csproj') -c Release -v quiet | Out-Null
  }
  if (Test-Path $castRuler) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $castRuler -Target $dll
    if ($LASTEXITCODE -ne 0) {
      Write-Host "IL GATE FAILED -- NOT deploying. The dump would come back empty." -ForegroundColor Red
      exit 5
    }
  } else {
    Write-Host "   (skipped: $castRuler not found)" -ForegroundColor Yellow
  }
}

# ---- stage the runtime contract next to the mod ------------------------------------
if (-not $NoCopy) {
  $userDir = Join-Path $GameDir 'UserData\BaModProbe'
  New-Item -ItemType Directory -Force -Path $userDir | Out-Null
  if (Test-Path $columnsJson) {
    Copy-Item $columnsJson (Join-Path $userDir 'columns.json') -Force
    Write-Host "   columns  : copied to $(Join-Path $userDir 'columns.json')"
  } else {
    Write-Host "   columns  : _work\columns.json absent -- the probe will fall back to reflection" -ForegroundColor Yellow
  }

  # Watch list for probe.column_diagnostics. Created once; edit it freely afterwards.
  $diag = Join-Path $userDir 'diagnose_columns.txt'
  if (-not (Test-Path $diag)) {
    Set-Content -Path $diag -Encoding ASCII -Value @(
      '# Columns traced in full on every run: one "Table.Column" per line.',
      '# Turrets.ParentTurretId is always traced even if this file is empty.',
      'Turrets.ParentTurretId'
    )
    Write-Host "   diagnose : created $diag"
  }

  $modsDir = Join-Path $GameDir 'Mods'
  New-Item -ItemType Directory -Force -Path $modsDir | Out-Null
  Copy-Item $dll (Join-Path $modsDir 'BaModProbe.dll') -Force
  Write-Host "   deployed : $(Join-Path $modsDir 'BaModProbe.dll')" -ForegroundColor Green
}

Write-Host ""
Write-Host "OK. Output the probe writes at runtime:" -ForegroundColor Green
Write-Host "   <GameDir>\UserData\BaModProbe\dump_latest.json"
Write-Host "   <GameDir>\UserData\BaModProbe\dumps\dump_<utc>.json"
Write-Host "   <GameDir>\UserData\BaModProbe\override.json     (you author this)"
Write-Host "   <GameDir>\UserData\BaModProbe\probe.log"
