<#
.SYNOPSIS
  Install a new version of the Sanger Seq Analyzer server over an existing one.

.DESCRIPTION
  Run elevated from the NEW unzipped SangerSeqServer-windows folder:
      powershell -ExecutionPolicy Bypass -File .\upgrade.ps1
  Stops the service, makes a backup of the data, replaces the program files and starts the
  service again (the database is upgraded automatically, after its own backup).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\SangerSeqServer",
    [string]$DataDir = "$env:ProgramData\SangerSeqServer"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$service = Join-Path $InstallDir "SangerSeqServerService.exe"
if (-not (Test-Path $service)) { throw "No installation found in $InstallDir (use install.ps1)." }

Write-Host "Stopping the service"
& $service stop
Start-Sleep -Seconds 3

Write-Host "Backing up the data folder"
& (Join-Path $InstallDir "SangerSeqServer\SangerSeqServer.exe") server --data $DataDir backup
if ($LASTEXITCODE) { & $service start; throw "The backup failed; nothing was changed." }

Write-Host "Replacing the program files"
Remove-Item -Recurse -Force (Join-Path $InstallDir "SangerSeqServer")
Copy-Item -Recurse -Force "$here\SangerSeqServer" $InstallDir
Copy-Item -Force "$here\upgrade.ps1", "$here\uninstall.ps1", "$here\DEPLOY.md" $InstallDir

Write-Host "Starting the service"
& $service start
if ($LASTEXITCODE) { throw "The service did not start; see $DataDir\logs." }
Write-Host "Upgraded."
