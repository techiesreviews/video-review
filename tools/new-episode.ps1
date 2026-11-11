<#
.SYNOPSIS
Creates a new episode folder with the same layout as Video 01 under D:\Published.

.EXAMPLE
.\tools\new-episode.ps1 -Title "Video 02 - Who Changed Your WordPress Site"

.EXAMPLE
.\tools\new-episode.ps1 -Title "Video 02 - Who Changed Your WordPress Site" -WhatIf
Prints what it would create and changes nothing.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Root = 'D:\Published'
)
$ErrorActionPreference = 'Stop'

$bad = [IO.Path]::GetInvalidFileNameChars()
if ($Title.Trim() -eq '' -or $Title.IndexOfAny($bad) -ge 0) {
    throw "Title '$Title' cannot be used as a folder name (no \ / : * ? `" < > |)."
}
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "Root folder '$Root' does not exist."
}

$episode = Join-Path $Root $Title.Trim()
$cameraRecordings = Join-Path $episode '06 Source recordings\camera recordings'

# Same top folders and fixed subfolders as Video 01. Dated work folders are made later, per job.
$folders = @(
    '01 Final',
    '02 Publishing',
    '03 Thumbnails and social images',
    '04 Edit project',
    '04 Edit project\a-cut-and-review-records',
    '04 Edit project\final-cut-records',
    '04 Edit project\html-reel-plates',
    '04 Edit project\referenced-media',
    '05 Script and notes',
    '06 Source recordings',
    '06 Source recordings\camera recordings'
)

if (Test-Path -LiteralPath $episode) {
    $existing = @(Get-ChildItem -LiteralPath $episode -Force)
    if ($existing.Count -gt 0) {
        Write-Warning "'$episode' already exists and has $($existing.Count) item(s). Only missing folders are added; nothing is overwritten."
    }
}

foreach ($f in $folders) {
    $path = Join-Path $episode $f
    if (Test-Path -LiteralPath $path) { continue }
    if ($PSCmdlet.ShouldProcess($path, 'Create folder')) {
        New-Item -ItemType Directory -Path $path | Out-Null
    }
}

$readme = Join-Path $episode 'README.md'
$created = Get-Date -Format 'yyyy-MM-dd'
$readmeText = @"
# $($Title.Trim())

- Created: $created
- Published: (not yet)
- YouTube: (not yet)
- Final video: (not yet)

## Folders

| Folder | Holds |
|---|---|
| 01 Final | 4K master, review copy, SHA256SUMS, render notes |
| 02 Publishing | YouTube description, upload and social records |
| 03 Thumbnails and social images | thumbnails and social images |
| 04 Edit project | ``a-cut-and-review-records``, ``final-cut-records`` (.drp exports), ``html-reel-plates``, ``referenced-media`` |
| 05 Script and notes | script, sources, sponsor notes |
| 06 Source recordings | ``camera recordings`` (OBS records here; original .mkv files, only copies), dated working folders |

## Recording

OBS recording path: ``$cameraRecordings``
"@

if (Test-Path -LiteralPath $readme) {
    Write-Warning "README.md already exists; left as it is."
} elseif ($PSCmdlet.ShouldProcess($readme, 'Write README.md')) {
    [IO.File]::WriteAllText($readme, $readmeText, (New-Object Text.UTF8Encoding($false)))
}

if ($WhatIfPreference) {
    Write-Host "Dry run. Nothing was created."
} else {
    Write-Host "Episode folder ready: $episode"
}
Write-Host "Set the OBS recording path to:"
Write-Output $cameraRecordings
