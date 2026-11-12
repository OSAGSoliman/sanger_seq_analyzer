<#
.SYNOPSIS
  Remove the Sanger Seq Analyzer server service and program files. The data folder is kept
  unless -RemoveData is given (it holds the database, runs and uploaded files).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\SangerSeqServer",
    [string]$DataDir = "$env:ProgramData\SangerSeqServer",
    [switch]$RemoveData
)
$ErrorActionPreference = "Stop"
$service = Join-Path $InstallDir "SangerSeqServerService.exe"
if (Test-Path $service) {
    & $service stop
    Start-Sleep -Seconds 2
    & $service uninstall
}
Get-NetFirewallRule -DisplayName "Sanger Seq Analyzer server" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Set-Location $env:TEMP
Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
if ($RemoveData) { Remove-Item -Recurse -Force $DataDir }
else { Write-Host "The data folder was kept: $DataDir" }
Write-Host "Uninstalled."
