# body.ps1 - on-demand decompile: symbol -> Ghidra C pseudocode, one command, ~10 s.
#
#   powershell -ExecutionPolicy Bypass -File tools\body.ps1 "Units$$Clone"
#   powershell -ExecutionPolicy Bypass -File tools\body.ps1 "Units$$Clone" -Out D:\ba_ghidra\out\x.c
#
# Why PowerShell and not python-spawned ghidra: a process started by the
# Python interpreter cannot write %APPDATA%\ghidra (access denied) nor paths
# outside the repo, so analyzeHeadless refuses to start from there. Running
# from the desktop shell works. (AGENTS 0.17)
#
# ASCII only: Windows PowerShell 5.1 reads .ps1 as ANSI unless it has a BOM.

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true, Position=0)][string]$Symbol,
  [string]$Out
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $repo ".venv\Scripts\python.exe"
$tool = Join-Path $PSScriptRoot "method_body.py"

if (-not (Test-Path $py))   { throw "venv python not found: $py" }
if (-not (Test-Path $tool)) { throw "method_body.py not found: $tool" }

$planArgs = @($tool, "--ghidra-plan", $Symbol)
if ($Out) { $planArgs += @("--ghidra-out", $Out) }
$line = & $py @planArgs
if ($LASTEXITCODE -ne 0) { throw "resolve failed (rc=$LASTEXITCODE): $line" }

$parts = ($line -split "`t")
if ($parts.Count -lt 6) { throw "unexpected plan output: $line" }
$rva      = $parts[0]
$outFile  = $parts[1]
$projDir  = $parts[2]
$projName = $parts[3]
$scripts  = $parts[4]
$bat      = $parts[5]

if (-not (Test-Path $bat)) { throw "analyzeHeadless.bat not found: $bat" }

$sw = [System.Diagnostics.Stopwatch]::StartNew()
& $bat $projDir $projName -process GameAssembly.dll -noanalysis `
    -scriptPath $scripts -postScript DecompOne.java $rva $outFile | Out-Null
$sw.Stop()

if (-not (Test-Path $outFile)) { throw "ghidra produced nothing: $outFile" }

Write-Output ("// src   {0}" -f $outFile)
Write-Output ("// rva   {0}   elapsed {1} ms" -f $rva, [int]$sw.Elapsed.TotalMilliseconds)
Write-Output ""
Get-Content -Path $outFile -Encoding UTF8