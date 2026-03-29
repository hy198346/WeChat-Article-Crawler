param(
  [string]$ProfileDir = "./my_wechat_profile",
  [switch]$Headless,
  [int]$MaxWait = 600,
  [string]$Account = "",
  [string]$AccountsFile = "",
  [ValidateSet("push-latest-all","extract-latest","refresh-only")]
  [string]$RunMode = "push-latest-all",
  [string]$TargetUrl = "",
  [string]$ServerChanSendKey = "",
  [switch]$NoForce
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if ($ServerChanSendKey -ne "") {
  $env:SERVERCHAN_SENDKEY = $ServerChanSendKey
}

if ($AccountsFile -eq "") {
  $AccountsFile = ".\accounts.json"
}

# Build argument list
$argList = @(
  "-ProfileDir", $ProfileDir
  "-MaxWait", $MaxWait
  "-AccountsFile", $AccountsFile
  "-RunMode", $RunMode
)

if ($Headless) {
  $argList += "-Headless"
}

if ($Account -ne "") {
  $argList += @("-Account", $Account)
}

if ($TargetUrl -ne "") {
  $argList += @("-TargetUrl", $TargetUrl)
}

if (-not $NoForce) {
  $argList += "-Force"
}

# 直接执行 run_project.ps1 脚本
$runProjectPath = Join-Path $PSScriptRoot "run_project.ps1"
& "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$runProjectPath" @argList
