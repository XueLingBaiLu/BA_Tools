$ErrorActionPreference='Stop'
$ML  = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$PROXY = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$want = @('Il2CppSystem.Collections.IEnumerable','Il2CppSystem.Collections.Generic.IEnumerable`1','Il2CppSystem.Collections.Generic.IEnumerator`1','Il2CppSystem.Collections.Generic.List`1','Il2CppSystem.Collections.Generic.Dictionary`2','Il2CppSystem.Collections.IEnumerator','Il2CppSystem.Collections.Generic.ICollection`1')
foreach ($f in Get-ChildItem $PROXY -Filter 'Il2Cppmscorlib.dll') {
  $a = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($f.FullName)
  $ix = @{}
  foreach ($t in $a.MainModule.Types) { $ix[$t.FullName] = $t }
  Write-Output ('===== ' + $f.Name + ' =====')
  foreach ($w in $want) {
    if (-not $ix.ContainsKey($w)) { Write-Output ('  MISSING: ' + $w); continue }
    $t = $ix[$w]
    Write-Output ('--- ' + $t.FullName)
    $ifs = @($t.Interfaces | ForEach-Object { $_.InterfaceType.FullName })
    Write-Output ('    implements: ' + (($ifs | Sort-Object) -join ' | '))
    $ms = @($t.Methods | Where-Object { $_.IsPublic -and ($_.Name -eq 'GetEnumerator' -or $_.Name -eq 'MoveNext' -or $_.Name -eq 'get_Count' -or $_.Name -eq 'get_Item' -or $_.Name -eq 'get_Current' -or $_.Name -eq '.ctor') } | ForEach-Object { $ps = ($_.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ','; $_.Name + '(' + $ps + ')' })
    Write-Output ('    key members: ' + (($ms | Select-Object -First 14) -join '  '))
  }
  break
}