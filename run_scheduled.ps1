param(
  [string]$ProfileDir = "./my_wechat_profile",
  [switch]$Headless,
  [int]$MaxWait = 600,
  [string]$Account = "",
  [string]$AccountsFile = "",
  [ValidateSet("push-latest-all","extract-latest","refresh-only")]
  [string]$RunMode = "push-latest-all",
  [string]$TargetUrl = "",
  [string]$ServerChanSendKey = ""
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if ($ServerChanSendKey -ne "") {
  $env:SERVERCHAN_SENDKEY = $ServerChanSendKey
}

if ($AccountsFile -eq "") {
  $AccountsFile = ".\\accounts.json"
}

$headlessArg = $(if ($Headless) { "-Headless" } else { "" })

powershell -NoProfile -ExecutionPolicy Bypass -File .\run_project.ps1 -ProfileDir $ProfileDir $headlessArg -MaxWait $MaxWait -Account $Account -AccountsFile $AccountsFile -RunMode $RunMode -TargetUrl $TargetUrl
