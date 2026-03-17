$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $here "secure_delete_context.py"

if (-not (Test-Path -LiteralPath $scriptPath)) {
  throw "File not found: $scriptPath"
}

$subKey = "Software\Classes\*\shell\SecureDeleteOverwrite"
$cmdSubKey = "$subKey\command"

$k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($subKey)
$caption = [string]::new([char[]]@(
  0x0423,0x0434,0x0430,0x043B,0x0438,0x0442,0x044C,0x0020,
  0x0441,0x0020,
  0x043F,0x0435,0x0440,0x0435,0x0437,0x0430,0x043F,0x0438,0x0441,0x044C,0x044E
))
$k.SetValue("", $caption, [Microsoft.Win32.RegistryValueKind]::String)
$k.SetValue("Icon", "imageres.dll,-5302", [Microsoft.Win32.RegistryValueKind]::String)
$k.SetValue("MultiSelectModel", "Player", [Microsoft.Win32.RegistryValueKind]::String)
$k.Close()

$ck = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($cmdSubKey)
$cmd = "cmd.exe /c `"python `"$scriptPath`" `"%1`"`""
$ck.SetValue("", $cmd, [Microsoft.Win32.RegistryValueKind]::String)
$ck.Close()

Write-Host "Done. Context menu item installed for current user."

