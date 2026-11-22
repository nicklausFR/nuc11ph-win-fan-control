# SPDX-License-Identifier: GPL-3.0-only
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$taskService = Get-CimInstance Win32_Service -Filter "Name='NucControlEngine'"
if (-not $taskService) { Write-Output 'Service absent.'; return }
$taskProgramFiles = [Environment]::GetFolderPath('ProgramFiles')
$taskTargetDir = [IO.Path]::GetFullPath((Join-Path $taskProgramFiles 'NucControl'))
$taskTarget = Join-Path $taskTargetDir 'nuc-engine.exe'
if (-not $taskTargetDir.StartsWith($taskProgramFiles.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or
    -not $taskService.PathName.StartsWith('"'+$taskTarget+'" service ',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Le service ne correspond pas à la destination attendue. Aucune suppression.'
}
Stop-Service -Name 'NucControlEngine' -ErrorAction Stop
(Get-Service -Name 'NucControlEngine').WaitForStatus('Stopped',[TimeSpan]::FromSeconds(20))
& sc.exe delete NucControlEngine
if ($LASTEXITCODE -ne 0) { throw 'Suppression du service refusée.' }
# Keep the executable: a recovery process might still need it. No recursive deletion.
Write-Output "Service supprimé. Binaire conservé dans $taskTargetDir."
