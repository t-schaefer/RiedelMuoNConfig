<#
.SYNOPSIS
  Starts or stops the MuoN Config Tool service (the scheduled task
  "MuoNConfigToolService"). Double-click Start-MuoNConfig-Service.bat or
  Stop-MuoNConfig-Service.bat in the repo root; they ask for administrator
  rights themselves.

  Stop only stops the running tool. The task stays installed and enabled, so
  the tool still comes back at the next boot or when you run the Start file.
  To remove it completely, use config-tool\uninstall-task-windows.ps1.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

$RepoDir = $PSScriptRoot
$TaskName = "MuoNConfigToolService"
$ServerPy = Join-Path $RepoDir "config-tool\server.py"
$Port = 8091

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Task '$TaskName' is not installed. Double-click Install-MuoNConfig-Service.bat first."
}

# Only this tool's python process, matched by its full server.py path, so the
# Fusion dashboard's server.py is never touched.
function Get-ToolProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$ServerPy*" }
}

function Test-PortListening {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Stop-Tool {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Task Scheduler can keep reporting "Running" for a while after a stop.
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-ScheduledTask -TaskName $TaskName).State -eq "Running" -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    $left = @(Get-ToolProcess)
    if ($left.Count -gt 0) {
        # Killing the process makes the task look failed, and the task is set
        # to restart on failure (every minute). Disable it while the process
        # is killed so it cannot come straight back, then enable it again so
        # it still starts at the next boot.
        Write-Host "Tool process is still running - stopping it directly..." -ForegroundColor Yellow
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        try {
            foreach ($p in $left) {
                Write-Host "  Stopping PID $($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 2
        } finally {
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
        }
    }
}

function Start-Tool {
    $task = Get-ScheduledTask -TaskName $TaskName
    if ($task.State -eq "Disabled") {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    if ($task.State -eq "Running" -and (Get-ToolProcess) -and (Test-PortListening)) {
        Write-Host "The tool is already running."
        return
    }
    # Task Scheduler may think the task is running although its process is
    # gone, and would then refuse a new start. Clear that state first.
    if ($task.State -eq "Running") { Stop-Tool }

    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        throw "Port $Port is already in use (PID $($busy.OwningProcess -join ', ')). If that is Start-MuoNConfig.bat, close its window first."
    }

    Write-Host "Starting task '$TaskName'..."
    Start-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-PortListening) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
}

if ($Action -eq "Start") { Start-Tool } else { Stop-Tool }

$state = (Get-ScheduledTask -TaskName $TaskName).State
$listening = Test-PortListening
$running = [bool](Get-ToolProcess)
Write-Host ""
Write-Host "Task state: $state | tool process running: $running | port $Port listening: $listening"

if ($Action -eq "Start") {
    if ($listening) {
        Write-Host "Reachable at http://localhost:$Port/ (from other PCs: http://$($env:COMPUTERNAME):$Port/)"
    } else {
        Write-Host "The tool is not listening on port $Port - check config-tool\config-tool.log." -ForegroundColor Yellow
        exit 1
    }
} else {
    if ($listening -or $running) {
        Write-Host "The tool is still running." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Stopped. It starts again at the next boot, or with Start-MuoNConfig-Service.bat."
}
