<#
.SYNOPSIS
Puts a Techies verdict card on the timeline that is open in DaVinci Resolve Studio, with your text filled in.

.DESCRIPTION
Resolve must be open, with Preferences > System > General > External scripting using set to Local.
The card goes on the top video track if it is above V1 and free at that time (or a new track on top). If any
other clip moves, the script removes the card and stops. The text stays editable in Resolve: select the card,
then Inspector > Video.

.EXAMPLE
.\tools\motion-place.ps1 -Tool "Notion Calendar" -Score 4 -Reason "Fast and clean, but tasks need a second app." -At 01:00:12:00

.EXAMPLE
.\tools\motion-place.ps1 -Tool "Cal.com" -Score 4.5 -Reason "Open source." -At 01:02:00:00 -Seconds 3 -Project "VID-7 Black Friday packs"
Refuses to change anything unless that project is the one open in Resolve.
#>
param(
    [Parameter(Mandatory = $true)][string]$Tool,
    [Parameter(Mandatory = $true)][string]$Score,
    [Parameter(Mandatory = $true)][string]$Reason,
    [Parameter(Mandatory = $true)][string]$At,
    [double]$Seconds = 5,
    [string]$Label,
    [string]$Project,
    [string]$Python = $(if ($env:VIDEO_ACUT_PYTHON) { $env:VIDEO_ACUT_PYTHON } else { 'python' })
)
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'motion_place.py'
$argsList = @($script, '--install', '--tool', $Tool, '--score', $Score, '--reason', $Reason, '--at', $At,
    '--seconds', $Seconds)
if ($Label) { $argsList += @('--label', $Label) }
if ($Project) { $argsList += @('--project', $Project) }
$ErrorActionPreference = 'Continue'
& $Python @argsList
exit $LASTEXITCODE
