
<#
  dump_types.ps1 -- read Il2CppDumper dummy assemblies with Mono.Cecil and emit the
  authoritative type/field/property table for every table of
  BrokenArrow.DataBase.DataBaseSourceData.

  ASCII-only on purpose: Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI,
  so no non-ASCII byte may appear in this file. Repo paths are derived from $PSScriptRoot.

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\probe_mod\tools\dump_types.ps1
    ... -DummyDir <dir> -Cecil <Mono.Cecil.dll> -OutFile <json>
#>
param(
  [string]$DummyDir,
  [string]$Cecil,
  [string]$OutFile
)
$ErrorActionPreference = 'Stop'

$Proj     = Split-Path $PSScriptRoot -Parent                     # BA_Mod_Maker_2.0\tools\probe_mod
$Tools    = Split-Path $Proj -Parent                             # BA_Mod_Maker_2.0\tools
$Repo     = Split-Path $Tools -Parent                            # BA_Mod_Maker_2.0
$WorkRoot = Split-Path $Repo -Parent                             # D:\<workspace>

if (-not $DummyDir) { $DummyDir = Join-Path $Repo '_work\il2cpp' }
if (-not $Cecil)    { $Cecil    = Join-Path $WorkRoot 'BROKEN ARROW REALISM OVERHAUL\MelonLoader\net6\Mono.Cecil.dll' }
if (-not $OutFile)  { $OutFile  = Join-Path $Proj '_work\il2cpp_types.json' }

if (-not (Test-Path $Cecil))    { throw "Mono.Cecil.dll not found: $Cecil" }
if (-not (Test-Path $DummyDir)) { throw "dummy assembly dir not found: $DummyDir" }
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile -Parent) | Out-Null

Add-Type -Path $Cecil
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $DummyDir 'BrokenArrow.dll'))
$all = @{}
foreach ($t in $asm.MainModule.Types) { $all[$t.FullName] = $t }

function Get-PropList($t) {
  $o = New-Object System.Collections.ArrayList
  foreach ($p in $t.Properties) {
    $short = $p.Name
    $explicit = $false
    # explicit interface implementations are named 'Ns.Iface.PropName' in metadata
    if ($p.Name -match '^(.+)\.([^.]+)$') { $short = $Matches[2]; $explicit = $true }
    [void]$o.Add([ordered]@{
      raw           = $p.Name
      name          = $short
      explicitIface = $explicit
      type          = $p.PropertyType.FullName
      canWrite      = ($p.SetMethod -ne $null)
      isPublic      = (($p.GetMethod -ne $null) -and $p.GetMethod.IsPublic)
    })
  }
  return ,$o
}

$src = $all['BrokenArrow.DataBase.DataBaseSourceData']
if (-not $src) { throw 'BrokenArrow.DataBase.DataBaseSourceData not found in BrokenArrow.dll' }

$result = New-Object System.Collections.ArrayList
foreach ($p in $src.Properties) {
  $pt = $p.PropertyType
  $modelFull = $null
  if ($pt.IsGenericInstance) {
    $ga = $pt.GenericArguments
    if ($ga.Count -eq 1) { $modelFull = $ga[0].FullName }
  }
  $entry = [ordered]@{
    table      = $p.Name
    tableType  = $pt.FullName
    modelType  = $modelFull
    baseChain  = @()
    interfaces = @()
    ownProps   = @()
    ifaceProps = @()
    fields     = @()
  }
  if ($modelFull -and $all.ContainsKey($modelFull)) {
    $mt = $all[$modelFull]
    $b = $mt.BaseType
    while ($b -and $all.ContainsKey($b.FullName)) {
      $entry.baseChain += $b.FullName
      $bt = $all[$b.FullName]
      $entry.ownProps += (Get-PropList $bt)
      $b = $bt.BaseType
    }
    $entry.ownProps += (Get-PropList $mt)
    foreach ($ie in $mt.Interfaces) {
      $ifn = $ie.InterfaceType.FullName
      $entry.interfaces += $ifn
      if ($all.ContainsKey($ifn)) {
        foreach ($p2 in (Get-PropList $all[$ifn])) {
          $p2['fromInterface'] = $ifn
          $entry.ifaceProps += $p2
        }
      }
    }
    foreach ($mf in $mt.Fields) {
      $entry.fields += [ordered]@{ name = $mf.Name; type = $mf.FieldType.FullName }
    }
  }
  [void]$result.Add($entry)
}

$doc = [ordered]@{
  generated_from = (Join-Path $DummyDir 'BrokenArrow.dll')
  assembly       = $asm.FullName
  table_count    = $result.Count
  source_data    = 'BrokenArrow.DataBase.DataBaseSourceData'
  tables         = $result
}
[System.IO.File]::WriteAllText($OutFile, ($doc | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
Write-Output ('WROTE ' + $OutFile + ' tables=' + $result.Count)
foreach ($e in $result) {
  Write-Output ($e.table.PadRight(30) + ' ownProps=' + @($e.ownProps).Count + ' ifaces=' + @($e.interfaces).Count + ' ifaceProps=' + @($e.ifaceProps).Count)
}
