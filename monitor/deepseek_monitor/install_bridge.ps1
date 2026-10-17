param(
    [string]$HarnessDirectory = 'D:\Deepseek Harness',
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$bridgePackage = Join-Path $PSScriptRoot 'bridge'
$harnessExe = Join-Path $HarnessDirectory 'DeepSeek Harness.exe'
$harnessCli = Join-Path $HarnessDirectory 'resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js'
$runtimeRoot = Join-Path $PSScriptRoot 'runtime'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'installed-layout.json')) {
    # Match the packaged Python client and JS bridge without writing to the app.
    $localData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
    $runtimeRoot = Join-Path $localData 'MonitorSolo\data\runtime\deepseek'
}
$installerStore = Join-Path $runtimeRoot 'installer-store'
$installerCache = Join-Path $runtimeRoot 'installer-cache'
$harnessDataHome = if ([string]::IsNullOrWhiteSpace($env:DSH_HOME)) { Join-Path $env:USERPROFILE '.dsh' } else { $env:DSH_HOME.Trim() }
if ($harnessDataHome -eq '~') { $harnessDataHome = $env:USERPROFILE }
elseif ($harnessDataHome -match '^~[\\/]') { $harnessDataHome = Join-Path $env:USERPROFILE $harnessDataHome.Substring(2) }
$modulesFile = Join-Path $harnessDataHome 'profiles\desktop\node_modules\.modules.yaml'
if (Test-Path -LiteralPath $modulesFile -PathType Leaf) {
    # Existing profiles must keep their pnpm store; changing it breaks linking.
    # pnpm 11 writes JSON here despite the .yaml filename.
    $modulesText = Get-Content -LiteralPath $modulesFile -Raw -Encoding UTF8
    if ($modulesText.TrimStart().StartsWith('{')) {
        $existingStore = ($modulesText | ConvertFrom-Json).storeDir
    } else {
        $existingStore = [regex]::Match($modulesText, '(?m)^storeDir:\s*(.+?)\s*$').Groups[1].Value.Trim()
        if ($existingStore.StartsWith('"')) { $existingStore = $existingStore | ConvertFrom-Json }
        elseif ($existingStore.StartsWith("'")) { $existingStore = $existingStore.Trim("'").Replace("''", "'") }
    }
    if ([string]::IsNullOrWhiteSpace($existingStore) -or -not [System.IO.Path]::IsPathRooted($existingStore)) {
        throw "Cannot determine the existing pnpm store from $modulesFile"
    }
    $installerStore = if ((Split-Path -Leaf $existingStore) -match '^v\d+$') { Split-Path -Parent $existingStore } else { $existingStore }
}
if (-not (Test-Path -LiteralPath $harnessExe)) { throw "Harness executable not found: $harnessExe" }
if (Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue) {
    throw 'Fully quit DeepSeek Harness (including its tray icon), then run this script again.'
}
$previousNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    # Windows PowerShell does not wait for a GUI-subsystem executable unless its
    # output joins a pipeline. Harness uses that subsystem even in Node mode.
    if ($Uninstall) {
        & $harnessExe --expose-internals $harnessCli plugin --profile desktop remove '@brightmoon/deepseek-monitor-bridge' --store-dir $installerStore --cache-dir $installerCache | Out-Host
    } else {
        # A link keeps both source code and runtime discovery files in this folder.
        & $harnessExe --expose-internals $harnessCli plugin --profile desktop add "link:$bridgePackage" --offline --store-dir $installerStore --cache-dir $installerCache | Out-Host
    }
    if ($LASTEXITCODE -ne 0) { throw "Harness plugin command failed with exit code $LASTEXITCODE" }
} finally {
    $env:ELECTRON_RUN_AS_NODE = $previousNodeMode
}
Write-Output 'Done. Restart DeepSeek Harness and enable its source in Monitor Solo.'
