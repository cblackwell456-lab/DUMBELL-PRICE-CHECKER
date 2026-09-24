# Schedules the dumbbell price tracker to run every hour on this Windows PC.
# Run from PowerShell in this folder:   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
# Remove it later with:                 Unregister-ScheduledTask -TaskName "Dumbbell Price Tracker"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = $null
foreach ($name in "pythonw", "pyw", "python", "py") {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
}
if (-not $python) {
    Write-Error "Python not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run."
    exit 1
}

$action   = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\tracker.py`"" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "Dumbbell Price Tracker" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Checks PowerBlock dumbbell prices every hour" -Force | Out-Null

Write-Host "Scheduled 'Dumbbell Price Tracker' to run every hour using $python"
Write-Host "Running one check now..."
& $python "$dir\tracker.py"
