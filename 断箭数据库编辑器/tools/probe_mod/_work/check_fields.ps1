
$ErrorActionPreference='Stop'
$ML  = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$PROXY = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $PROXY 'Il2CppBrokenArrow.dll'))
$idx = @{}
foreach ($t in $asm.MainModule.Types) { $idx[$t.FullName] = $t }

$checks = @(
  @('Il2CppBrokenArrow.DataBase.Models.Units',      @('OriginalName','OriginalCost','OwnerInfantryID','Id','Name','Type','Role')),
  @('Il2CppBrokenArrow.DataBase.Models.Weapons',    @('WeaponChannel','WeaponPriority','Id','Type')),
  @('Il2CppBrokenArrow.DataBase.Models.Mobility',   @('FlyPreset','Id')),
  @('Il2CppBrokenArrow.DataBase.Models.Turrets',    @('ParentTurretId','Id'))
)
foreach ($c in $checks) {
  $tn = $c[0]; $names = $c[1]
  if (-not $idx.ContainsKey($tn)) { Write-Output ("!! NOT FOUND " + $tn); continue }
  $t = $idx[$tn]
  Write-Output ('=== ' + $tn + '  base=' + $t.BaseType)
  Write-Output ('    props total=' + $t.Properties.Count + '  fields total=' + $t.Fields.Count)
  Write-Output ('    interfaces: ' + (($t.Interfaces | ForEach-Object { $_.InterfaceType.Name }) -join ', '))
  foreach ($n in $names) {
    $pset = @($t.Properties | Where-Object { $_.Name -eq $n -or $_.Name -eq ('_' + $n + '_k__BackingField') })
    $fset = @($t.Fields | Where-Object { $_.Name -eq $n })
    if ($pset.Count -eq 0 -and $fset.Count -eq 0) { Write-Output ('    ' + $n.PadRight(20) + ' -> NOT ON TYPE') ; continue }
    foreach ($p in $pset) {
      $g = $p.GetMethod; $s = $p.SetMethod
      Write-Output ('    PROP ' + $p.Name.PadRight(30) + ' ty=' + $p.PropertyType.Name.PadRight(12) + ' getPub=' + ($g -ne $null -and $g.IsPublic) + ' setPub=' + ($s -ne $null -and $s.IsPublic))
    }
    foreach ($f in $fset) {
      Write-Output ('    FIELD ' + $f.Name.PadRight(29) + ' ty=' + $f.FieldType.Name.PadRight(12) + ' pub=' + $f.IsPublic + ' static=' + $f.IsStatic)
    }
  }
  # interface property names that look like IReadOnlyUnits members
  foreach ($ie in $t.Interfaces) {
    $ifn = $ie.InterfaceType.FullName
    if ($idx.ContainsKey($ifn)) {
      $ip = ($idx[$ifn].Properties | ForEach-Object { $_.Name }) -join ', '
      Write-Output ('    iface ' + $ifn + ' props: ' + $ip)
    }
  }
}
