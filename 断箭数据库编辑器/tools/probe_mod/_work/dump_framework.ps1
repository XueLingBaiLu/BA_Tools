
$ErrorActionPreference='Stop'
$ML = 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6'
Add-Type -Path (Join-Path $ML 'Mono.Cecil.dll')
function Dump($asmPath, $typeNames) {
  $asm = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($asmPath)
  $idx = @{}
  foreach ($t in $asm.MainModule.Types) { $idx[$t.FullName] = $t }
  Write-Output ("##### " + (Split-Path $asmPath -Leaf) + " #####")
  foreach ($tn in $typeNames) {
    if (-not $idx.ContainsKey($tn)) { Write-Output ("  !! NOT FOUND: " + $tn); continue }
    $t = $idx[$tn]
    Write-Output ("=== " + $t.FullName + "  attrs=" + $t.Attributes)
    foreach ($f in $t.Fields) { if ($f.IsPublic -or $f.IsStatic) { Write-Output ("   FIELD  " + $f.FieldType.Name + " " + $f.Name + " static=" + $f.IsStatic) } }
    foreach ($p in $t.Properties) { Write-Output ("   PROP   " + $p.PropertyType.Name + " " + $p.Name) }
    foreach ($m in $t.Methods) {
      if (-not ($m.IsPublic -or $m.IsFamily)) { continue }
      $ps2 = ($m.Parameters | ForEach-Object { $_.ParameterType.Name + " " + $_.Name }) -join ", "
      Write-Output ("   METHOD " + $m.ReturnType.Name + " " + $m.Name + "(" + $ps2 + ") static=" + $m.IsStatic)
    }
  }
}
Dump (Join-Path $ML 'MelonLoader.dll') @(
  'MelonLoader.MelonMod','MelonLoader.MelonLogger','MelonLoader.MelonEnvironment',
  'MelonLoader.MelonInfoAttribute','MelonLoader.MelonGameAttribute','MelonLoader.MelonPriorityAttribute',
  'MelonLoader.MelonOptionalDependenciesAttribute','MelonLoader.MelonAdditionalDependenciesAttribute'
)
Dump (Join-Path $ML '0Harmony.dll') @('HarmonyLib.HarmonyPatch','HarmonyLib.Harmony')
Dump (Join-Path $ML 'Il2CppInterop.Runtime.dll') @(
  'Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase','Il2CppInterop.Runtime.Il2CppType'
)
