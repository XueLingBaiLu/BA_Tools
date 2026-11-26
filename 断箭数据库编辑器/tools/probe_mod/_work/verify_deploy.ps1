
$ErrorActionPreference='Stop'
$G='D:\Steam\steamapps\common\broken_arrow'
Write-Output '=== deployed artefacts ==='
foreach ($p in @("$G\Mods\BaModProbe.dll", "$G\UserData\BaModProbe\columns.json")) {
  if (Test-Path $p) { Write-Output ("  OK   " + $p + "  " + (Get-Item $p).Length + " bytes") }
  else { Write-Output ("  MISSING " + $p) }
}
Write-Output '=== Mods dir (game-owned subdirs must be untouched) ==='
Get-ChildItem "$G\Mods" | Select-Object Mode,Name | Format-Table -AutoSize | Out-String

Write-Output '=== assembly references of the built dll (each must exist at runtime) ==='
Add-Type -Path "$G\MelonLoader\net6\Mono.Cecil.dll"
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly("$G\Mods\BaModProbe.dll")
Write-Output ("  assembly: " + $asm.FullName)
$net6 = "$G\MelonLoader\net6"
$proxy = "$G\MelonLoader\Il2CppAssemblies"
foreach ($r in $asm.MainModule.AssemblyReferences) {
  $cands = @("$net6\$($r.Name).dll", "$proxy\$($r.Name).dll")
  $hit = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($hit) { Write-Output ("  OK    " + $r.Name + "  -> " + $hit) }
  else {
    if ($r.Name -in @('System.Runtime','System.Console','System.Collections','netstandard','mscorlib','System.Private.CoreLib','System.Linq','System.IO.Compression')) {
      Write-Output ("  OK    " + $r.Name + "  -> .NET 6 runtime")
    } else {
      Write-Output ("  RISK  " + $r.Name + "  -> NOT FOUND in net6 or Il2CppAssemblies")
    }
  }
}
Write-Output '=== column manifest the mod will read ==='
$j = Get-Content "$G\UserData\BaModProbe\columns.json" -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Output ("  version=" + $j.version + "  table_count=" + $j.table_count + "  tables=" + (($j.tables.PSObject.Properties.Name) -join ', '))
