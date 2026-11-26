
<#
  dump_proxy_members.ps1 -- authoritative member map of the Il2CppInterop PROXY assembly.

  For every table of Il2CppBrokenArrow.DataBase.DataBaseSourceData and every column named in
  _work/columns.json, record which mechanism can read and write it on the row type:
      named prop | _X_k__BackingField | field | missing
  and whether a public setter exists.

  ASCII-only (Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI).
#>
param(
  [string]$ProxyDir,
  [string]$Cecil,
  [string]$ColumnsJson,
  [string]$OutFile
)
$ErrorActionPreference = 'Stop'
$Proj     = Split-Path $PSScriptRoot -Parent
$Tools    = Split-Path $Proj -Parent
$Repo     = Split-Path $Tools -Parent
$WorkRoot = Split-Path $Repo -Parent
# Game dir: honour BA_MOD_MAKER_GAME (same variable the product uses), else the documented default.
$GameDir = $env:BA_MOD_MAKER_GAME
if (-not $GameDir) { $GameDir = 'D:\Steam\steamapps\common\broken_arrow' }
if (-not $ProxyDir)   { $ProxyDir   = Join-Path $GameDir 'MelonLoader\Il2CppAssemblies' }
if (-not $Cecil)      { $Cecil      = Join-Path $GameDir 'MelonLoader\net6\Mono.Cecil.dll' }
if (-not $ColumnsJson){ $ColumnsJson= Join-Path $Proj '_work\columns.json' }
if (-not $OutFile)    { $OutFile    = Join-Path $Proj '_work\proxy_members.json' }

if (-not (Test-Path $ProxyDir)) { throw "proxy dir not found: $ProxyDir" }
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile -Parent) | Out-Null

Add-Type -Path $Cecil
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $ProxyDir 'Il2CppBrokenArrow.dll'))
$idx = @{}
foreach ($t in $asm.MainModule.Types) { $idx[$t.FullName] = $t }

$cols = Get-Content $ColumnsJson -Raw -Encoding UTF8 | ConvertFrom-Json
$out = [ordered]@{
  assembly    = $asm.FullName
  proxy_dir   = $ProxyDir
  source_data = 'Il2CppBrokenArrow.DataBase.DataBaseSourceData'
  tables      = [ordered]@{}
}

$src = $idx['Il2CppBrokenArrow.DataBase.DataBaseSourceData']
if (-not $src) { throw 'Il2CppBrokenArrow.DataBase.DataBaseSourceData not found' }

foreach ($prop in $src.Properties) {
  if (-not $cols.tables.PSObject.Properties.Name.Contains($prop.Name)) { continue }
  $pt = $prop.PropertyType
  $mt = $null
  if ($pt.IsGenericInstance -and $pt.GenericArguments.Count -eq 1) { $mt = $pt.GenericArguments[0].FullName }
  $tbl = [ordered]@{ modelType = $mt; columns = [ordered]@{} }
  if ($mt -and $idx.ContainsKey($mt)) {
    $m = $idx[$mt]
    foreach ($col in $cols.tables.($prop.Name)) {
      $named   = @($m.Properties | Where-Object { $_.Name -eq $col })
      $backing = @($m.Properties | Where-Object { $_.Name -eq ('_' + $col + '_k__BackingField') })
      $field   = @($m.Fields     | Where-Object { $_.Name -eq $col })
      $rec = [ordered]@{ kind = 'missing'; writable = $false; type = $null }
      if ($named.Count -gt 0) {
        $p = $named[0]
        $rec.kind     = 'prop'
        $rec.type     = $p.PropertyType.FullName
        $rec.writable = ($p.SetMethod -ne $null -and $p.SetMethod.IsPublic)
      } elseif ($backing.Count -gt 0) {
        $p = $backing[0]
        $rec.kind     = 'backingFieldProp'
        $rec.type     = $p.PropertyType.FullName
        $rec.writable = ($p.SetMethod -ne $null -and $p.SetMethod.IsPublic)
      } elseif ($field.Count -gt 0) {
        $f = $field[0]
        $rec.kind     = 'field'
        $rec.type     = $f.FieldType.FullName
        $rec.writable = ($f.IsPublic -and -not $f.IsInitOnly -and -not $f.IsLiteral)
      }
      $tbl.columns[$col] = $rec
    }
  }
  $out.tables[$prop.Name] = $tbl
}
[System.IO.File]::WriteAllText($OutFile, ($out | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
Write-Output ('WROTE ' + $OutFile)

$tot = 0; $now = 0; $miss = 0
foreach ($tn in $out.tables.Keys) {
  $c = $out.tables[$tn].columns
  foreach ($k in $c.Keys) {
    $tot++
    if ($c[$k].kind -eq 'missing') { $miss++ }
    if (-not $c[$k].writable)      { $now++; Write-Output ('  READ-ONLY/missing: ' + $tn + '.' + $k + '  kind=' + $c[$k].kind + ' type=' + $c[$k].type) }
  }
}
Write-Output ('columns=' + $tot + '  writable=' + ($tot - $now) + '  not-writable=' + $now + '  missing=' + $miss)
