$ErrorActionPreference='Stop'
$ML='D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$P='D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$b=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2CppBrokenArrow.dll'))
$ix=@{}; foreach($t in $b.MainModule.Types){$ix[$t.FullName]=$t}
function ShowProp($typeName,$propName){
  $t=$ix[$typeName]
  if(-not $t){Write-Output ('  MISSING TYPE '+$typeName); return}
  foreach($p in $t.Properties){
    if($p.Name -ne $propName){continue}
    Write-Output ('  PROP '+$p.Name+'   type='+$p.PropertyType.FullName)
    if($p.GetMethod){
      $g=$p.GetMethod
      Write-Output ('    getter: '+$g.ReturnType.FullName+' public='+$g.IsPublic+' hasBody='+$g.HasBody)
      if($g.HasBody){
        $ops=@(); foreach($i in $g.Body.Instructions){ $ops += ($i.OpCode.Name + ' ' + $i.Operand) }
        Write-Output ('    IL: ' + (($ops | Select-Object -First 18) -join ' | '))
      }
    }
  }
}
Write-Output '=== Turrets.ParentTurretId ==='
ShowProp 'Il2CppBrokenArrow.DataBase.Models.Turrets' 'ParentTurretId'
Write-Output '=== Turrets._ParentTurretId_k__BackingField ==='
ShowProp 'Il2CppBrokenArrow.DataBase.Models.Turrets' '_ParentTurretId_k__BackingField'
Write-Output '=== PlaneFlyPresets.CornerSpeedYaw ==='
ShowProp 'Il2CppBrokenArrow.DataBase.Models.PlaneFlyPresets' 'CornerSpeedYaw'
Write-Output '=== PlaneFlyPresets._CornerSpeedYaw_k__BackingField ==='
ShowProp 'Il2CppBrokenArrow.DataBase.Models.PlaneFlyPresets' '_CornerSpeedYaw_k__BackingField'
Write-Output '=== Il2CppSystem.Nullable<T> definition ==='
$m=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2Cppmscorlib.dll'))
foreach($t in $m.MainModule.Types){ if($t.Name -eq 'Nullable`1'){
  Write-Output ('  '+$t.FullName+'  isValueType='+$t.IsValueType+' isClass='+$t.IsClass+' base='+$t.BaseType)
  foreach($p2 in $t.Properties){ Write-Output ('    PROP '+$p2.Name+' : '+$p2.PropertyType.FullName) }
  foreach($f in $t.Fields){ Write-Output ('    FIELD '+$f.Name+' : '+$f.FieldType.FullName) }
} }