$ErrorActionPreference='Continue'
$G='D:\Steam\steamapps\common\broken_arrow'
Add-Type -Path (Join-Path "$G\MelonLoader\net6" 'Mono.Cecil.dll')
$asm=[Mono.Cecil.AssemblyDefinition]::ReadAssembly("$G\Mods\BaModProbe.dll")
Write-Output '=== new code present in the DEPLOYED dll? ==='
foreach($t in $asm.MainModule.Types){
  if($t.FullName -notin @('BaModProbe.Il2CppNullable','BaModProbe.RowAccess','BaModProbe.DbDump')){continue}
  $names = @($t.Methods | ForEach-Object { $_.Name })
  foreach($want in @('UnderlyingOf','IsProxyNullable','TryRawNullableIntRead','Diagnose','IsPrimitiveish','LoadWatchList','IsWatched')){
    if($names -contains $want){ Write-Output ('  OK   ' + $t.Name + '::' + $want) }
  }
}
Write-Output '=== IsPrimitiveish recurses into Il2CppNullable.UnderlyingOf? ==='
foreach($t in $asm.MainModule.Types){ if($t.Name -ne 'RowAccess'){continue}
  foreach($m in $t.Methods){ if($m.Name -ne 'IsPrimitiveish'){continue}
    $calls = @($m.Body.Instructions | Where-Object { $_.OpCode.Name -eq 'call' } | ForEach-Object { $_.Operand.Name })
    Write-Output ('  calls: ' + (($calls | Sort-Object -Unique) -join ', '))
  } }
Write-Output '=== deployed artefact ==='
$d=Get-Item "$G\Mods\BaModProbe.dll"; $ia=Get-Item "$G\MelonLoader\Il2CppAssemblies\Il2CppBrokenArrow.dll"
Write-Output ('  size  : ' + $d.Length + ' bytes')
Write-Output ('  mtime : ' + $d.LastWriteTimeUtc.ToString('yyyy-MM-dd HH:mm:ss') + ' UTC')
Write-Output ('  now   : ' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC')
Write-Output ('  newer than proxies: ' + ($d.LastWriteTimeUtc -gt $ia.LastWriteTimeUtc))
Write-Output ('  diagnose_columns.txt: ' + (Test-Path "$G\UserData\BaModProbe\diagnose_columns.txt"))
Get-Content "$G\UserData\BaModProbe\diagnose_columns.txt" | ForEach-Object { '    | ' + $_ }