
$ErrorActionPreference='Stop'
$ML = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $ML 'MelonLoader.dll'))
foreach ($t in $asm.MainModule.Types) {
  if ($t.FullName -eq 'MelonLoader.Utils.MelonEnvironment') {
    Write-Output ('=== ' + $t.FullName + ' ===')
    foreach ($p in $t.Properties) { Write-Output ('   PROP   ' + $p.PropertyType.Name + ' ' + $p.Name + ' static=' + ($p.GetMethod -ne $null -and $p.GetMethod.IsStatic)) }
    foreach ($m in $t.Methods) { if ($m.IsPublic -or $m.IsFamily) { Write-Output ('   METHOD ' + $m.ReturnType.Name + ' ' + $m.Name) } }
  }
}
