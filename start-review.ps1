$ErrorActionPreference = 'Stop'
$reviewRoot = $PSScriptRoot
$reviewPort = if ($env:VIDEO_REVIEW_PORT) { [int]$env:VIDEO_REVIEW_PORT } else { 8879 }

# 'mine' is this copy of the app, 'other' is another copy or program on the port, 'free' is nothing answering.
function Get-PortState([int]$port) {
    try {
        $served = Invoke-WebRequest -Uri "http://127.0.0.1:$port/app.js" -UseBasicParsing -TimeoutSec 2
    } catch {
        if ($_.Exception.Response) { return 'other' }
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect('127.0.0.1', $port)
            $client.Close()
            return 'other'
        } catch { return 'free' }
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    $servedHash = [BitConverter]::ToString($hasher.ComputeHash($served.RawContentStream.ToArray()))
    $localHash = [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes((Join-Path $reviewRoot 'web\app.js'))))
    if ($servedHash -eq $localHash) { 'mine' } else { 'other' }
}

function Find-ReviewPython {
    if ($env:VIDEO_REVIEW_PYTHON) { return @{ Path = $env:VIDEO_REVIEW_PYTHON; Prefix = @() } }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) { return @{ Path = $launcher.Source; Prefix = @('-3') } }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\') { return @{ Path = $command.Source; Prefix = @() } }
    $resolvePython = Join-Path $env:ProgramFiles 'Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe'
    if (Test-Path -LiteralPath $resolvePython) { return @{ Path = $resolvePython; Prefix = @() } }
    throw 'Python was not found. Install Python 3.11 or newer from python.org, then try again.'
}

# The app itself needs only Python. ffprobe (part of ffmpeg) is needed to add a video, so say so before it surprises anyone.
function Test-ReviewTools {
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $found = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue) -or ($user -and ($user -split ';' | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path ([Environment]::ExpandEnvironmentVariables($_)) 'ffprobe.exe')) }))
    if (-not $found) {
        Write-Host 'Note: ffprobe (part of ffmpeg) was not found. The page will open, but adding a video will fail until you install it.'
        Write-Host '      To install it, open PowerShell and run: winget install Gyan.FFmpeg   (then close and reopen this window)'
    }
}

try {
    Test-ReviewTools
    # Another copy on the port would show its older screens, so this copy takes the next free port instead.
    $state = Get-PortState $reviewPort
    while ($state -eq 'other') {
        if ($reviewPort -ge 8899) { throw 'Ports 8879 to 8899 are all in use. Close other copies of Video Review, then try again.' }
        Write-Host "Port $reviewPort is used by another program or another copy of Video Review. Trying the next one."
        $reviewPort++
        $state = Get-PortState $reviewPort
    }
    $reviewUrl = "http://127.0.0.1:$reviewPort"
    if ($state -eq 'free') {
        $python = Find-ReviewPython
        $reviewLog = Join-Path $reviewRoot 'server-error.log'
        $reviewArguments = $python.Prefix + @(('"' + (Join-Path $reviewRoot 'server.py') + '"'), '--port', $reviewPort)
        try {
            $server = Start-Process -FilePath $python.Path -ArgumentList $reviewArguments -WorkingDirectory $reviewRoot -WindowStyle Hidden -RedirectStandardError $reviewLog -PassThru
        } catch {
            throw "Python could not be started ($($python.Path)). Install Python 3.11 or newer from python.org, then try again."
        }
        $reviewReady = $false
        for ($reviewAttempt = 0; $reviewAttempt -lt 40 -and -not $server.HasExited; $reviewAttempt++) {
            Start-Sleep -Milliseconds 250
            if ((Get-PortState $reviewPort) -eq 'mine') { $reviewReady = $true; break }
        }
        if (-not $reviewReady) {
            $detail = if (Test-Path -LiteralPath $reviewLog) { (Get-Content -LiteralPath $reviewLog -Tail 3) -join ' ' } else { '' }
            throw "The Video Review server did not start. $detail"
        }
    }
    Write-Host "Video Review: $reviewUrl"
    if (-not $env:VIDEO_REVIEW_NO_BROWSER) { Start-Process $reviewUrl }
} catch {
    Write-Host ''
    Write-Host "Problem: $($_.Exception.Message)"
    exit 1
}
