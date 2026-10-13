$ErrorActionPreference = 'Stop'
$reviewRoot = $PSScriptRoot
$reviewPort = if ($env:VIDEO_REVIEW_PORT) { [int]$env:VIDEO_REVIEW_PORT } else { 8879 }
$reviewUrl = "http://127.0.0.1:$reviewPort"
try {
    Invoke-WebRequest -Uri "$reviewUrl/api/videos" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    $reviewPython = $env:VIDEO_REVIEW_PYTHON
    $reviewPythonPrefix = @()
    if (-not $reviewPython) {
        $reviewLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        $reviewCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        $resolvePython = Join-Path $env:ProgramFiles 'Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe'
        if ($reviewLauncher) {
            $reviewPython = $reviewLauncher.Source
            $reviewPythonPrefix = @('-3')
        } elseif ($reviewCommand -and $reviewCommand.Source -notmatch '\\Microsoft\\WindowsApps\\') {
            $reviewPython = $reviewCommand.Source
        } elseif (Test-Path -LiteralPath $resolvePython) {
            $reviewPython = $resolvePython
        } else {
            throw 'Install Python 3.11+ or set VIDEO_REVIEW_PYTHON to its executable path.'
        }
    }
    $reviewArguments = $reviewPythonPrefix + @(('"' + (Join-Path $reviewRoot 'server.py') + '"'), '--port', $reviewPort)
    Start-Process -FilePath $reviewPython -ArgumentList $reviewArguments -WorkingDirectory $reviewRoot -WindowStyle Hidden
    $reviewReady = $false
    for ($reviewAttempt = 0; $reviewAttempt -lt 20; $reviewAttempt++) {
        Start-Sleep -Milliseconds 250
        try {
            Invoke-WebRequest -Uri "$reviewUrl/api/videos" -UseBasicParsing -TimeoutSec 1 | Out-Null
            $reviewReady = $true
            break
        } catch {}
    }
    if (-not $reviewReady) { throw 'Video Review did not start. Run python server.py to see the error.' }
}
Start-Process $reviewUrl
