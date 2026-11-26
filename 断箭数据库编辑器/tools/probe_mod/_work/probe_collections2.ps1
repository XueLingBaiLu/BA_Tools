$ErrorActionPreference='Stop'
$ML  = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
$PROXY = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\Il2CppAssemblies'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
$a = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $PROXY 'Il2Cppmscorlib.dll'))
$ix = @{}
foreach ($t in $a.MainModule.Types) { $ix[$t.FullName] = $t }
foreach ($n in @('Il2CppSystem.Collections.Generic.IEnumerable`1','Il2CppSystem.Collections.Generic.IEnumerator`1')) {
  $t = $ix[$n]
  Write-Output ('=== ' + $t.FullName + '   (implements: ' + ((@($t.Interfaces | ForEach-Object { $_.InterfaceType.Name }) | Sort-Object) -join ',') + ')')
  foreach ($m in $t.Methods) {
    if ($m.Name -eq '.ctor') { Write-Output ('    .ctor(' + (($m.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ',') + ')'); continue }
    Write-Output ('    ' + $m.ReturnType.FullName + '  ' + $m.Name + '(' + (($m.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ',') + ')')
  }
}
Write-Output '=== does Table<T>.GetAll() declare the Il2Cpp IEnumerable<T>? ==='
$b = [Mono.Cecil.AssemblyDefinition]::ReadAssembly((Join-Path $PROXY 'Il2CppBrokenArrow.dll'))
$tg = $b.MainModule.Types | Where-Object { $_.FullName -eq ('Il2CppBrokenArrow.DataBase.Driver.Table' + [char]96 + '1') }
foreach ($m in $tg.Methods) { if ($m.Name -eq 'GetAll') { Write-Output ('    GetAll() -> ' + $m.ReturnType.FullName) } }
Write-Output '=== List<T> full public surface (what BARO uses) ==='
$lt = $ix['Il2CppSystem.Collections.Generic.List`1']
foreach ($m in $lt.Methods) { if (-not $m.IsPublic) { continue }; if ($m.Name -eq '.ctor') { Write-Output ('    .ctor(' + (($m.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ',') + ')'); continue }; Write-Output ('    ' + $m.ReturnType.Name + ' ' + $m.Name + '(' + (($m.Parameters | ForEach-Object { $_.ParameterType.Name }) -join ',') + ')') }