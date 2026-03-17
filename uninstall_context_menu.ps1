$ErrorActionPreference = "Stop"

$subKey = "Software\Classes\*\shell\SecureDeleteOverwrite"
$parentKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Classes\*\shell", $true)
if ($parentKey) {
  try { $parentKey.DeleteSubKeyTree("SecureDeleteOverwrite", $false) } catch {}
  $parentKey.Close()
}

Write-Host "Done. Context menu item removed for current user."

