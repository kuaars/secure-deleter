$ErrorActionPreference = "Stop"

$menuKey = "HKCU:\Software\Classes\*\shell\SecureDeleteOverwrite"

if (Test-Path -LiteralPath $menuKey) {
  Remove-Item -LiteralPath $menuKey -Recurse -Force
}

Write-Host "Done. Context menu item removed for current user."

