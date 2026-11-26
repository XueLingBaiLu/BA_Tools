$ErrorActionPreference='Stop'
$ML='D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$P='D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
$cols = Get-Content 'D:\断箭模组制作\BA_Mod_Maker_2.0\tools\probe_mod\_work\columns.json' -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$b=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2CppBrokenArrow.dll'))
$ix=@{}; foreach($t in $b.MainModule.Types){$ix[$t.FullName]=$t}
$src=$ix['Il2CppBrokenArrow.DataBase.DataBaseSourceData']
$model=@{}
foreach($p in $src.Properties){
  $pt=$p.PropertyType
  if(-not $pt.IsGenericInstance){continue}
  if($pt.GenericArguments.Count -ne 1){continue}
  if($pt.ElementType.Namespace -ne 'Il2CppBrokenArrow.DataBase.Driver'){continue}
  if(-not $pt.ElementType.Name.StartsWith('Table')){continue}
  if($p.Name.StartsWith('_') -and $p.Name.EndsWith('_k__BackingField')){continue}
  $model[$p.Name]=$ix[$pt.GenericArguments[0].FullName]
}
$auto=@(); $plain=@(); $computed=@(); $n=0
foreach($tn in $cols.tables.PSObject.Properties.Name){
  $m=$model[$tn]
  foreach($c in $cols.tables.$tn){
    $n++
    $hasAuto = @($m.Properties | Where-Object { $_.Name -eq ('_' + $c + '_k__BackingField') }).Count -gt 0
    $hasPlain= @($m.Fields     | Where-Object { $_.Name -eq $c }).Count -gt 0
    if($hasAuto){ $auto += ($tn + '.' + $c) }
    elseif($hasPlain){ $plain += ($tn + '.' + $c) }
    else { $computed += ($tn + '.' + $c) }
  }
}
Write-Output ('columns checked      : ' + $n)
Write-Output ('stored via _X_k__BackingField : ' + $auto.Count)
Write-Output ('stored via plain-named field  : ' + $plain.Count)
foreach($c in $plain){ Write-Output ('    plain-field: ' + $c) }
Write-Output ('NEITHER => derived by the game : ' + $computed.Count)
foreach($c in $computed){ Write-Output ('    DERIVED: ' + $c) }