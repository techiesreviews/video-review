<#
.SYNOPSIS
Transcribes one recording with local Whisper large-v3 on the GPU (no HappyScribe, no credits).

.EXAMPLE
.\tools\transcribe-local.ps1 "D:\Published\Video 02 - ...\06 Source recordings\camera recordings\take.mkv"

.EXAMPLE
.\tools\transcribe-local.ps1 take.mkv -Start 60 -Duration 30 -OutDir $env:TEMP\tx-test
Transcribes only 30 seconds starting at 1:00.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Media,
    [string]$OutDir,
    [double]$Start = 0,
    [double]$Duration = 0,
    [string]$Python = $(if ($env:VIDEO_ACUT_PYTHON) { $env:VIDEO_ACUT_PYTHON } else { Join-Path $env:USERPROFILE '.cache\video-acut-venv\Scripts\python.exe' })
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python with stable-ts and CUDA PyTorch not found at '$Python'. Set VIDEO_ACUT_PYTHON or pass -Python."
}
$script = Join-Path $PSScriptRoot 'transcribe_local.py'
$argsList = @($script, $Media, '--start', $Start, '--duration', $Duration)
if ($OutDir) { $argsList += @('--out-dir', $OutDir) }
& $Python @argsList
exit $LASTEXITCODE
