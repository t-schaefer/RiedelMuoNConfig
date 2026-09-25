<#
.SYNOPSIS
  Removes the MuoN Config Tool scheduled task and its firewall rule.
  Run from an elevated (Administrator) PowerShell.
#>

$TaskName = "MuoNConfigToolService"
$Port = 8091
$fwRuleName = "MuoN Config Tool (TCP $Port)"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
} else {
    Write-Host "Task '$TaskName' not found."
}

if (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $fwRuleName
    Write-Host "Removed firewall rule '$fwRuleName'."
}
