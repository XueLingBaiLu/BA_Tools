$ErrorActionPreference='Stop'
$ML  = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$PROXY = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $PROXY 'Il2CppBrokenArrow.dll'))
$t = $asm.MainModule.Types | Where-Object { $_.FullName -eq 'Il2CppBrokenArrow.Shared.Ecs.DataBaseService' }
Write-Output ('TYPE ' + $t.FullName + '  base=' + $t.BaseType)
foreach ($m in $t.Methods) {
  if ($m.Name -ne 'Load' -and $m.Name -ne 'Reload' -and $m.Name -ne 'ReloadByDefault' -and $m.Name -ne 'Initialize' -and $m.Name -ne 'LoadCompiled' -and $m.Name -ne 'LoadScenario' -and $m.Name -ne 'LoadByJson') { continue }
  $ps = ($m.Parameters | ForEach-Object { $_.ParameterType.Name + ' ' + $_.Name }) -join ', '
  Write-Output ('  ' + $m.ReturnType.Name + ' ' + $m.Name + '(' + $ps + ')  public=' + $m.IsPublic + ' private=' + $m.IsPrivate + ' static=' + $m.IsStatic + ' virtual=' + $m.IsVirtual)
}