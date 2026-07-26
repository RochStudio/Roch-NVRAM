"""Generate assets/roch_nvram.ico (build-time only; the .ico is committed).

Run with the project venv:  .venv\\Scripts\\python.exe tools\\make_icon.py
Requires Pillow, which is not a runtime dependency of the app.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (17, 24, 39, 255)  # slate-900, matches the dark UI
CHIP = (239, 68, 68, 255)  # red-500
PIN = (185, 28, 28, 255)  # red-700


def _font(px: int) -> ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded dark tile
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.18), fill=BG)

    # Chip pins along left and right edges
    pin_w, pin_h = int(SIZE * 0.075), int(SIZE * 0.075)
    for i in range(3):
        y = int(SIZE * 0.30) + i * int(SIZE * 0.17)
        d.rounded_rectangle(
            [int(SIZE * 0.16), y, int(SIZE * 0.16) + pin_w, y + pin_h],
            radius=int(pin_h * 0.3),
            fill=PIN,
        )
        d.rounded_rectangle(
            [SIZE - int(SIZE * 0.16) - pin_w, y, SIZE - int(SIZE * 0.16), y + pin_h],
            radius=int(pin_h * 0.3),
            fill=PIN,
        )

    # Chip body
    m = int(SIZE * 0.23)
    d.rounded_rectangle([m, m, SIZE - m, SIZE - m], radius=int(SIZE * 0.06), fill=CHIP)

    # "R" centred in the chip
    font = _font(int(SIZE * 0.42))
    box = d.textbbox((0, 0), "R", font=font)
    d.text(
        ((SIZE - (box[2] - box[0])) / 2 - box[0], (SIZE - (box[3] - box[1])) / 2 - box[1]),
        "R",
        font=font,
        fill=BG,
    )
    return img


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "assets" / "roch_nvram.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    master = build()
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    master.save(out, format="ICO", sizes=sizes)
    print(f"Wrote {out} ({out.stat().st_size:,} bytes) with sizes {[s[0] for s in sizes]}")


if __name__ == "__main__":
    main()
