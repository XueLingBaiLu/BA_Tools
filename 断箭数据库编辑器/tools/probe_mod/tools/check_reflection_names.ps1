<#
  check_reflection_names.ps1 -- assert every name the C# reaches for *by string* actually exists.

  The compiler already proves every *typed* reference (the project builds against the real
  Il2CppInterop proxy assembly). It cannot prove anything about reflection done with string
  literals, and that is exactly where a probe silently does nothing instead of failing loudly.
  This script closes that gap with Mono.Cecil over the proxy assembly.

  Checked -- the literals actually present in src/*.cs:
      "RawAccess"            -> Il2CppBrokenArrow.Shared.Ecs.DataBaseService property
      "CurrentSourceId"      -> Il2CppBrokenArrow.Shared.Ecs.DataBaseService property
      "GetAll"               -> Il2CppBrokenArrow.DataBase.Driver.Table<T> method
      "Id"                   -> present on all 24 row models
      "_<name>_k__BackingField" -> the Il2CppInterop auto-property convention really holds
      "Table" prefix + ...Driver namespace -> yields exactly the 24 table names

  A positive control runs first: a bogus member name MUST be reported missing, otherwise the
  ruler cannot go red and its PASS would mean nothing.

  Exit 0 only when every check passes and the positive control went red.
  ASCII-only, and deliberately free of PowerShell line-continuation characters.
#>
param(
  [string]$GameDir,
  [string]$Cecil
)
$ErrorActionPreference = 'Stop'
$Proj = Split-Path $PSScriptRoot -Parent
if (-not $GameDir) { $GameDir = $env:BA_MOD_MAKER_GAME }
if (-not $GameDir) { $GameDir = 'D:\Steam\steamapps\common\broken_arrow' }
if (-not $Cecil)   { $Cecil = Join-Path $GameDir 'MelonLoader\net6\Mono.Cecil.dll' }
$proxy = Join-Path $GameDir "MelonLoader\Il2CppAssemblies\Il2CppBrokenArrow.dll"
if (-not (Test-Path $proxy)) { throw "proxy assembly not found: $proxy" }

Add-Type -Path $Cecil
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($proxy)
$idx = @{}
foreach ($t in $asm.MainModule.Types) { $idx[$t.FullName] = $t }

$tableGen = 'Table' + [char]96 + '1'
$script:fails = 0
function Check([bool]$ok, [string]$what) {
  if ($ok) { Write-Host ('  PASS  ' + $what) }
  else { Write-Host ('  FAIL  ' + $what) -ForegroundColor Red; $script:fails++ }
}

Write-Host "POSITIVE CONTROL (the ruler must be able to go red)" -ForegroundColor Cyan
$bogus = 'NoSuchMember_ZZZ'
$svc = $idx['Il2CppBrokenArrow.Shared.Ecs.DataBaseService']
if (-not $svc) { throw 'Il2CppBrokenArrow.Shared.Ecs.DataBaseService not found' }
$bogusFound = (@($svc.Properties | Where-Object { $_.Name -eq $bogus }).Count -gt 0)
Check (-not $bogusFound) ('bogus member ' + $bogus + ' is reported missing')
if ($bogusFound) {
  Write-Host "  ruler cannot go red -- aborting, nothing it says would be evidence" -ForegroundColor Red
  exit 1
}

Write-Host "REAL CHECKS" -ForegroundColor Cyan

$rawAccess = @($svc.Properties | Where-Object { $_.Name -eq 'RawAccess' })
Check ($rawAccess.Count -eq 1) 'DataBaseService.RawAccess exists (property)'
if ($rawAccess.Count -eq 1) {
  $want = 'Il2CppBrokenArrow.DataBase.DataBaseSourceData'
  Check ($rawAccess[0].PropertyType.FullName -eq $want) ('RawAccess type = ' + $rawAccess[0].PropertyType.FullName)
}

$curSrc = @($svc.Properties | Where-Object { $_.Name -eq 'CurrentSourceId' })
Check ($curSrc.Count -eq 1) 'DataBaseService.CurrentSourceId exists'

$tgName = 'Il2CppBrokenArrow.DataBase.Driver.' + $tableGen
$tg = $idx[$tgName]
Check ($tg -ne $null) ('Table generic type found: ' + $tgName)
if ($tg) { Check ((@($tg.Methods | Where-Object { $_.Name -eq 'GetAll' }).Count) -eq 1) 'Table<T>.GetAll() exists' }

$src = $idx['Il2CppBrokenArrow.DataBase.DataBaseSourceData']
if (-not $src) { throw "Il2CppBrokenArrow.DataBase.DataBaseSourceData not found" }
$tables = @()
foreach ($p in $src.Properties) {
  $pt = $p.PropertyType
  if (-not $pt.IsGenericInstance) { continue }
  if ($pt.GenericArguments.Count -ne 1) { continue }
  $gen = $pt.ElementType   # Cecil: GenericInstanceType.ElementType is the open generic
  if ($gen.Namespace -ne 'Il2CppBrokenArrow.DataBase.Driver') { continue }
  if (-not $gen.Name.StartsWith('Table')) { continue }
  if ($p.Name.StartsWith('_') -and $p.Name.EndsWith('_k__BackingField')) { continue }
  $tables += ,@{ Name = $p.Name; Model = $pt.GenericArguments[0].FullName }
}
Check ($tables.Count -eq 24) ('discovery rule yields exactly 24 tables (got ' + $tables.Count + ')')
$pfp = @($tables | Where-Object { $_.Name -eq 'PlaneFlyPresets' })
Check ($pfp.Count -eq 1) 'PlaneFlyPresets present under its PLURAL table name'
if ($pfp.Count -eq 1) {
  $wantModel = 'Il2CppBrokenArrow.DataBase.Models.PlaneFlyPreset'
  Check ($pfp[0].Model -eq $wantModel) ('its ROW MODEL is singular: ' + $pfp[0].Model)
}

$noId = @()
$noBacking = @()
foreach ($t in $tables) {
  $m = $idx[$t.Model]
  if (-not $m) { $noId += ($t.Name + '(model missing)'); continue }
  if ((@($m.Properties | Where-Object { $_.Name -eq 'Id' }).Count) -eq 0) { $noId += $t.Name }
  if ((@($m.Properties | Where-Object { $_.Name -eq '_Id_k__BackingField' }).Count) -eq 0) { $noBacking += $t.Name }
}
$noIdTxt = ''
if ($noId.Count -gt 0) { $noIdTxt = ' -- missing on: ' + ($noId -join ', ') }
Check ($noId.Count -eq 0) ('every row model exposes Id' + $noIdTxt)
$noBk = ''
if ($noBacking.Count -gt 0) { $noBk = ' -- absent on: ' + ($noBacking -join ', ') }
Check ($noBacking.Count -eq 0) ('the _<name>_k__BackingField convention holds for Id' + $noBk)

Write-Host ""
if ($fails -eq 0) {
  Write-Host ('RESULT: all reflection names verified (' + $tables.Count + ' tables)') -ForegroundColor Green
  exit 0
}
Write-Host ('RESULT: ' + $fails + ' check(s) FAILED') -ForegroundColor Red
exit 1
