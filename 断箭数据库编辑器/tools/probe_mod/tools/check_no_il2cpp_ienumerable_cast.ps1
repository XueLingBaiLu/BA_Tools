<#>
  check_no_il2cpp_ienumerable_cast.ps1 -- a ruler that CAN go red, for the bug that made the
  first real probe run return 24/24 empty tables.

  Why this exists: on the first live run every table failed with
      "GetAll() did not return IEnumerable"
  because every Il2CppInterop proxy collection implements NO managed interface. The managed
  cast  x as System.Collections.IEnumerable  is therefore always null. A compiler cannot catch
  it (the cast is perfectly legal C#), and we cannot run the game, so the check has to be made
  against the emitted IL of the shipped assembly.

  What it flags: any isinst / castclass in the target assembly aimed at a managed collection
  abstraction:
      System.Collections.IEnumerable
      System.Collections.Generic.IEnumerable`1
      System.Collections.IEnumerator
      System.Collections.Generic.IEnumerator`1
      System.Collections.ICollection / IList / IDictionary

  POSITIVE CONTROL runs first and is mandatory: tests/CastProbe is a tiny assembly that
  deliberately contains the forbidden cast. If the ruler does not flag it, the ruler cannot go
  red and its verdict on the real mod would be worthless -- so it aborts with exit 1.

  Exit 0 only when: the target assembly is clean AND the positive control was flagged.
  ASCII-only (Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI).
#>
param(
  [string]$Target
  ,[string]$PositiveControl
)
$ErrorActionPreference = 'Stop'
$Proj  = Split-Path $PSScriptRoot -Parent
$Tools = Split-Path $Proj -Parent
$Repo  = Split-Path $Tools -Parent
$Game  = $env:BA_MOD_MAKER_GAME
if (-not $Game) { $Game = 'D:\Steam\steamapps\common\broken_arrow' }
if (-not $Target) { $Target = Join-Path $Game 'Mods\BaModProbe.dll' }
if (-not $PositiveControl) { $PositiveControl = Join-Path $Proj 'tests\CastProbe\bin\Release\net6.0\CastProbe.dll' }

Add-Type -Path (Join-Path $Game 'MelonLoader\net6\Mono.Cecil.dll')

$forbidden = @(
  'System.Collections.IEnumerable',
  'System.Collections.Generic.IEnumerable`1',
  'System.Collections.IEnumerator',
  'System.Collections.Generic.IEnumerator`1',
  'System.Collections.ICollection',
  'System.Collections.IList',
  'System.Collections.IDictionary'
)

function Find-Forbidden([string]$path) {
  $hits = @()
  if (-not (Test-Path $path)) { return ,@("FILE-NOT-FOUND: $path") }
  $asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($path)
  foreach ($t in $asm.MainModule.GetTypes()) {
    foreach ($m in $t.Methods) {
      if (-not $m.HasBody) { continue }
      foreach ($i in $m.Body.Instructions) {
        if ($i.OpCode.Name -ne 'isinst' -and $i.OpCode.Name -ne 'castclass') { continue }
        $op = $i.Operand
        if ($op -eq $null) { continue }
        if ($forbidden -contains $op.FullName) {
          $hits += ('  ' + $t.FullName + '::' + $m.Name + '  ' + $i.OpCode.Name + ' ' + $op.FullName)
        }
      }
    }
  }
  return ,$hits
}

Write-Host "POSITIVE CONTROL (the ruler must be able to go red)" -ForegroundColor Cyan
Write-Host "  control assembly: $PositiveControl"
$ctlHits = Find-Forbidden $PositiveControl
foreach ($h in $ctlHits) { Write-Host $h }
$ctlRed = $ctlHits.Count -gt 0
if (-not $ctlRed) {
  Write-Host "  the ruler did NOT flag the known-bad assembly -- it cannot go red, aborting" -ForegroundColor Red
  exit 1
}
Write-Host ('  flagged ' + $ctlHits.Count + ' forbidden cast(s) in the control -> ruler can go red') -ForegroundColor Green

Write-Host "TARGET" -ForegroundColor Cyan
Write-Host "  assembly: $Target"
$tHits = Find-Forbidden $Target
foreach ($h in $tHits) { Write-Host $h -ForegroundColor Red }

Write-Host ""
if ($tHits.Count -eq 0) {
  Write-Host "RESULT: 0 forbidden managed-collection casts in the shipped probe" -ForegroundColor Green
  exit 0
}
Write-Host ('RESULT: ' + $tHits.Count + ' forbidden cast(s) present -- the dump WILL come back empty') -ForegroundColor Red
exit 1
