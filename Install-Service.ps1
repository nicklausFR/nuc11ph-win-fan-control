# SPDX-License-Identifier: GPL-3.0-only
#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)][string]$OwnerSid,
    [switch]$AllowWrite
)
$ErrorActionPreference = 'Stop'
$taskSid = [System.Security.Principal.SecurityIdentifier]::new($OwnerSid)
$OwnerSid = $taskSid.Value
if ($OwnerSid -eq 'S-1-5-18') { throw "Indiquer le SID du compte utilisateur de l’interface Python." }
if (Get-Service -Name 'NucControlEngine' -ErrorAction SilentlyContinue) { throw 'Le service existe déjà. Le désinstaller avant cette installation.' }
$taskSource = Join-Path $PSScriptRoot 'nuc-engine.exe'
if (-not (Test-Path -LiteralPath $taskSource)) { $taskSource = Join-Path $PSScriptRoot 'fan-control\build\nuc-engine.exe' }
if (-not (Test-Path -LiteralPath $taskSource)) { throw "Compiler le moteur ou utiliser l’archive binaire." }
$taskProgramFiles = [System.Environment]::GetFolderPath('ProgramFiles')
$taskTargetDir = [IO.Path]::GetFullPath((Join-Path $taskProgramFiles 'NucControl'))
if (-not $taskTargetDir.StartsWith($taskProgramFiles.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Destination invalide.' }
$taskCheckPath = $taskTargetDir
while ($taskCheckPath) {
    if (Test-Path -LiteralPath $taskCheckPath) {
        if ((Get-Item -LiteralPath $taskCheckPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Point de réanalyse interdit dans le chemin du service.' }
    }
    $taskCheckPath = [IO.Path]::GetDirectoryName($taskCheckPath)
}
New-Item -ItemType Directory -Path $taskTargetDir -Force | Out-Null
$taskAcl = [System.Security.AccessControl.DirectorySecurity]::new()
$taskAcl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;BU)')
Set-Acl -LiteralPath $taskTargetDir -AclObject $taskAcl
$taskTarget = Join-Path $taskTargetDir 'nuc-engine.exe'
if (Test-Path -LiteralPath $taskTarget) { throw "Un binaire existe déjà dans la destination. Ne pas l’écraser implicitement." }
Copy-Item -LiteralPath $taskSource -Destination $taskTarget
$taskBinary = '"'+$taskTarget+'" service --owner-sid '+$OwnerSid
if ($AllowWrite) { $taskBinary += ' --allow-write' }
New-Service -Name 'NucControlEngine' -DisplayName 'NUC Control Engine' -BinaryPathName $taskBinary -StartupType Manual -Description 'Moteur C local pour le NUC vérifié, avec récupération indépendante.' | Out-Null
Write-Output 'Service créé, en démarrage manuel et arrêté. Pour démarrer : Start-Service NucControlEngine'
