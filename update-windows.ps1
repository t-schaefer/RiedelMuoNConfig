<#
.SYNOPSIS
  Updates this RiedelMuoNConfig checkout from git and restarts the MuoN
  Config Tool service. Double-click Update-MuoNConfig.bat, which asks for
  administrator rights itself.

  devices.json, profiles/site-default.json and backups/ are not tracked in
  git, so `git pull` never touches them.
#>

$ErrorActionPreference = "Stop"

$RepoDir = $PSScriptRoot
$TaskName = "MuoNConfigToolService"
$ServerPy = Join-Path $RepoDir "config-tool\server.py"

if (-not (Test-Path (Join-Path $RepoDir ".git"))) { throw "$RepoDir is not a git checkout." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git was not found on PATH." }

$taskExists = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
if ($taskExists) {
    Write-Host "Stopping task '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Task Scheduler can report "Running" for a while after a stop and then
    # refuses to start a new instance. Wait, then kill only THIS tool's
    # python process (matched by its full server.py path, so the Fusion
    # dashboard's server.py is never hit).
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-ScheduledTask -TaskName $TaskName).State -eq "Running" -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$ServerPy*" } |
        ForEach-Object {
            Write-Host "  Stopping PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
} else {
    Write-Host "Task '$TaskName' not installed - only updating the files." -ForegroundColor Yellow
}

Push-Location $RepoDir
try {
    Write-Host "Pulling latest changes..."
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed (exit code $LASTEXITCODE) - the service was NOT restarted." }
} finally {
    Pop-Location
}

if ($taskExists) {
    Write-Host "Starting task '$TaskName'..."
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    $state = (Get-ScheduledTask -TaskName $TaskName).State
    Write-Host "Task state: $state"
    if ($state -ne "Running") { Write-Host "Not running - check config-tool\config-tool.log." -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "Update complete."
