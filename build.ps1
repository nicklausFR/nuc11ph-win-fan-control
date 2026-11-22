# SPDX-License-Identifier: GPL-3.0-only
param([switch]$TestBackend, [switch]$Analyze)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'fan-control\build.ps1') -TestBackend:$TestBackend -Analyze:$Analyze
