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

# 检查执行策略
$execPolicy = Get-ExecutionPolicy
if ($execPolicy -eq "Restricted" -or $execPolicy -eq "AllSigned") {
  Write-Host "[WARNING] Current execution policy is '$execPolicy'. This script may fail to run." -ForegroundColor Yellow
  Write-Host "[INFO] To fix this, run PowerShell as Administrator and execute:" -ForegroundColor Yellow
  Write-Host "  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Cyan
  Write-Host ""
}

$who = (whoami)
Write-Host "[INFO] Current user: $who" -ForegroundColor Green
Write-Host "[INFO] Script directory: $PSScriptRoot" -ForegroundColor Green
Write-Host "[INFO] Run level: $RunLevel" -ForegroundColor Green
Write-Host ""

$pwsh = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$scriptPath = Join-Path $PSScriptRoot "run_scheduled.ps1"

Write-Host "[INFO] PowerShell executable: $pwsh" -ForegroundColor Green
Write-Host "[INFO] Scheduled task script: $scriptPath" -ForegroundColor Green

if (-not (Test-Path $scriptPath)) {
  throw "run_scheduled.ps1 not found: $scriptPath"
}
Write-Host "[INFO] run_scheduled.ps1 exists." -ForegroundColor Green

# 使用 $argList 代替 $args 避免与 PowerShell 内置变量冲突
$argList = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$scriptPath`"",
  "-ProfileDir", "`"$ProfileDir`"",
  "-MaxWait", "$MaxWait",
  "-AccountsFile", "`"$AccountsFile`"",
  "-RunMode", "$RunMode"
)

if ($Headless) { $argList += "-Headless" }
if ($TargetUrl -ne "") { $argList += @("-TargetUrl", "`"$TargetUrl`"") }
if ($ServerChanSendKey -ne "") { $argList += @("-ServerChanSendKey", "`"$ServerChanSendKey`"") }

$action = New-ScheduledTaskAction -Execute $pwsh -Argument ($argList -join " ") -WorkingDirectory $PSScriptRoot

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

Write-Host "`n[INFO] Creating scheduled task: $TaskName" -ForegroundColor Cyan
Write-Host "[INFO] Task action: $pwsh $($argList -join " ")" -ForegroundColor Cyan

try {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing) {
    Write-Host "[INFO] Removing existing task: $TaskName" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false | Out-Null
  }
} catch {
  Write-Host "[INFO] No existing task to remove." -ForegroundColor Green
}

try {
  Write-Host "[INFO] Registering new task..." -ForegroundColor Cyan
  Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
  Write-Host "[SUCCESS] Task registered successfully!" -ForegroundColor Green
} catch {
  $errMsg = $_.Exception.Message
  Write-Host "`n[ERROR] Register-ScheduledTask failed!" -ForegroundColor Red
  Write-Host "[ERROR] Details: $errMsg" -ForegroundColor Red
  
  if ($errMsg -like "*access*denied*" -or $errMsg -like "*拒绝访问*") {
    Write-Host "`n[SOLUTION] This error usually means you need Administrator privileges." -ForegroundColor Yellow
    Write-Host "[SOLUTION] Try one of the following:" -ForegroundColor Yellow
    Write-Host "  1. Run PowerShell as Administrator (右键 -> 以管理员身份运行)" -ForegroundColor Cyan
    Write-Host "  2. Use -RunLevel Limited instead of -RunLevel Highest" -ForegroundColor Cyan
  }
  
  throw "Register-ScheduledTask failed. Details: $errMsg"
}

Write-Host "`n[SUCCESS] Scheduled task '$TaskName' created successfully!" -ForegroundColor Green
Write-Host "[INFO] Task will run at: 08:00, 12:00, 16:00, 20:00, 00:00 daily" -ForegroundColor Green
Get-ScheduledTask -TaskName $TaskName
