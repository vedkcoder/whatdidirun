"""Generate the app icon (src/recall/assets/icon.png + icon.ico). Run: uv run --extra tray python packaging/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "src" / "recall" / "assets"
SIZE = 256
BG = (24, 26, 33, 255)       # dark terminal
ACCENT = (94, 234, 212, 255)  # teal prompt
TEXT = (255, 255, 255, 255)


def font(size: int):
    for name in ("DejaVuSansMono-Bold.ttf", "consolab.ttf", "Menlo.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=48, fill=BG)
    d.text((40, 58), ">_", font=font(92), fill=ACCENT)
    d.text((SIZE - 108, 118), "??", font=font(96), fill=TEXT)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT / 'icon.png'} and icon.ico")
