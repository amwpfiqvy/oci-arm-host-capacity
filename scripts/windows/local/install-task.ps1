# Register OCI-ARM-Grabber. ASCII only (Windows PowerShell 5.x).
# Run from an elevated or same-user interactive session:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-task.ps1
# Requires .env (copy .env.example) and the OCI private key in this dir.
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$TaskName = 'OCI-ARM-Grabber'
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Vbs = Join-Path $Root 'run_grabber.vbs'
$Watchdog = Join-Path $Root 'oci_grabber.py'
$EnvFile = Join-Path $Root '.env'
$UserId = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $Watchdog)) { throw "missing $Watchdog" }
if (-not (Test-Path $Vbs)) { throw "missing $Vbs" }
if (-not (Test-Path $EnvFile)) { throw "missing $EnvFile (copy .env.example and fill in)" }

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
Invoke-Uv @('pip', 'install', '--python', $VenvPython, 'cryptography')

$XmlPath = Join-Path $env:TEMP 'OCI-ARM-Grabber.task.xml'
$Xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$UserId</Author>
    <Description>OCI ARM capacity grabber calling OCI API directly. Hidden via wscript. Auto-disables itself on success.</Description>
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
