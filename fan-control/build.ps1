# SPDX-License-Identifier: GPL-3.0-only
param([switch]$TestBackend, [switch]$Analyze)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskBuild = Join-Path $taskRoot 'build'
New-Item -ItemType Directory -Path $taskBuild -Force | Out-Null
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw 'Ouvrir un terminal développeur Visual Studio x64 (MSVC + SDK Windows).'
}
$taskCompiler = (Get-Command cl.exe).Source
$taskMsvcRoot = (Get-Item -LiteralPath $taskCompiler).Directory.Parent.Parent.Parent.FullName
$taskSdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
$taskSdkVersion = Get-ChildItem -LiteralPath (Join-Path $taskSdkRoot 'Include') -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'um\Windows.h') } |
    Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
if (-not $taskSdkVersion) { throw 'SDK Windows introuvable.' }
$env:INCLUDE = @((Join-Path $taskMsvcRoot 'include'), (Join-Path $taskSdkVersion.FullName 'ucrt'), (Join-Path $taskSdkVersion.FullName 'shared'), (Join-Path $taskSdkVersion.FullName 'um')) -join ';'
$env:LIB = @((Join-Path $taskMsvcRoot 'lib\x64'), (Join-Path $taskSdkRoot "Lib\$($taskSdkVersion.Name)\ucrt\x64"), (Join-Path $taskSdkRoot "Lib\$($taskSdkVersion.Name)\um\x64")) -join ';'
$taskName = if ($TestBackend) { 'nuc-engine-test' } else { 'nuc-engine' }
$taskObjects = Join-Path $taskBuild $taskName
New-Item -ItemType Directory -Path $taskObjects -Force | Out-Null
$taskSources = @('main.c','policy.c','hardware.c','guard.c','server.c','selftest.c') | ForEach-Object { Join-Path $taskRoot "src\$_" }
$taskArgs = @('/nologo','/std:c17','/W4','/WX','/O2','/MT','/GS','/sdl','/guard:cf','/utf-8','/DUNICODE','/D_UNICODE','/D_WIN32_WINNT=0x0602',"/Fo$taskObjects\", "/Fe$taskBuild\$taskName.exe")
if ($TestBackend) { $taskArgs += '/DNUC_TESTING' }
if ($Analyze) { $taskArgs += @('/analyze','/analyze:external-') }
& cl.exe @taskArgs @taskSources /link /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA /guard:cf setupapi.lib advapi32.lib bcrypt.lib psapi.lib powrprof.lib
if ($LASTEXITCODE -ne 0) { throw "Compilation échouée ($LASTEXITCODE)." }
& (Join-Path $taskBuild "$taskName.exe") self-test
if ($LASTEXITCODE -ne 0) { throw 'Échec des tests C.' }
