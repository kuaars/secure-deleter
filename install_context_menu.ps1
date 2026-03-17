$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $here "secure_delete_context.py"

if (-not (Test-Path -LiteralPath $scriptPath)) {
  throw "File not found: $scriptPath"
}

$menuKey = "HKCU:\Software\Classes\*\shell\SecureDeleteOverwrite"
$cmdKey = Join-Path $menuKey "command"

New-Item -Path $menuKey -Force | Out-Null
Set-ItemProperty -Path $menuKey -Name "(default)" -Value "Удалить с перезаписью"
Set-ItemProperty -Path $menuKey -Name "Icon" -Value "imageres.dll,-5302"
Set-ItemProperty -Path $menuKey -Name "MultiSelectModel" -Value "Player"

New-Item -Path $cmdKey -Force | Out-Null
$cmd = "cmd.exe /c `"python `"$scriptPath`" `"%1`"`""
Set-ItemProperty -Path $cmdKey -Name "(default)" -Value $cmd

Write-Host "Done. Context menu item installed for current user."

