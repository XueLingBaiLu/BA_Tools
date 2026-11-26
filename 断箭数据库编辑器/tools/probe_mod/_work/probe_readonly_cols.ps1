$ErrorActionPreference='Stop'
$ML='D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$P='D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$b=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2CppBrokenArrow.dll'))
$ix=@{}; foreach($t in $b.MainModule.Types){$ix[$t.FullName]=$t}
$t=$ix['Il2CppBrokenArrow.DataBase.Models.PlaneFlyPreset']
Write-Output ('Turrets singular model found: ' + ($t -ne $null))
Write-Output '=== PlaneFlyPreset: the 3 read-only float columns, backed or computed? ==='
foreach($nm in @('CornerSpeedYaw','CornerAfterburnSpeedYaw','AfterburnCornerSpeed')){
  $p = $t.Properties | Where-Object { $_.Name -eq $nm }
  $bk = $t.Properties | Where-Object { $_.Name -eq ('_' + $nm + '_k__BackingField') }
  Write-Output ('  ' + $nm + ': namedProp=' + ($p -ne $null) + ' setter=' + ($p -ne $null -and $p.SetMethod -ne $null) + ' backingFieldProp=' + ($bk -ne $null))
  if($p -ne $null -and $p.GetMethod -and $p.GetMethod.HasBody){
    $ops=@(); foreach($i in $p.GetMethod.Body.Instructions){ $ops += $i.OpCode.Name }
    $inv = @($ops | Where-Object { $_ -eq 'call' -or $_ -eq 'callvirt' }).Count
    Write-Output ('      getter IL ops=' + $ops.Count + '  calls=' + $inv + '  uses_runtime_invoke=' + (($p.GetMethod.Body.Instructions | Where-Object { $_.Operand -ne $null -and $_.Operand.ToString() -like '*il2cpp_runtime_invoke*' }).Count -gt 0))
  }
}