<#
.SYNOPSIS
YouTube chapters and 5 clip candidates from a live replay's words.json (from transcribe-local.ps1).

.EXAMPLE
.\tools\replay-chapters.ps1 "D:\Live\2026-11-18 tool lab\replay.words.json"
Splits on the longest silences. Writes replay.chapters.txt and replay.clips.json next to it.

.EXAMPLE
.\tools\replay-chapters.ps1 replay.words.json -Sections run-sheet.txt
Places one chapter per run-sheet line where the transcript first says it.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Words,
    [string]$Sections,
    [string]$Python = $(if ($env:VIDEO_ACUT_PYTHON) { $env:VIDEO_ACUT_PYTHON } else { 'python' })
)
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'replay_chapters.py'
$argsList = @($script, $Words)
if ($Sections) { $argsList += @('--sections', $Sections) }
& $Python @argsList
exit $LASTEXITCODE
