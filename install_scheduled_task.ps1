param(
  [string]$TaskName = "WeChat-Article-Crawler",
  [string]$ProfileDir = "./my_wechat_profile",
  [string]$AccountsFile = "./accounts.json",
  [string]$TargetUrl = "",
  [string]$ServerChanSendKey = "",
  [ValidateSet("push-latest-all","extract-latest","refresh-only")]
  [string]$RunMode = "push-latest-all",
  [ValidateSet("Limited","Highest")]
  [string]$RunLevel = "Limited",
  [switch]$Headless,
  [int]$MaxWait = 600
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$who = (whoami)

$pwsh = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$scriptPath = Join-Path $PSScriptRoot "run_scheduled.ps1"
if (-not (Test-Path $scriptPath)) {
  throw "run_scheduled.ps1 not found: $scriptPath"
}

$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$scriptPath`"",
  "-ProfileDir", "`"$ProfileDir`"",
  "-MaxWait", "$MaxWait",
  "-AccountsFile", "`"$AccountsFile`"",
  "-RunMode", "$RunMode"
)

if ($Headless) { $args += "-Headless" }
if ($TargetUrl -ne "") { $args += @("-TargetUrl", "`"$TargetUrl`"") }
if ($ServerChanSendKey -ne "") { $args += @("-ServerChanSendKey", "`"$ServerChanSendKey`"") }

$action = New-ScheduledTaskAction -Execute $pwsh -Argument ($args -join " ") -WorkingDirectory $PSScriptRoot

$triggers = @(
  (New-ScheduledTaskTrigger -Daily -At 08:00),
  (New-ScheduledTaskTrigger -Daily -At 12:00),
  (New-ScheduledTaskTrigger -Daily -At 16:00),
  (New-ScheduledTaskTrigger -Daily -At 20:00),
  (New-ScheduledTaskTrigger -Daily -At 00:00)
)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel $RunLevel
$task = New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal

try {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false | Out-Null
  }
} catch {
}

try {
  Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
} catch {
  throw "Register-ScheduledTask failed (access denied). Try running PowerShell as Administrator or set -RunLevel Limited. Details: $($_.Exception.Message)"
}
Get-ScheduledTask -TaskName $TaskName
