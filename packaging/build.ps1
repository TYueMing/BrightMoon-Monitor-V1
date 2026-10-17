param([switch]$CreateInstaller, [string]$InnoCompiler)
$ErrorActionPreference = 'Stop'
$soloRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $soloRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run prepare_build.ps1 with the documented Python first.' }
$release = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$detected = & $python -I -c 'import platform,sys; print(platform.python_version()+"/"+platform.machine()+"/"+sys.platform)'
if ($LASTEXITCODE -ne 0 -or $detected.Trim() -ne "$($release.python)/AMD64/win32") {
    throw "Build environment must be CPython $($release.python) x64 on Windows."
}
& $python -I (Join-Path $PSScriptRoot 'verify_dependencies.py')
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
if ($CreateInstaller -and (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler))) {
    throw 'Provide the installed Inno Setup 6.7.3 ISCC.exe path.'
}
Remove-Item Env:TCL_LIBRARY -ErrorAction SilentlyContinue
Remove-Item Env:TK_LIBRARY -ErrorAction SilentlyContinue
Push-Location $soloRoot
try {
    # Tests only use fixtures and temporary data; they never approve a live agent.
    $reports = Join-Path $soloRoot '.artifacts'
    New-Item -ItemType Directory -Force -Path $reports | Out-Null
    $report = Join-Path $reports 'release-tests.xml'
    $testData = Join-Path $reports ('release-test-data-' + [guid]::NewGuid().ToString('N'))
    & $python -I -m pytest tests -q -rs -p no:cacheprovider "--basetemp=$testData" "--junitxml=$report"
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build cancelled.' }
    [xml]$testResults = Get-Content -LiteralPath $report -Raw -Encoding UTF8
    if ($testResults.SelectNodes('//testcase/skipped').Count -gt 0) {
        throw 'Tests were skipped; verify native Tk support before building a release.'
    }
    & $python -I -m PyInstaller --noconfirm --distpath (Join-Path $soloRoot 'dist') --workpath (Join-Path $soloRoot 'build') (Join-Path $PSScriptRoot 'MonitorSolo.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $frozenExe = Join-Path $soloRoot 'dist\MonitorSolo\MonitorSolo.exe'
    $smokeReport = Join-Path $reports 'frozen-smoke.json'
    $smoke = Start-Process -FilePath $frozenExe -ArgumentList @('--self-test', ('"'+$smokeReport+'"')) -WindowStyle Hidden -PassThru
    if (-not $smoke.WaitForExit(60000)) { throw 'Frozen self-test timed out; installer build cancelled.' }
    $smoke.Refresh()
    if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $smokeReport)) { throw 'Frozen self-test failed.' }
    $smokeResult = Get-Content -LiteralPath $smokeReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($smokeResult.status -ne 'passed') { throw 'Frozen runtime validation failed.' }
    if ($CreateInstaller) {
        & $InnoCompiler "/DAppVersion=$($release.version)" "/DBuildRoot=$(Join-Path $soloRoot 'dist\MonitorSolo')" (Join-Path $PSScriptRoot 'MonitorSolo.iss')
        if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    }
} finally { Pop-Location }
