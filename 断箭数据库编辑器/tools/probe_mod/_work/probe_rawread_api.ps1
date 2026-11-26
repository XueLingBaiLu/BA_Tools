$ErrorActionPreference='Stop'
$ML='D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$P='D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$ir=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $ML 'Il2CppInterop.Runtime.dll'))
foreach($t in $ir.MainModule.Types){
  if($t.Name -ne 'IL2CPP'){continue}
  Write-Output ('=== ' + $t.FullName)
  foreach($m in $t.Methods){
    if($m.Name -like '*field*' -or $m.Name -like '*Field*'){
      Write-Output ('  ' + $m.ReturnType.Name + ' ' + $m.Name + '(' + (($m.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ',') + ') static=' + $m.IsStatic)
    }
  }
}
Write-Output '=== Turrets proxy: NativeFieldInfoPtr_* statics + the backing prop ==='
$b=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2CppBrokenArrow.dll'))
foreach($t in $b.MainModule.Types){
  if($t.Name -ne 'Turrets'){continue}
  Write-Output ('  type ' + $t.FullName + '  isValueType=' + $t.IsValueType)
  foreach($f in $t.Fields){ if($f.Name -like '*ParentTurretId*'){ Write-Output ('    FIELD ' + $f.FieldType.FullName + ' ' + $f.Name + ' static=' + $f.IsStatic + ' public=' + $f.IsPublic) } }
}