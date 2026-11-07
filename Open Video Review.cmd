@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-review.ps1"
if errorlevel 1 (
  echo.
  echo Video Review did not open. The reason is above.
  if not defined VIDEO_REVIEW_NO_PAUSE pause
  exit /b 1
)
