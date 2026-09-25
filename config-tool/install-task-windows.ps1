<#
.SYNOPSIS
  Installs the MuoN Config Tool as a Windows Scheduled Task running under
  the SYSTEM account, the same way as the PLS Fusion Dashboard. It starts at
  boot, runs whether or not anyone is logged in, restarts itself if it
  crashes, and is reachable from the network on port 8091.

.NOTES
  Easiest: double-click Install-MuoNConfig-Service.bat in the repo root. It
  asks for administrator rights itself. Or, from an elevated PowerShell:

      cd C:\Apps\RiedelMuoNConfig\config-tool
      powershell -ExecutionPolicy Bypass -File .\install-task-windows.ps1

  The tool has no login and can write to broadcast devices. Anyone who can
  reach this PC on port 8091 can use it.
#>

$ErrorActionPreference = "Stop"

$TaskName = "MuoNConfigToolService"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerPy = Join-Path $ScriptDir "server.py"
$Port = 8091

if (-not (Test-Path $ServerPy)) {
    throw "server.py not found next to this script ($ServerPy)."
}

# pythonw.exe runs without a console window; fall back to python.exe.
$python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python was not found on PATH." }
Write-Host "Using Python at: $($python.Source)"

$fwRuleName = "MuoN Config Tool (TCP $Port)"
if (-not (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "Firewall rule created for TCP port $Port."
} else {
    Write-Host "Firewall rule for TCP port $Port already exists."
}

# A manually started instance (Start-MuoNConfig.bat) would block the port.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port is in use (PID $($busy.OwningProcess -join ', ')) - close the Start-MuoNConfig.bat window first." -ForegroundColor Yellow
}

$action = New-ScheduledTaskAction -Execute $python.Source `
    -Argument "`"$ServerPy`" --host 0.0.0.0 --port $Port" -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found - replacing it."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "MuoN / Fusion config tool - web UI on port $Port. Runs as SYSTEM, no user login required." | Out-Null

Write-Host "Task '$TaskName' registered. Starting it now..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Write-Host "Task state: $((Get-ScheduledTask -TaskName $TaskName).State)"
Write-Host ""
Write-Host "Reachable at: http://localhost:$Port/"
Write-Host "From another machine:  http://$($env:COMPUTERNAME):$Port/"
Write-Host "Logs: $ScriptDir\config-tool.log"
