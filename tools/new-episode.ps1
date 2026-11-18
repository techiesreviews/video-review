<#
.SYNOPSIS
Creates one home folder for a new video under D:\Published, named after its Orbit card,
for example "VID-7 Black Friday packs". Same layout as Video 01.

.DESCRIPTION
Everything for that video lives in this one folder: the OBS recordings, the Resolve project,
the renders, the chapters and the Shorts. The README.md it writes says which subfolder holds what.
It only adds missing folders: existing folders and files are never renamed, moved or overwritten.

.EXAMPLE
.\tools\new-episode.ps1 -Card VID-7 -Title "Black Friday packs"
Creates D:\Published\VID-7 Black Friday packs.

.EXAMPLE
.\tools\new-episode.ps1 -Card VID-7 -Title "Black Friday packs" -WhatIf
Prints what it would create and changes nothing.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Card,
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Root = 'D:\Published'
)
$ErrorActionPreference = 'Stop'

$Card = $Card.Trim().ToUpperInvariant()
if ($Card -notmatch '^[A-Z][A-Z0-9]*-[0-9]+$') {
    throw "Card '$Card' does not look like an Orbit card key (for example VID-7)."
}
$Title = $Title.Trim()
# The key may be typed in the title as well ("VID-7 Black Friday packs"): keep it once, at the front.
$keyInTitle = '^' + [regex]::Escape($Card) + '(?![0-9])[\s:-]*'
if ($Title -match $keyInTitle) { $Title = $Title.Substring($Matches[0].Length).Trim() }
$bad = [IO.Path]::GetInvalidFileNameChars()
if ($Title -eq '' -or $Title.IndexOfAny($bad) -ge 0) {
    throw "Title '$Title' cannot be used as a folder name (no \ / : * ? `" < > |)."
}
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "Root folder '$Root' does not exist."
}

$name = "$Card $Title"
$episode = Join-Path $Root $name
$cameraRecordings = Join-Path $episode '06 Source recordings\camera recordings'

# Same top folders and fixed subfolders as Video 01. Dated work folders are made later, per job.
$folders = @(
    '01 Final',
    '01 Final\Shorts',
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
# $name

- Orbit card: $Card
- Created: $created
- Published: (not yet)
- YouTube: (not yet)
- Final video: (not yet)

## Folders

Everything for this video stays in this one folder.

| Folder | Holds |
|---|---|
| 01 Final | renders: 4K master, review copy, SHA256SUMS, render notes; ``Shorts`` holds the 9:16 Shorts |
| 02 Publishing | YouTube description and chapters, upload and social records |
| 03 Thumbnails and social images | thumbnails and social images |
| 04 Edit project | the Resolve project, ``a-cut-and-review-records``, ``final-cut-records`` (.drp exports), ``html-reel-plates``, ``referenced-media`` |
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
