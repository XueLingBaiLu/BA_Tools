
$ErrorActionPreference='Stop'
$ML  = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$PROXY = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $PROXY 'Il2CppBrokenArrow.dll'))
Write-Output ('ASSEMBLY ' + $asm.FullName + '  top-level types=' + $asm.MainModule.Types.Count)
$want = @('Il2CppBrokenArrow.Shared.Ecs.DataBaseService','Il2CppBrokenArrow.DataBase.DataBaseSourceData','Il2CppBrokenArrow.DataBase.Driver.Table`1','Il2CppBrokenArrow.DataBase.Models.Ammunitions')
foreach ($t in $asm.MainModule.Types) {
  if ($want -notcontains $t.FullName) { continue }
  Write-Output ('=== ' + $t.FullName + '  base=' + $t.BaseType)
  foreach ($p in $t.Properties) { Write-Output ('   PROP   ' + $p.PropertyType.Name + '  ' + $p.Name + '   set=' + ($p.SetMethod -ne $null)) }
  foreach ($m in $t.Methods) {
    if ($m.Name -like 'get_*' -or $m.Name -like 'set_*') { continue }
    $ps2 = ($m.Parameters | ForEach-Object { $_.ParameterType.Name + ' ' + $_.Name }) -join ', '
    Write-Output ('   METHOD ' + $m.ReturnType.Name + ' ' + $m.Name + '(' + $ps2 + ')')
  }
}
