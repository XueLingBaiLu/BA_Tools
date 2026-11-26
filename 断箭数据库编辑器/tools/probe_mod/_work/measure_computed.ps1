$ErrorActionPreference='Stop'
$ML='D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$P='D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$b=[Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $P 'Il2CppBrokenArrow.dll'))
$ix=@{}; foreach($t in $b.MainModule.Types){$ix[$t.FullName]=$t}
$src=$ix['Il2CppBrokenArrow.DataBase.DataBaseSourceData']
$computed=@(); $total=0
foreach($p in $src.Properties){
  $pt=$p.PropertyType
  if(-not $pt.IsGenericInstance){continue}
  if($pt.GenericArguments.Count -ne 1){continue}
  if($pt.ElementType.Namespace -ne 'Il2CppBrokenArrow.DataBase.Driver'){continue}
  if(-not $pt.ElementType.Name.StartsWith('Table')){continue}
  if($p.Name.StartsWith('_') -and $p.Name.EndsWith('_k__BackingField')){continue}
  $m=$ix[$pt.GenericArguments[0].FullName]
  if(-not $m){continue}
  # columns = public instance props not starting with _   (the DataBaseCompiled key set approximates this)
  foreach($c in $m.Properties){
    if($c.Name.StartsWith('_')){continue}
    if($c.GetMethod -eq $null){continue}
    $total++
    $bk = $m.Properties | Where-Object { $_.Name -eq ('_' + $c.Name + '_k__BackingField') }
    if($bk -eq $null){ $computed += ($p.Name + '.' + $c.Name) }
  }
}
Write-Output ('public-read properties scanned: ' + $total)
Write-Output ('WITHOUT a backing field (=> computed by the game): ' + $computed.Count)
foreach($c in $computed){ Write-Output ('   ' + $c) }