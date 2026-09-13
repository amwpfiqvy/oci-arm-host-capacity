# Register OCI-ARM-Watchdog. ASCII only (Windows PowerShell 5.x).
# Run from an elevated or same-user interactive session:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-task.ps1
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$TaskName = 'OCI-ARM-Watchdog'
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Vbs = Join-Path $Root 'run_watchdog.vbs'
$Watchdog = Join-Path $Root 'oci_workflow_watchdog.py'
$UserId = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $Watchdog)) { throw "missing $Watchdog" }
if (-not (Test-Path $Vbs)) { throw "missing $Vbs" }

function Invoke-Uv {
    param([Parameter(Mandatory=$true)][string[]]$UvArgs)
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw 'uv not found in PATH' }
    & $uv.Source @UvArgs
    if ($LASTEXITCODE -ne 0) { throw "uv $($UvArgs -join ' ') failed ($LASTEXITCODE)" }
}

if (-not (Test-Path $VenvPython)) {
    Invoke-Uv @('venv', $VenvDir, '--python', '3.14')
}
Invoke-Uv @('pip', 'install', '--python', $VenvPython, 'websockets')

$XmlPath = Join-Path $env:TEMP 'OCI-ARM-Watchdog.task.xml'
$Xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$UserId</Author>
    <Description>OCI ARM GitHub workflow watchdog via Chrome CDP 127.0.0.1:9222. Hidden via wscript.</Description>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT2M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$UserId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>//B //Nologo "$Vbs"</Arguments>
      <WorkingDirectory>$Root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
[System.IO.File]::WriteAllText($XmlPath, $Xml, [System.Text.Encoding]::Unicode)

& schtasks.exe /Create /TN $TaskName /XML $XmlPath /F
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed ($LASTEXITCODE)" }
Write-Host "Registered $TaskName -> $Vbs"
& schtasks.exe /Query /TN $TaskName /FO LIST
