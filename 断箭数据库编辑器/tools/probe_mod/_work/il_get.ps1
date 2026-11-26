$ErrorActionPreference='Stop'
Add-Type -Path 'D:\Steam\steamapps\common\broken_arrow\MelonLoader\net6\Mono.Cecil.dll'
$asm=[Mono.Cecil.AssemblyDefinition]::ReadAssembly('D:\Steam\steamapps\common\broken_arrow\Mods\BaModProbe.dll')
foreach($t in $asm.MainModule.Types){
  if($t.Name -ne 'ColumnHandle' -and $t.Name -ne 'RowAccess'){continue}
  Write-Output ('=== TYPE ' + $t.FullName)
  foreach($m in $t.Methods){
    if($m.Name -ne 'Get' -and $m.Name -ne 'ResolveUncached'){continue}
    Write-Output ('  METHOD ' + $m.Name)
    if(-not $m.HasBody){ Write-Output '    (no body)'; continue }
    $h=$m.Body; Write-Output ('    HasExceptionHandlers=' + $h.HasExceptionHandlers)
    foreach($eh in $h.ExceptionHandlers){ Write-Output ('    HANDLER ' + $eh.HandlerType + ' catch=' + $eh.CatchType) }
    foreach($i in $h.Instructions){ Write-Output ('      ' + $i.OpCode.Name + ' ' + $i.Operand) }
  }
}