"""Prepare release-only defaults and a multi-resolution Windows icon."""
from pathlib import Path
import shutil

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
SOLO = ROOT.parent
ROPE_NAMES = ("claude", "deepseek", "gemini", "glm", "gpt", "grok", "kimi", "qwen", "whale")


def main():
    assets = ROOT / "assets"
    (assets / "rope").mkdir(parents=True, exist_ok=True)
    with Image.open(SOLO / "monitor/icon_asset/MonitorSolo_logo.png") as source:
        # Preserve the existing design and alpha, rather than stretching it.
        logo = ImageOps.contain(source.convert("RGBA"), (256, 256), Image.Resampling.LANCZOS)
        square = Image.new("RGBA", (256, 256))
        square.alpha_composite(logo, ((256 - logo.width) // 2, (256 - logo.height) // 2))
        square.save(assets / "MonitorSolo.ico", sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    for name in [*(n + ".png" for n in ROPE_NAMES), "readme.txt"]:
        # No glob of data/: user's wallpapers, settings and history stay private.
        shutil.copyfile(SOLO / "data/rope" / name, assets / "rope" / name)
    print("Prepared Windows icon and nine default pendant images; original files unchanged.")


if __name__ == "__main__":
    main()
