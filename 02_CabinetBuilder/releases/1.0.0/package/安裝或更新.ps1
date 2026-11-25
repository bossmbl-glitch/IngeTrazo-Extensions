# Run from the extracted package while IngeTrazo is closed.
[CmdletBinding()]
param([string]$PluginDirectory = (Join-Path $env:APPDATA 'ingetrazo\plugins'))
$ErrorActionPreference = 'Stop'
$packageRoot = $PSScriptRoot
$entry = 'lime_integrated_tools_ingetrazo.py'
$assets = 'lime_integrated_assets'
if (Get-Process -Name '*ingetrazo*' -ErrorAction SilentlyContinue) {
    throw '請先關閉 IngeTrazo，再執行安裝。'
}
$manifest = Get-Content -LiteralPath (Join-Path $packageRoot 'MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($item in $manifest.files.PSObject.Properties) {
    $relative = $item.Name
    if ($relative.Contains('..') -or [IO.Path]::IsPathRooted($relative)) { throw '安裝清單路徑無效。' }
    $file = Join-Path $packageRoot $relative
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "缺少安裝檔：$relative" }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value) {
        throw "安裝檔校驗失敗：$relative"
    }
}
New-Item -ItemType Directory -Force -Path $PluginDirectory | Out-Null
$backupRoot = Join-Path $env:LOCALAPPDATA ('LimeToolbox\Backups\' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$hadEntry = Test-Path -LiteralPath (Join-Path $PluginDirectory $entry)
$hadAssets = Test-Path -LiteralPath (Join-Path $PluginDirectory $assets)
if ($hadEntry) { Copy-Item -LiteralPath (Join-Path $PluginDirectory $entry) -Destination $backupRoot }
if ($hadAssets) { Copy-Item -LiteralPath (Join-Path $PluginDirectory $assets) -Destination $backupRoot -Recurse }
try {
    # Only the two packaged plugin paths are installed. User catalogs live elsewhere.
    if ($hadAssets) { Remove-Item -LiteralPath (Join-Path $PluginDirectory $assets) -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $packageRoot $assets) -Destination $PluginDirectory -Recurse
    Copy-Item -LiteralPath (Join-Path $packageRoot $entry) -Destination $PluginDirectory -Force
} catch {
    if (Test-Path -LiteralPath (Join-Path $PluginDirectory $assets)) { Remove-Item -LiteralPath (Join-Path $PluginDirectory $assets) -Recurse -Force }
    if ($hadAssets) { Copy-Item -LiteralPath (Join-Path $backupRoot $assets) -Destination $PluginDirectory -Recurse }
    if ($hadEntry) { Copy-Item -LiteralPath (Join-Path $backupRoot $entry) -Destination $PluginDirectory -Force }
    elseif (Test-Path -LiteralPath (Join-Path $PluginDirectory $entry)) { Remove-Item -LiteralPath (Join-Path $PluginDirectory $entry) -Force }
    throw
}
Write-Host ('已安裝 ' + $manifest.version + '。舊版備份：' + $backupRoot)
Write-Host '請重新開啟 IngeTrazo。使用者配件資料庫未被移動或覆寫。'
