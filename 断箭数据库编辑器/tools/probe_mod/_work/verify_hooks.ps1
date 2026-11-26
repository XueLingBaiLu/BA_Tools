$ErrorActionPreference='Stop'
Add-Type -Path 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6\Mono.Cecil.dll'
$dll = 'D:\Steam\steamapps\common\broken_arrow\Mods\BaModProbe.dll'
$asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($dll)
Write-Output ('ASSEMBLY ' + $asm.FullName)
$n = 0
foreach ($t in $asm.MainModule.Types) {
  foreach ($ca in $t.CustomAttributes) {
    if ($ca.AttributeType.Name -ne 'HarmonyPatch') { continue }
    $n++
    $args = @()
    foreach ($a in $ca.ConstructorArguments) {
      if ($a.Value -is [Mono.Cecil.TypeReference]) { $args += ('typeof(' + $a.Value.Name + ')') }
      else { $args += ('"' + $a.Value + '"') }
    }
    Write-Output ('  ' + $t.Name.PadRight(24) + ' -> HarmonyPatch(' + ($args -join ', ') + ')')
  }
}
Write-Output ('HarmonyPatch attribute count = ' + $n)
# confirm the Postfix body really calls into ProbeMod.OnDatabaseLoaded
$found = @()
foreach ($t in $asm.MainModule.Types) {
  foreach ($m in $t.Methods) {
    if (-not $m.HasBody) { continue }
    foreach ($i in $m.Body.Instructions) {
      if ($i.OpCode.Name -eq 'call' -and $i.Operand -ne $null -and $i.Operand.Name -eq 'OnDatabaseLoaded') { $found += ($t.Name + '.' + $m.Name) }
    }
  }
}
Write-Output ('methods calling ProbeMod.OnDatabaseLoaded: ' + (($found | Sort-Object -Unique) -join ', '))
Write-Output ('distinct hooks wired = ' + ($found | Sort-Object -Unique).Count)