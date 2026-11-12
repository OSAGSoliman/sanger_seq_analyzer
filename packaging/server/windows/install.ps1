<#
.SYNOPSIS
  Install the Sanger Seq Analyzer lab server as a Windows service.

.DESCRIPTION
  Run in an elevated PowerShell from the unzipped SangerSeqServer-windows folder:
      powershell -ExecutionPolicy Bypass -File .\install.ps1
  Then create the first admin (the command is printed at the end) and open the address.

  The service runs as NT AUTHORITY\NetworkService by default. On the network it then acts
  as this server's computer account (DOMAIN\SERVERNAME$): give that account Modify rights
  on the lab's shared folders. To use a domain service account or gMSA instead, change the
  "Log On" tab of the service in services.msc after installing, and pass -DataAccount so it
  can write the data folder. See DEPLOY.md.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\SangerSeqServer",
    [string]$DataDir = "$env:ProgramData\SangerSeqServer",
    [int]$Port = 8443,
    [string]$CertFile = "",          # PEM certificate from the hospital CA (HTTPS)
    [string]$KeyFile = "",           # its PEM private key
    [string]$DataAccount = "*S-1-5-20",  # NT AUTHORITY\NETWORK SERVICE (by SID: works in every language)
    [switch]$NoFirewall,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell opened with 'Run as administrator'."
}
$here = $PSScriptRoot
$serviceId = "SangerSeqServer"
if (Get-Service -Name $serviceId -ErrorAction SilentlyContinue) {
    throw "The service is already installed. Use upgrade.ps1 to install a new version."
}

Write-Host "Copying program files to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Recurse -Force "$here\SangerSeqServer" $InstallDir
Copy-Item -Force "$here\SangerSeqServerService.exe", "$here\SangerSeqServerService.xml", "$here\upgrade.ps1", `
    "$here\uninstall.ps1", "$here\DEPLOY.md" $InstallDir
$exe = Join-Path $InstallDir "SangerSeqServer\SangerSeqServer.exe"

Write-Host "Preparing the data folder $DataDir"
New-Item -ItemType Directory -Force -Path $DataDir, "$DataDir\logs", "$DataDir\certs" | Out-Null
# only SYSTEM, administrators and the service account may read the data (it holds patient data)
& icacls $DataDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${DataAccount}:(OI)(CI)M" | Out-Null
& $exe server --data $DataDir check | Out-Null   # writes config.toml and the database

$config = Join-Path $DataDir "config.toml"
$text = Get-Content -Raw $config
$text = $text -replace '(?m)^port = \d+', "port = $Port"
if ($CertFile -and $KeyFile) {
    Copy-Item -Force $CertFile "$DataDir\certs\server.crt"
    Copy-Item -Force $KeyFile "$DataDir\certs\server.key"
    $text = $text -replace '(?m)^tls_cert = ".*"', 'tls_cert = "certs/server.crt"'
    $text = $text -replace '(?m)^tls_key = ".*"', 'tls_key = "certs/server.key"'
}
Set-Content -Path $config -Value $text -Encoding UTF8

# the service definition (WinSW): restart on failure, rotate the console log
$xml = Join-Path $InstallDir "SangerSeqServerService.xml"
(Get-Content -Raw $xml).Replace("@DATA@", $DataDir) | Set-Content -Path $xml -Encoding UTF8

if (-not $NoFirewall) {
    if (-not (Get-NetFirewallRule -DisplayName "Sanger Seq Analyzer server" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Sanger Seq Analyzer server" -Direction Inbound -Protocol TCP `
            -LocalPort $Port -Action Allow -Profile Domain, Private | Out-Null
    }
}

Write-Host "Installing the service"
& (Join-Path $InstallDir "SangerSeqServerService.exe") install
if ($LASTEXITCODE) { throw "The service could not be installed (WinSW exit code $LASTEXITCODE)." }
if (-not $NoStart) {
    & (Join-Path $InstallDir "SangerSeqServerService.exe") start
    if ($LASTEXITCODE) { throw "The service did not start; see $DataDir\logs." }
}

$scheme = if ($CertFile) { "https" } else { "http" }
Write-Host ""
Write-Host "Installed. Next steps:"
Write-Host "  1. Create the first admin:"
Write-Host "       & `"$exe`" server --data `"$DataDir`" create-admin"
Write-Host "  2. Open ${scheme}://$([System.Net.Dns]::GetHostEntry('').HostName):$Port/ and sign in."
if (-not $CertFile) {
    Write-Host "  Without -CertFile/-KeyFile the server uses plain HTTP: ask IT for a certificate (DEPLOY.md)."
}
