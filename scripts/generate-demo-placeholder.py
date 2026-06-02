#!/usr/bin/env python3
"""Build docs/assets/lro-demo.gif until a real screen recording replaces it."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 960, 540
FRAMES = 48
OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "lro-demo.gif"

BG = (16, 18, 22)
CARD = (24, 27, 33)
BORDER = (35, 39, 48)
ACCENT = (249, 115, 22)
MUTED = (140, 150, 165)
FG = (242, 242, 242)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    if bold:
        candidates = [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            *candidates,
        ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def draw_frame(t: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title = _font(18, bold=True)
    body = _font(13)
    small = _font(11)

    d.rectangle([0, 0, W, 52], fill=CARD)
    d.ellipse([18, 16, 34, 32], fill=ACCENT)
    d.text((42, 14), "Local Recruiting Ops", fill=FG, font=title)
    for i, tab in enumerate(["Brief", "Matches", "History", "Settings"]):
        x = 140 + i * 96
        colour = ACCENT if tab == "Brief" else MUTED
        d.text((x, 18), tab, fill=colour, font=body)

    metrics = [
        ("Registry", "80"),
        ("Match rate", "6.2%"),
        ("Ghost rate", "11%"),
        ("Last cycle", "3m 12s"),
    ]
    for i, (label, value) in enumerate(metrics):
        x = 20 + i * 232
        d.rounded_rectangle([x, 72, x + 216, 148], radius=10, fill=CARD, outline=BORDER)
        d.text((x + 14, 86), label, fill=MUTED, font=small)
        d.text((x + 14, 112), value, fill=FG, font=title)

    progress = min(0.92, (t % FRAMES) / (FRAMES * 0.85))
    d.rounded_rectangle([20, 168, W - 20, 214], radius=10, fill=CARD, outline=BORDER)
    d.text((34, 178), "Run Pipeline", fill=FG, font=body)
    bar_x = 34
    bar_max = W - 54
    d.rounded_rectangle([bar_x, 196, bar_max, 206], radius=4, fill=BORDER)
    d.rounded_rectangle([bar_x, 196, bar_x + int((bar_max - bar_x) * progress), 206], radius=4, fill=ACCENT)

    d.rounded_rectangle([20, 232, W - 20, H - 24], radius=10, fill=CARD, outline=BORDER)
    pulse = 0.55 + 0.45 * abs((t % 24) / 12 - 1)
    hint = (int(MUTED[0] * pulse), int(MUTED[1] * pulse), int(MUTED[2] * pulse))
    d.text((W // 2 - 170, H // 2 - 36), "Placeholder demo recording", fill=FG, font=title)
    d.text((W // 2 - 210, H // 2 - 4), "Replace with a screen capture of:", fill=MUTED, font=body)
    d.text((W // 2 - 198, H // 2 + 24), "Settings -> Run Pipeline -> Matches tab", fill=hint, font=body)
    d.text((W // 2 - 176, H // 2 + 52), "docs/assets/lro-demo.gif", fill=MUTED, font=small)
    return img


def main() -> None:
    frames = [draw_frame(i) for i in range(FRAMES)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=90,
        loop=0,
        optimize=True,
    )
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
