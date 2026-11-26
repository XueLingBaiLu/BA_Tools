
$ErrorActionPreference='Stop'
$ML = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $ML 'MelonLoader.dll'))
$idx = @{}
foreach ($t in $asm.MainModule.Types) { $idx[$t.FullName] = $t }

Write-Output '=== every type whose name contains MelonEnvironment ==='
foreach ($k in $idx.Keys) { if ($k -like '*MelonEnvironment*') { Write-Output ('  ' + $k) } }

Write-Output '=== MelonBase ==='
if ($idx.ContainsKey('MelonLoader.MelonBase')) {
  $t = $idx['MelonLoader.MelonBase']
  Write-Output ('  base=' + $t.BaseType)
  foreach ($p in $t.Properties) { Write-Output ('   PROP   ' + $p.PropertyType.Name + ' ' + $p.Name + '  get=' + ($p.GetMethod -ne $null) + ' set=' + ($p.SetMethod -ne $null)) }
  foreach ($m in $t.Methods) {
    if ($m.IsPublic -or $m.IsFamily -or $m.IsFamilyOrAssembly) {
      Write-Output ('   METHOD ' + $m.ReturnType.Name + ' ' + $m.Name + ' virtual=' + $m.IsVirtual + ' family=' + $m.IsFamily)
    }
  }
}
Write-Output '=== MelonMod base chain ==='
$m = $idx['MelonLoader.MelonMod']
Write-Output ('  MelonMod base = ' + $m.BaseType.FullName)
$mb = $idx['MelonLoader.MelonBase']
Write-Output ('  MelonBase base = ' + $mb.BaseType.FullName)
Write-Output '=== MelonBase fields ==='
foreach ($f in $mb.Fields) { Write-Output ('   FIELD ' + $f.FieldType.Name + ' ' + $f.Name) }
