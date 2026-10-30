# Regenerate firmware/ESP32-S3-Island/island_cjk_16.c
#
# The mirror UI needs Simplified-Chinese glyphs, but LVGL's built-in
# lv_font_simsun_16_cjk only contains common radicals (~1400 glyphs, many
# missing).  This script builds a 16 px, 4 bpp, LVGL-8 compressed font from
# Noto Sans SC (SIL Open Font License 1.1) that covers:
#   * ASCII 0x20-0x7E
#   * GB2312 punctuation / full-width forms / kana (0xA1A1..0xA9FE)
#   * GB2312 level-1 and level-2 Hanzi (0xB0A1..0xF7FE), ~6763 chars
#   * common punctuation and geometric shapes used by the UI
#
# Requirements: Node.js (npx), Python 3, internet access for the first run.
# Usage:  powershell -ExecutionPolicy Bypass -File firmware/tools/make_island_cjk_font.ps1
#
# Output is checked in on purpose: Arduino users should not need font tools.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$sketch = Join-Path $root 'firmware\ESP32-S3-Island'
$work = Join-Path $env:TEMP 'solo-island-font'
New-Item -ItemType Directory -Force $work | Out-Null

$fontUrl = 'https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf'
$font = Join-Path $work 'NotoSansSC-Regular.otf'
if (-not (Test-Path $font)) {
    Write-Host "Downloading Noto Sans SC..."
    Invoke-WebRequest -UseBasicParsing $fontUrl -OutFile $font -TimeoutSec 300
}

# Build the exact symbol set with Python so the result is reproducible.
$symbolsFile = Join-Path $work 'gb2312-symbols.txt'
$py = @'
import sys
chars = []
for hi in range(0xA1, 0xF8):
    for lo in range(0xA1, 0xFF):
        try:
            chars.append(bytes([hi, lo]).decode('gb2312'))
        except UnicodeDecodeError:
            pass
open(sys.argv[1], 'w', encoding='utf-8').write(''.join(chars))
print(len(chars))
'@
$pyFile = Join-Path $work 'make_symbols.py'
Set-Content -Path $pyFile -Value $py -Encoding UTF8
& python $pyFile $symbolsFile
$symbols = [System.IO.File]::ReadAllText($symbolsFile, [System.Text.Encoding]::UTF8)

$out = Join-Path $sketch 'island_cjk_16.c'
Write-Host "Generating $out ..."
& npx.cmd -y lv_font_conv@1.5.3 `
    --font $font `
    --size 16 --bpp 4 --format lvgl --lv-include lvgl.h --lv-font-name island_cjk_16 `
    --symbols $symbols `
    -r 0x20-0x7E -r 0x2000-0x206F -r 0x25A0-0x25FF `
    -o $out
if ($LASTEXITCODE -ne 0) { throw "lv_font_conv failed" }
Write-Host "Done: $((Get-Item $out).Length) bytes of C source."
Write-Host "Reminder: lv_conf.h must keep LV_FONT_FMT_TXT_LARGE=1 and LV_USE_FONT_COMPRESSED=1."
