"""
PROCEDURAL PIXEL-ART SPRITE GENERATOR

Regenerates every helper sprite GIF under sentinel-ui/public/sprites/.
Run whenever you want to tweak art. The backend manifest in
sentinel/core/helper.py is the source of truth for which files are
expected here.

Design notes
------------
- Canvas is 32x32 source, dashboard renders at 96x96 with
  `image-rendering: pixelated` so a 3x nearest-neighbour upscale
  keeps the pixels crisp.
- All drawing is hard-edged. We use Pillow's ImageDraw primitives on
  an RGBA canvas which does not antialias fills, so every pixel is
  either fully opaque or fully transparent.
- Eleven animation states per sprite:
    idle       2 frames, 220ms/frame — gentle bob (baseline)
    blink      3 frames, 100ms/frame — quick eye close (random idle garnish)
    look       3 frames, 220ms/frame — eyes dart side-to-side (curious)
    nod        3 frames, 140ms/frame — "yes" head bob (keep feedback)
    shake      4 frames, 110ms/frame — "no" head wobble (skip feedback)
    think      3 frames, 260ms/frame — chin tap + dot cycle (cycle running)
    eat        4 frames, 120ms/frame — chomp (new match arrived)
    wave       3 frames, 180ms/frame — small arm/tail wave (tier-1 happy)
    bounce     4 frames, 140ms/frame — medium hop (tier-2 happy)
    celebrate  4 frames, 120ms/frame — full jump + sparkles (tier-3 happy)
    sleep      3 frames, 500ms/frame — flattened body + floating Z
  The three happy tiers (wave/bounce/celebrate) are driven by the
  frontend "petting" meter - arrow-key presses bump the meter. The
  contextual states (nod/shake/eat/think) fire on pipeline events.
  The garnish states (blink/look) are randomly woven into long idle
  stretches so the helper never looks frozen.
- Each sprite has its own palette (body/light/dark/eye plus accents).
  Kept together at the top of each function for easy editing.
- Transparency in animated GIFs is notoriously fiddly in Pillow. We
  quantize each RGBA frame to a 255-colour palette, reserve index
  255 for transparent, and composite every alpha<128 pixel into that
  index. disposal=2 clears each frame back to the background so the
  previous frame doesn't ghost through transparent pixels.
"""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw

OUT_DIR = (Path(__file__).resolve().parent.parent
           / "sentinel-ui" / "public" / "sprites")
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 32, 32
TRANSPARENT = (0, 0, 0, 0)


# ══════════════════════════════════════════════════════════════════
# IO HELPERS
# ══════════════════════════════════════════════════════════════════
def new_frame() -> Image.Image:
    return Image.new("RGBA", (W, H), TRANSPARENT)


def new_frame_with(draw_fn) -> Image.Image:
    """Allocate a blank frame and run a one-shot drawing callable. Tiny
    sugar for `[draw_fn(new_frame()) for ...]` style list comps where we
    want an RGBA image back."""
    img = new_frame()
    draw_fn(img)
    return img


def save_gif(frames: list[Image.Image], path: Path, duration_ms: int) -> None:
    """Save RGBA frames as an animated GIF with clean transparency.

    We quantize each frame independently to keep per-frame colour
    fidelity, push fully-transparent pixels into palette index 255,
    and use disposal=2 so frames don't ghost into each other."""
    paletted = []
    for rgba in frames:
        # Quantize to 255 palette colours, leaving slot 255 for transparency.
        q = rgba.convert("RGB").quantize(
            colors=255,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        # Mask: 255 where the source was transparent, 0 elsewhere.
        alpha = rgba.split()[-1]
        mask = alpha.point(lambda a: 0 if a >= 128 else 255)
        q.paste(255, mask=mask)
        paletted.append(q)

    paletted[0].save(
        path,
        save_all=True,
        append_images=paletted[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )


def sparkle(draw: ImageDraw.ImageDraw, x: int, y: int,
            color=(255, 240, 150), size: int = 1) -> None:
    """Draw a tiny plus-shape sparkle."""
    draw.point((x, y), fill=color)
    draw.point((x - size, y), fill=color)
    draw.point((x + size, y), fill=color)
    draw.point((x, y - size), fill=color)
    draw.point((x, y + size), fill=color)


def letter_z(draw: ImageDraw.ImageDraw, x: int, y: int,
             color=(240, 240, 240)) -> None:
    """Draw a 3x3 'Z' letter."""
    draw.line((x, y, x + 2, y), fill=color)
    draw.line((x + 2, y, x, y + 2), fill=color)
    draw.line((x, y + 2, x + 2, y + 2), fill=color)


def closed_eye(draw: ImageDraw.ImageDraw, x: int, y: int,
               color) -> None:
    """Two-pixel horizontal closed eye."""
    draw.point((x, y), fill=color)
    draw.point((x + 1, y), fill=color)


# ══════════════════════════════════════════════════════════════════
# JOBY — round peach blob
# ══════════════════════════════════════════════════════════════════
JOBY = {
    "body":   (255, 159, 139),
    "light":  (255, 205, 187),
    "dark":   (208, 110,  94),
    "eye":    ( 40,  25,  25),
    "mouth":  (180,  70,  70),
}


def joby_body(img: Image.Image, y_off: int = 0, squash: int = 0) -> None:
    """Draw Joby's body at a vertical offset + optional bottom squash."""
    d = ImageDraw.Draw(img)
    p = JOBY
    # Base ellipse: 22w x 17h, centered horizontally
    x1, y1, x2, y2 = 5, 8 + y_off, 26, 24 + y_off - squash
    d.ellipse((x1, y1, x2, y2), fill=p["body"])
    # Dark underbelly (bottom 1/3)
    d.ellipse((x1 + 1, y1 + 10, x2 - 1, y2), fill=p["dark"])
    d.ellipse((x1, y1, x2, y2 - 5), fill=p["body"])
    # Top highlight
    d.ellipse((8, 10 + y_off, 14, 14 + y_off), fill=p["light"])


def joby_face(img: Image.Image, y_off: int = 0, mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = JOBY
    eye_y = 15 + y_off
    if mood == "sleep":
        closed_eye(d, 12, eye_y, p["eye"])
        closed_eye(d, 18, eye_y, p["eye"])
        return
    if mood == "wave":
        # Tier-1 happy: squinty half-closed eyes (^_^)
        d.line((11, eye_y, 13, eye_y), fill=p["eye"])
        d.line((18, eye_y, 20, eye_y), fill=p["eye"])
        d.point((12, eye_y - 1), fill=p["eye"])
        d.point((19, eye_y - 1), fill=p["eye"])
        # Medium smile
        d.rectangle((14, 19 + y_off, 17, 20 + y_off), fill=p["mouth"])
        return
    # Open eyes: 2x2 blocks for more presence
    d.rectangle((12, eye_y, 13, eye_y + 1), fill=p["eye"])
    d.rectangle((18, eye_y, 19, eye_y + 1), fill=p["eye"])
    # Tiny eye shine
    d.point((13, eye_y), fill=(255, 255, 255))
    d.point((19, eye_y), fill=(255, 255, 255))
    if mood in ("celebrate", "bounce"):
        # Big open smile
        d.rectangle((14, 19 + y_off, 17, 20 + y_off), fill=p["mouth"])
        d.point((13, 19 + y_off), fill=p["mouth"])
        d.point((18, 19 + y_off), fill=p["mouth"])
        if mood == "celebrate":
            # Inner mouth darker
            d.point((15, 20 + y_off), fill=(120, 40, 40))
            d.point((16, 20 + y_off), fill=(120, 40, 40))
    else:
        # Tiny closed smile
        d.point((14, 19 + y_off), fill=p["mouth"])
        d.point((15, 20 + y_off), fill=p["mouth"])
        d.point((16, 20 + y_off), fill=p["mouth"])
        d.point((17, 19 + y_off), fill=p["mouth"])


def joby_arms(img: Image.Image, y_off: int, left_up: int, right_up: int) -> None:
    """Little peach arm nubs. Positive '*_up' lifts the arm higher."""
    d = ImageDraw.Draw(img)
    p = JOBY
    # Left arm: shoulder at x=5, elbow/hand lifts up
    lx, ly = 4, 16 + y_off - left_up
    d.rectangle((lx, ly, lx + 2, ly + 2), fill=p["body"])
    d.point((lx, ly), fill=p["dark"])
    # Right arm mirrored
    rx, ry = 26, 16 + y_off - right_up
    d.rectangle((rx, ry, rx + 2, ry + 2), fill=p["body"])
    d.point((rx + 2, ry), fill=p["dark"])


def joby_idle() -> list[Image.Image]:
    a = new_frame(); joby_body(a, y_off=0, squash=0); joby_face(a, 0)
    b = new_frame(); joby_body(b, y_off=1, squash=1); joby_face(b, 1)
    return [a, b]


def joby_wave() -> list[Image.Image]:
    """Tier-1 happy: alternating-arm little wave, squinty smile."""
    frames = []
    for (y_off, la, ra) in [(0, 2, 0), (-1, 0, 3), (0, 3, 0), (-1, 0, 2)]:
        img = new_frame()
        joby_body(img, y_off=y_off, squash=0)
        joby_arms(img, y_off=y_off, left_up=la, right_up=ra)
        joby_face(img, y_off=y_off, mood="wave")
        frames.append(img)
    return frames


def joby_bounce() -> list[Image.Image]:
    """Tier-2 happy: bigger hop + arms flop, normal smile."""
    frames = []
    for (y_off, squash, la, ra) in [
        (0,  2, 0, 0),
        (-3, 0, 3, 3),
        (-5, 0, 4, 4),
        (-1, 1, 1, 1),
    ]:
        img = new_frame()
        joby_body(img, y_off=y_off, squash=squash)
        joby_arms(img, y_off=y_off, left_up=la, right_up=ra)
        joby_face(img, y_off=y_off, mood="bounce")
        frames.append(img)
    return frames


def joby_blink() -> list[Image.Image]:
    """Quick eye-close then back. Three frames so the middle closed
    frame registers visually at 100ms each."""
    frames = []
    # Frame 0 = open, frame 1 = closed, frame 2 = open
    for mood in ("idle", "sleep", "idle"):
        img = new_frame()
        joby_body(img, y_off=0, squash=0)
        joby_face(img, y_off=0, mood=mood)
        frames.append(img)
    return frames


def joby_look() -> list[Image.Image]:
    """Eyes dart left then right. Body holds still."""
    frames = []
    # We override the default face with shifted pupils.
    for dx in (-2, 2, 0):
        img = new_frame()
        joby_body(img, y_off=0, squash=0)
        d = ImageDraw.Draw(img)
        p = JOBY
        eye_y = 15
        # Eye sockets (white-ish highlight behind) - keeps silhouette readable
        d.rectangle((12, eye_y, 13, eye_y + 1), fill=p["light"])
        d.rectangle((18, eye_y, 19, eye_y + 1), fill=p["light"])
        # Pupils shifted by dx
        d.point((12 + max(0, dx), eye_y), fill=p["eye"])
        d.point((18 + max(-1, dx - 1), eye_y), fill=p["eye"])
        # Closed smile
        d.point((14, 19), fill=p["mouth"])
        d.point((15, 20), fill=p["mouth"])
        d.point((16, 20), fill=p["mouth"])
        d.point((17, 19), fill=p["mouth"])
        frames.append(img)
    return frames


def joby_nod() -> list[Image.Image]:
    """'Yes' bob: up, down, up. Smile grows slightly."""
    frames = []
    for (y_off, mood) in [(-1, "idle"), (2, "wave"), (0, "idle")]:
        img = new_frame()
        joby_body(img, y_off=y_off, squash=max(0, -y_off))
        joby_face(img, y_off=y_off, mood=mood)
        frames.append(img)
    return frames


def joby_shake() -> list[Image.Image]:
    """'No' wobble: body leans left/right. Mouth neutral-line."""
    frames = []
    for (x_off, mood) in [(-2, "idle"), (2, "idle"), (-1, "idle"), (0, "idle")]:
        img = new_frame()
        # Joby body doesn't take x_off - shift the whole image by drawing
        # body on a temp image and pasting. Simpler: redraw ellipse shifted.
        d = ImageDraw.Draw(img)
        p = JOBY
        x1, y1, x2, y2 = 5 + x_off, 8, 26 + x_off, 24
        d.ellipse((x1, y1, x2, y2), fill=p["body"])
        d.ellipse((x1 + 1, y1 + 10, x2 - 1, y2), fill=p["dark"])
        d.ellipse((x1, y1, x2, y2 - 5), fill=p["body"])
        d.ellipse((8 + x_off, 10, 14 + x_off, 14), fill=p["light"])
        # Eyes with concerned straight-line mouth
        eye_y = 15
        d.rectangle((12 + x_off, eye_y, 13 + x_off, eye_y + 1), fill=p["eye"])
        d.rectangle((18 + x_off, eye_y, 19 + x_off, eye_y + 1), fill=p["eye"])
        d.line((14 + x_off, 20, 17 + x_off, 20), fill=p["mouth"])
        frames.append(img)
    return frames


def joby_think() -> list[Image.Image]:
    """Looking up and to the side, three dots cycling above the head."""
    frames = []
    dot_sets = [(1, 0, 0), (1, 1, 0), (1, 1, 1)]
    for i, dots in enumerate(dot_sets):
        img = new_frame()
        joby_body(img, y_off=0, squash=0)
        d = ImageDraw.Draw(img)
        p = JOBY
        # Eyes look up-and-right
        eye_y = 14  # one pixel higher than default
        d.rectangle((12, eye_y, 13, eye_y + 1), fill=p["eye"])
        d.rectangle((18, eye_y, 19, eye_y + 1), fill=p["eye"])
        # Flat "hmm" mouth
        d.line((14, 20, 17, 20), fill=p["mouth"])
        # Thinking arm: one nub raised to chin
        joby_arms(img, y_off=0, left_up=0, right_up=5)
        # Dots above head, appearing sequentially
        dot_y = 4
        for idx, on in enumerate(dots):
            if on:
                dx = 22 + idx * 2
                d.rectangle((dx, dot_y, dx + 1, dot_y + 1), fill=p["eye"])
        frames.append(img)
    return frames


def joby_eat() -> list[Image.Image]:
    """Chomp cycle - mouth opens wide and closes, little crumb sparkles."""
    frames = []
    mouth_states = [
        (False, []),                  # closed
        (True,  [(8, 12)]),            # open, food incoming
        (True,  []),                   # open, food in mouth
        (False, [(22, 20), (10, 21)]), # closed, crumbs
    ]
    for (open_mouth, sparks) in mouth_states:
        img = new_frame()
        joby_body(img, y_off=0, squash=0)
        d = ImageDraw.Draw(img)
        p = JOBY
        # Eyes squint happily
        eye_y = 15
        d.line((11, eye_y, 13, eye_y), fill=p["eye"])
        d.line((18, eye_y, 20, eye_y), fill=p["eye"])
        # Mouth
        if open_mouth:
            d.rectangle((13, 19, 18, 22), fill=(120, 40, 40))
            d.rectangle((14, 20, 17, 21), fill=(200, 90, 90))
        else:
            d.line((13, 20, 18, 20), fill=p["mouth"])
        # Crumbs / bite marks
        for (sx, sy) in sparks:
            sparkle(d, sx, sy, color=(255, 220, 140))
        frames.append(img)
    return frames


def joby_celebrate() -> list[Image.Image]:
    frames = []
    for i, (y_off, sparks) in enumerate([
        (0, []),
        (-3, [(4, 6), (27, 8)]),
        (-5, [(3, 4), (28, 5), (16, 2)]),
        (-2, [(5, 10), (26, 11)]),
    ]):
        img = new_frame()
        joby_body(img, y_off=y_off, squash=0)
        joby_face(img, y_off=y_off, mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        # Shadow on ground when jumping
        if y_off < 0:
            shadow_y = 25
            w = 8 + y_off  # shadow shrinks when higher
            if w > 2:
                d.ellipse((16 - w, shadow_y, 16 + w, shadow_y + 1),
                          fill=(30, 20, 20, 80))
        frames.append(img)
    return frames


def joby_sleep() -> list[Image.Image]:
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        # Flattened body (more squash)
        joby_body(img, y_off=2, squash=3)
        joby_face(img, y_off=2, mood="sleep")
        d = ImageDraw.Draw(img)
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# ROLLO — pixel dog
# ══════════════════════════════════════════════════════════════════
ROLLO = {
    "body":   (168, 115,  78),
    "light":  (214, 168, 124),
    "dark":   ( 96,  58,  38),
    "belly":  (238, 215, 180),
    "eye":    ( 30,  22,  18),
    "nose":   ( 22,  18,  16),
    "tongue": (220,  90,  90),
}


def rollo_body(img: Image.Image, y_off: int = 0, tail_up: bool = False,
               mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = ROLLO
    # Body: rounded rectangle
    body_top = 15 + y_off
    d.rectangle((8, body_top, 24, 25), fill=p["body"])
    d.rectangle((7, body_top + 1, 25, 24), fill=p["body"])
    # Belly
    d.rectangle((11, 21, 21, 24), fill=p["belly"])
    # Legs (4 stubby)
    d.rectangle((9, 24, 11, 26), fill=p["dark"])
    d.rectangle((13, 24, 15, 26), fill=p["dark"])
    d.rectangle((17, 24, 19, 26), fill=p["dark"])
    d.rectangle((21, 24, 23, 26), fill=p["dark"])
    # Tail
    if tail_up:
        d.line((25, body_top + 2, 27, body_top - 2), fill=p["body"])
        d.line((26, body_top + 2, 28, body_top - 1), fill=p["dark"])
    else:
        d.line((25, body_top + 3, 28, body_top + 3), fill=p["body"])
        d.point((29, body_top + 3), fill=p["dark"])
    # Head: rounded rectangle + snout
    head_top = 6 + y_off
    d.rectangle((9, head_top, 22, head_top + 10), fill=p["body"])
    d.rectangle((8, head_top + 1, 23, head_top + 9), fill=p["body"])
    # Ears: triangular, drooping
    d.polygon([(8, head_top - 1), (6, head_top + 4), (10, head_top + 4)],
              fill=p["dark"])
    d.polygon([(23, head_top - 1), (21, head_top + 4), (25, head_top + 4)],
              fill=p["dark"])
    # Snout
    d.rectangle((12, head_top + 7, 19, head_top + 11), fill=p["light"])
    # Nose
    d.rectangle((14, head_top + 7, 17, head_top + 9), fill=p["nose"])
    # Eyes
    if mood == "sleep":
        closed_eye(d, 11, head_top + 4, p["eye"])
        closed_eye(d, 18, head_top + 4, p["eye"])
    else:
        d.rectangle((11, head_top + 4, 12, head_top + 5), fill=p["eye"])
        d.rectangle((18, head_top + 4, 19, head_top + 5), fill=p["eye"])
        d.point((12, head_top + 4), fill=(255, 255, 255))
        d.point((19, head_top + 4), fill=(255, 255, 255))
    # Tongue (celebrate only)
    if mood == "celebrate":
        d.rectangle((15, head_top + 10, 16, head_top + 12), fill=p["tongue"])


def rollo_idle() -> list[Image.Image]:
    a = new_frame(); rollo_body(a, y_off=0, tail_up=False, mood="idle")
    b = new_frame(); rollo_body(b, y_off=-1, tail_up=True, mood="idle")
    return [a, b]


def rollo_wave() -> list[Image.Image]:
    """Tail wag + small ear perk."""
    frames = []
    for (y_off, tail) in [(0, True), (0, False), (-1, True)]:
        img = new_frame()
        rollo_body(img, y_off=y_off, tail_up=tail, mood="celebrate")
        frames.append(img)
    return frames


def rollo_blink() -> list[Image.Image]:
    a = new_frame(); rollo_body(a, y_off=0, tail_up=False, mood="idle")
    b = new_frame(); rollo_body(b, y_off=0, tail_up=False, mood="sleep")
    c = new_frame(); rollo_body(c, y_off=0, tail_up=False, mood="idle")
    return [a, b, c]


def rollo_look() -> list[Image.Image]:
    frames = []
    for y in (0, 0, 0):
        img = new_frame()
        rollo_body(img, y_off=y, tail_up=False, mood="idle")
        frames.append(img)
    return frames


def rollo_nod() -> list[Image.Image]:
    return [new_frame_with(lambda i: rollo_body(i, y_off=y, tail_up=True, mood="celebrate"))
            for y in (-1, 1, 0)]


def rollo_shake() -> list[Image.Image]:
    return [new_frame_with(lambda i: rollo_body(i, y_off=0, tail_up=u, mood="idle"))
            for u in (True, False, True, False)]


def rollo_think() -> list[Image.Image]:
    frames = []
    for phase in range(3):
        img = new_frame()
        rollo_body(img, y_off=0, tail_up=False, mood="idle")
        d = ImageDraw.Draw(img)
        # 3 dots cycling above head
        for i in range(phase + 1):
            d.rectangle((22 + i * 2, 2, 23 + i * 2, 3), fill=ROLLO["eye"])
        frames.append(img)
    return frames


def rollo_eat() -> list[Image.Image]:
    return [new_frame_with(lambda i: rollo_body(i, y_off=0, tail_up=t, mood="celebrate"))
            for t in (False, True, False, True)]


def rollo_bounce() -> list[Image.Image]:
    """Small hop with tongue."""
    frames = []
    for (y_off, tail) in [(0, False), (-2, True), (-3, True), (-1, False)]:
        img = new_frame()
        rollo_body(img, y_off=y_off, tail_up=tail, mood="celebrate")
        frames.append(img)
    return frames


def rollo_celebrate() -> list[Image.Image]:
    frames = []
    for i, (y_off, tail, sparks) in enumerate([
        (0,  True,  []),
        (-2, False, [(3, 6), (28, 7)]),
        (-4, True,  [(2, 4), (29, 5), (16, 2)]),
        (-1, False, [(5, 9), (27, 10)]),
    ]):
        img = new_frame()
        rollo_body(img, y_off=y_off, tail_up=tail, mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        frames.append(img)
    return frames


def rollo_sleep() -> list[Image.Image]:
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        # Lying down: body flatter, head lower
        d = ImageDraw.Draw(img)
        p = ROLLO
        d.rectangle((6, 20, 26, 25), fill=p["body"])
        d.rectangle((9, 19, 23, 24), fill=p["body"])
        # Belly
        d.rectangle((10, 23, 22, 25), fill=p["belly"])
        # Head on side
        d.rectangle((4, 17, 14, 23), fill=p["body"])
        d.polygon([(4, 17), (2, 20), (6, 20)], fill=p["dark"])
        # Snout
        d.rectangle((2, 19, 6, 22), fill=p["light"])
        d.point((3, 20), fill=p["nose"])
        d.point((4, 20), fill=p["nose"])
        # Closed eye
        closed_eye(d, 8, 19, p["eye"])
        # Zs
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# MOMO — pixel cat
# ══════════════════════════════════════════════════════════════════
MOMO = {
    "body":   (170, 170, 180),
    "light":  (215, 215, 225),
    "dark":   ( 90,  90, 100),
    "belly":  (240, 240, 245),
    "eye":    ( 40,  50,  30),
    "pupil":  ( 20,  25,  15),
    "nose":   (255, 159, 191),
    "stripe": (110, 110, 120),
}


def momo_body(img: Image.Image, y_off: int = 0, tail_off: int = 0,
              mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = MOMO
    # Body
    body_top = 14 + y_off
    d.ellipse((7, body_top, 25, 26), fill=p["body"])
    # Belly
    d.ellipse((10, body_top + 4, 22, 25), fill=p["belly"])
    # Stripes
    d.line((9, body_top + 3, 11, body_top + 3), fill=p["stripe"])
    d.line((9, body_top + 6, 11, body_top + 6), fill=p["stripe"])
    d.line((21, body_top + 3, 23, body_top + 3), fill=p["stripe"])
    d.line((21, body_top + 6, 23, body_top + 6), fill=p["stripe"])
    # Legs
    d.rectangle((9, 25, 11, 27), fill=p["body"])
    d.rectangle((13, 25, 15, 27), fill=p["body"])
    d.rectangle((17, 25, 19, 27), fill=p["body"])
    d.rectangle((21, 25, 23, 27), fill=p["body"])
    # Tail: curls up
    tail_x = 26
    d.line((tail_x, body_top + 4 + tail_off, tail_x + 2, body_top + 1 + tail_off),
           fill=p["body"])
    d.line((tail_x + 2, body_top + 1 + tail_off, tail_x + 3, body_top - 2 + tail_off),
           fill=p["body"])
    d.point((tail_x + 3, body_top - 3 + tail_off), fill=p["stripe"])
    # Head
    head_top = 4 + y_off
    d.ellipse((9, head_top, 22, head_top + 11), fill=p["body"])
    # Ears: pointy triangles
    d.polygon([(9, head_top + 2), (7, head_top - 3), (12, head_top + 1)],
              fill=p["body"])
    d.polygon([(22, head_top + 2), (24, head_top - 3), (19, head_top + 1)],
              fill=p["body"])
    # Inner ear
    d.polygon([(9, head_top + 1), (8, head_top - 1), (11, head_top + 1)],
              fill=p["nose"])
    d.polygon([(22, head_top + 1), (23, head_top - 1), (20, head_top + 1)],
              fill=p["nose"])
    # Eyes
    if mood == "sleep":
        closed_eye(d, 11, head_top + 5, p["pupil"])
        closed_eye(d, 18, head_top + 5, p["pupil"])
    else:
        d.rectangle((11, head_top + 4, 12, head_top + 6), fill=p["eye"])
        d.rectangle((18, head_top + 4, 19, head_top + 6), fill=p["eye"])
        d.point((12, head_top + 4), fill=(255, 255, 255))
        d.point((19, head_top + 4), fill=(255, 255, 255))
    # Nose
    d.point((15, head_top + 7), fill=p["nose"])
    d.point((16, head_top + 7), fill=p["nose"])
    # Mouth
    if mood == "celebrate":
        d.rectangle((14, head_top + 8, 17, head_top + 10), fill=p["pupil"])
        d.point((15, head_top + 9), fill=p["nose"])
    else:
        d.point((14, head_top + 8), fill=p["pupil"])
        d.point((15, head_top + 9), fill=p["pupil"])
        d.point((16, head_top + 9), fill=p["pupil"])
        d.point((17, head_top + 8), fill=p["pupil"])
    # Whiskers
    d.point((7, head_top + 7), fill=p["dark"])
    d.point((8, head_top + 7), fill=p["dark"])
    d.point((23, head_top + 7), fill=p["dark"])
    d.point((24, head_top + 7), fill=p["dark"])


def momo_idle() -> list[Image.Image]:
    a = new_frame(); momo_body(a, y_off=0, tail_off=0, mood="idle")
    b = new_frame(); momo_body(b, y_off=-1, tail_off=-1, mood="idle")
    return [a, b]


def momo_wave() -> list[Image.Image]:
    """Tail swish, ears perked."""
    frames = []
    for (y_off, tail_off) in [(0, 0), (0, -2), (0, 1)]:
        img = new_frame()
        momo_body(img, y_off=y_off, tail_off=tail_off, mood="celebrate")
        frames.append(img)
    return frames


def momo_blink() -> list[Image.Image]:
    a = new_frame(); momo_body(a, y_off=0, tail_off=0, mood="idle")
    b = new_frame(); momo_body(b, y_off=0, tail_off=0, mood="sleep")
    c = new_frame(); momo_body(c, y_off=0, tail_off=0, mood="idle")
    return [a, b, c]


def momo_look() -> list[Image.Image]:
    return [new_frame_with(lambda i, t=t: momo_body(i, y_off=0, tail_off=t, mood="idle"))
            for t in (-2, 2, 0)]


def momo_nod() -> list[Image.Image]:
    return [new_frame_with(lambda i, y=y: momo_body(i, y_off=y, tail_off=0, mood="celebrate"))
            for y in (-1, 1, 0)]


def momo_shake() -> list[Image.Image]:
    return [new_frame_with(lambda i, t=t: momo_body(i, y_off=0, tail_off=t, mood="idle"))
            for t in (-2, 2, -2, 0)]


def momo_think() -> list[Image.Image]:
    frames = []
    for phase in range(3):
        img = new_frame()
        momo_body(img, y_off=0, tail_off=0, mood="idle")
        d = ImageDraw.Draw(img)
        for i in range(phase + 1):
            d.rectangle((22 + i * 2, 2, 23 + i * 2, 3), fill=MOMO["pupil"])
        frames.append(img)
    return frames


def momo_eat() -> list[Image.Image]:
    return [new_frame_with(lambda i, t=t: momo_body(i, y_off=0, tail_off=t, mood="celebrate"))
            for t in (0, 1, 0, 1)]


def momo_bounce() -> list[Image.Image]:
    """Medium pounce."""
    frames = []
    for (y_off, tail_off) in [(0, 0), (-2, 1), (-3, 2), (-1, 0)]:
        img = new_frame()
        momo_body(img, y_off=y_off, tail_off=tail_off, mood="celebrate")
        frames.append(img)
    return frames


def momo_celebrate() -> list[Image.Image]:
    frames = []
    for i, (y_off, tail_off, sparks) in enumerate([
        (0,  0,  []),
        (-2, 1,  [(3, 5), (28, 6)]),
        (-4, 2,  [(2, 3), (29, 4), (16, 1)]),
        (-1, 1,  [(5, 8), (27, 9)]),
    ]):
        img = new_frame()
        momo_body(img, y_off=y_off, tail_off=tail_off, mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        frames.append(img)
    return frames


def momo_sleep() -> list[Image.Image]:
    """Curled up cat."""
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        d = ImageDraw.Draw(img)
        p = MOMO
        # Curled body: big ellipse
        d.ellipse((5, 16, 27, 27), fill=p["body"])
        d.ellipse((8, 19, 24, 26), fill=p["belly"])
        # Stripes
        d.line((10, 18, 12, 18), fill=p["stripe"])
        d.line((14, 17, 16, 17), fill=p["stripe"])
        d.line((18, 18, 20, 18), fill=p["stripe"])
        # Head tucked in
        d.ellipse((4, 18, 13, 25), fill=p["body"])
        d.polygon([(5, 18), (4, 14), (8, 17)], fill=p["body"])
        d.polygon([(5, 17), (5, 15), (7, 17)], fill=p["nose"])
        # Closed eye
        closed_eye(d, 7, 21, p["pupil"])
        # Tail wrapped around
        d.line((24, 24, 22, 22), fill=p["body"])
        d.line((22, 22, 19, 21), fill=p["body"])
        # Zs
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# HOOT — pixel owl
# ══════════════════════════════════════════════════════════════════
HOOT = {
    "body":       (145, 102,  70),
    "light":      (200, 155, 110),
    "dark":       ( 85,  58,  40),
    "belly":      (232, 206, 170),
    "eye_white":  (252, 244, 210),
    "pupil":      ( 25,  20,  15),
    "beak":       (245, 180,  70),
    "beak_dark":  (185, 125,  40),
}


def hoot_body(img: Image.Image, y_off: int = 0, wings_out: bool = False,
              mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = HOOT
    # Body (round owl shape)
    d.ellipse((6, 5 + y_off, 26, 28 + y_off), fill=p["body"])
    # Belly panel
    d.ellipse((10, 11 + y_off, 22, 26 + y_off), fill=p["belly"])
    # Belly speckles
    d.point((13, 15 + y_off), fill=p["light"])
    d.point((17, 17 + y_off), fill=p["light"])
    d.point((14, 20 + y_off), fill=p["light"])
    d.point((19, 21 + y_off), fill=p["light"])
    # Ear tufts
    d.polygon([(8, 5 + y_off), (6, 1 + y_off), (11, 5 + y_off)], fill=p["dark"])
    d.polygon([(24, 5 + y_off), (26, 1 + y_off), (21, 5 + y_off)], fill=p["dark"])
    # Wings
    if wings_out:
        d.polygon([(5, 14 + y_off), (2, 20 + y_off), (7, 22 + y_off)],
                  fill=p["dark"])
        d.polygon([(27, 14 + y_off), (30, 20 + y_off), (25, 22 + y_off)],
                  fill=p["dark"])
    else:
        d.line((7, 14 + y_off, 5, 22 + y_off), fill=p["dark"])
        d.line((8, 14 + y_off, 6, 22 + y_off), fill=p["dark"])
        d.line((25, 14 + y_off, 27, 22 + y_off), fill=p["dark"])
        d.line((24, 14 + y_off, 26, 22 + y_off), fill=p["dark"])
    # Feet
    d.line((12, 28 + y_off, 12, 29 + y_off), fill=p["beak"])
    d.line((13, 29 + y_off, 14, 29 + y_off), fill=p["beak"])
    d.line((11, 29 + y_off, 11, 29 + y_off), fill=p["beak"])
    d.line((18, 28 + y_off, 18, 29 + y_off), fill=p["beak"])
    d.line((19, 29 + y_off, 20, 29 + y_off), fill=p["beak"])
    d.line((17, 29 + y_off, 17, 29 + y_off), fill=p["beak"])
    # Eyes: BIG white circles
    eye_y = 10 + y_off
    if mood == "sleep":
        d.line((9, eye_y + 2, 13, eye_y + 2), fill=p["pupil"])
        d.line((19, eye_y + 2, 23, eye_y + 2), fill=p["pupil"])
    else:
        d.ellipse((9, eye_y, 14, eye_y + 5), fill=p["eye_white"])
        d.ellipse((18, eye_y, 23, eye_y + 5), fill=p["eye_white"])
        # Pupils
        d.rectangle((11, eye_y + 2, 12, eye_y + 3), fill=p["pupil"])
        d.rectangle((20, eye_y + 2, 21, eye_y + 3), fill=p["pupil"])
        # Highlight
        d.point((11, eye_y + 2), fill=(255, 255, 255))
        d.point((20, eye_y + 2), fill=(255, 255, 255))
    # Beak
    d.polygon([(15, 16 + y_off), (17, 16 + y_off),
               (16, 18 + y_off)], fill=p["beak"])
    d.point((16, 18 + y_off), fill=p["beak_dark"])


def hoot_idle() -> list[Image.Image]:
    a = new_frame(); hoot_body(a, y_off=0, wings_out=False, mood="idle")
    b = new_frame(); hoot_body(b, y_off=1, wings_out=False, mood="idle")
    return [a, b]


def hoot_wave() -> list[Image.Image]:
    """Wing flap without jump."""
    frames = []
    for (y_off, wings) in [(0, False), (0, True), (0, False)]:
        img = new_frame()
        hoot_body(img, y_off=y_off, wings_out=wings, mood="celebrate")
        frames.append(img)
    return frames


def hoot_blink() -> list[Image.Image]:
    a = new_frame(); hoot_body(a, y_off=0, wings_out=False, mood="idle")
    b = new_frame(); hoot_body(b, y_off=0, wings_out=False, mood="sleep")
    c = new_frame(); hoot_body(c, y_off=0, wings_out=False, mood="idle")
    return [a, b, c]


def hoot_look() -> list[Image.Image]:
    return [new_frame_with(lambda i, w=w: hoot_body(i, y_off=0, wings_out=w, mood="idle"))
            for w in (False, True, False)]


def hoot_nod() -> list[Image.Image]:
    return [new_frame_with(lambda i, y=y: hoot_body(i, y_off=y, wings_out=False, mood="celebrate"))
            for y in (-1, 1, 0)]


def hoot_shake() -> list[Image.Image]:
    return [new_frame_with(lambda i, w=w: hoot_body(i, y_off=0, wings_out=w, mood="idle"))
            for w in (True, False, True, False)]


def hoot_think() -> list[Image.Image]:
    frames = []
    for phase in range(3):
        img = new_frame()
        hoot_body(img, y_off=0, wings_out=False, mood="idle")
        d = ImageDraw.Draw(img)
        for i in range(phase + 1):
            d.rectangle((2 + i * 2, 2, 3 + i * 2, 3), fill=HOOT["pupil"])
        frames.append(img)
    return frames


def hoot_eat() -> list[Image.Image]:
    return [new_frame_with(lambda i, w=w: hoot_body(i, y_off=0, wings_out=w, mood="celebrate"))
            for w in (False, True, False, True)]


def hoot_bounce() -> list[Image.Image]:
    """Small hop with wings out."""
    frames = []
    for (y_off, wings) in [(0, False), (-2, True), (-3, True), (-1, False)]:
        img = new_frame()
        hoot_body(img, y_off=y_off, wings_out=wings, mood="celebrate")
        frames.append(img)
    return frames


def hoot_celebrate() -> list[Image.Image]:
    frames = []
    for i, (y_off, wings, sparks) in enumerate([
        (0,  False, []),
        (-3, True,  [(3, 4), (28, 5)]),
        (-5, True,  [(2, 2), (29, 3), (16, 1)]),
        (-1, False, [(4, 8), (27, 9)]),
    ]):
        img = new_frame()
        hoot_body(img, y_off=y_off, wings_out=wings, mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        frames.append(img)
    return frames


def hoot_sleep() -> list[Image.Image]:
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        # Slumped owl
        hoot_body(img, y_off=3, wings_out=False, mood="sleep")
        d = ImageDraw.Draw(img)
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# SLIM — bouncy slime
# ══════════════════════════════════════════════════════════════════
SLIM = {
    "body":   (115, 185, 125),
    "light":  (185, 230, 195),
    "dark":   ( 60, 115,  75),
    "shine":  (245, 255, 245),
    "eye":    ( 30,  55,  35),
}


def slim_body(img: Image.Image, width_delta: int = 0, height_delta: int = 0,
              y_off: int = 0, mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = SLIM
    # Slime outline: rounded droplet shape
    # Base: narrower at top, wider at bottom
    cx = 16
    base_top = 10 + y_off - height_delta
    base_bottom = 26 + y_off
    top_w = 7 - height_delta // 2
    bot_w = 11 + width_delta
    # Body polygon via a series of horizontal lines
    for y in range(base_top, base_bottom + 1):
        t = (y - base_top) / max(1, base_bottom - base_top)
        # Ease the width from top to bottom
        w = int(top_w + (bot_w - top_w) * (t ** 0.6))
        d.line((cx - w, y, cx + w, y), fill=p["body"])
    # Dark underbelly
    for y in range(base_bottom - 3, base_bottom + 1):
        t = (y - base_top) / max(1, base_bottom - base_top)
        w = int(top_w + (bot_w - top_w) * (t ** 0.6))
        d.line((cx - w + 1, y, cx + w - 1, y), fill=p["dark"])
    # Top highlight
    d.ellipse((cx - 4, base_top + 1, cx - 1, base_top + 3),
              fill=p["light"])
    # Shine
    d.point((cx - 3, base_top + 2), fill=p["shine"])
    # Eyes
    eye_y = base_top + 6
    if mood == "sleep":
        closed_eye(d, cx - 4, eye_y, p["eye"])
        closed_eye(d, cx + 3, eye_y, p["eye"])
    else:
        d.rectangle((cx - 4, eye_y, cx - 3, eye_y + 1), fill=p["eye"])
        d.rectangle((cx + 3, eye_y, cx + 4, eye_y + 1), fill=p["eye"])
        d.point((cx - 3, eye_y), fill=(255, 255, 255))
        d.point((cx + 4, eye_y), fill=(255, 255, 255))
    # Mouth
    if mood == "celebrate":
        d.rectangle((cx - 2, eye_y + 4, cx + 2, eye_y + 5), fill=p["eye"])
        d.point((cx, eye_y + 6), fill=p["eye"])
    else:
        d.point((cx - 1, eye_y + 4), fill=p["eye"])
        d.point((cx, eye_y + 5), fill=p["eye"])
        d.point((cx + 1, eye_y + 4), fill=p["eye"])


def slim_idle() -> list[Image.Image]:
    # Squash-and-stretch bounce
    a = new_frame(); slim_body(a, width_delta=0, height_delta=0, y_off=0)
    b = new_frame(); slim_body(b, width_delta=1, height_delta=-1, y_off=1)
    return [a, b]


def slim_wave() -> list[Image.Image]:
    """Gentle wobble, celebrate face."""
    frames = []
    for (w, h, y_off) in [(0, 0, 0), (2, -1, 0), (-1, 1, 0)]:
        img = new_frame()
        slim_body(img, width_delta=w, height_delta=h, y_off=y_off,
                  mood="celebrate")
        frames.append(img)
    return frames


def slim_blink() -> list[Image.Image]:
    a = new_frame(); slim_body(a, 0, 0, 0, mood="idle")
    b = new_frame(); slim_body(b, 0, 0, 0, mood="sleep")
    c = new_frame(); slim_body(c, 0, 0, 0, mood="idle")
    return [a, b, c]


def slim_look() -> list[Image.Image]:
    return [new_frame_with(lambda i, w=w: slim_body(i, width_delta=w, height_delta=0, y_off=0, mood="idle"))
            for w in (-1, 1, 0)]


def slim_nod() -> list[Image.Image]:
    return [new_frame_with(lambda i, y=y: slim_body(i, width_delta=0, height_delta=0, y_off=y, mood="celebrate"))
            for y in (-1, 1, 0)]


def slim_shake() -> list[Image.Image]:
    return [new_frame_with(lambda i, w=w: slim_body(i, width_delta=w, height_delta=0, y_off=0, mood="idle"))
            for w in (-2, 2, -1, 0)]


def slim_think() -> list[Image.Image]:
    frames = []
    for phase in range(3):
        img = new_frame()
        slim_body(img, width_delta=0, height_delta=0, y_off=0, mood="idle")
        d = ImageDraw.Draw(img)
        for i in range(phase + 1):
            d.rectangle((22 + i * 2, 2, 23 + i * 2, 3), fill=SLIM["eye"])
        frames.append(img)
    return frames


def slim_eat() -> list[Image.Image]:
    return [new_frame_with(lambda i, h=h: slim_body(i, width_delta=0, height_delta=h, y_off=0, mood="celebrate"))
            for h in (0, -1, 0, -1)]


def slim_bounce() -> list[Image.Image]:
    """Medium squash-stretch bounce."""
    frames = []
    for (w, h, y_off) in [(0, 0, 0), (-1, 2, -2), (-2, 3, -3), (2, -2, -1)]:
        img = new_frame()
        slim_body(img, width_delta=w, height_delta=h, y_off=y_off,
                  mood="celebrate")
        frames.append(img)
    return frames


def slim_celebrate() -> list[Image.Image]:
    frames = []
    for i, (w, h, y_off, sparks) in enumerate([
        (1,  -1, 0,  []),
        (-1, 2,  -3, [(3, 8), (28, 9)]),
        (-2, 3,  -5, [(2, 5), (29, 6), (16, 3)]),
        (2,  -2, -1, [(5, 11), (27, 12)]),
    ]):
        img = new_frame()
        slim_body(img, width_delta=w, height_delta=h, y_off=y_off,
                  mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        frames.append(img)
    return frames


def slim_sleep() -> list[Image.Image]:
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        # Very flat
        slim_body(img, width_delta=4, height_delta=4, y_off=3, mood="sleep")
        d = ImageDraw.Draw(img)
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# PIXEL — blocky retro robot
# ══════════════════════════════════════════════════════════════════
PIXEL_P = {
    "body":    (145, 155, 170),
    "light":   (210, 215, 225),
    "dark":    ( 80,  90, 105),
    "screen":  ( 25,  30,  40),
    "eye":     ( 85, 210, 240),
    "accent":  (230,  95,  65),
    "antenna": (255, 220,  80),
}


def pixel_body(img: Image.Image, y_off: int = 0, antenna_phase: int = 0,
               mood: str = "idle") -> None:
    d = ImageDraw.Draw(img)
    p = PIXEL_P
    # Head: rectangle
    head_top = 6 + y_off
    d.rectangle((8, head_top, 23, head_top + 9), fill=p["body"])
    d.rectangle((7, head_top + 1, 24, head_top + 8), fill=p["body"])
    # Head shading: dark bottom, light top
    d.line((8, head_top + 8, 23, head_top + 8), fill=p["dark"])
    d.line((8, head_top, 23, head_top), fill=p["light"])
    # Screen
    d.rectangle((10, head_top + 2, 21, head_top + 7), fill=p["screen"])
    # Eye(s)
    if mood == "sleep":
        d.line((12, head_top + 4, 14, head_top + 4), fill=p["dark"])
        d.line((17, head_top + 4, 19, head_top + 4), fill=p["dark"])
    elif mood == "celebrate":
        # Heart-shaped eye
        d.rectangle((12, head_top + 3, 13, head_top + 4), fill=p["accent"])
        d.rectangle((14, head_top + 3, 15, head_top + 4), fill=p["accent"])
        d.rectangle((13, head_top + 5, 14, head_top + 5), fill=p["accent"])
        d.rectangle((17, head_top + 3, 18, head_top + 4), fill=p["accent"])
        d.rectangle((19, head_top + 3, 20, head_top + 4), fill=p["accent"])
        d.rectangle((18, head_top + 5, 19, head_top + 5), fill=p["accent"])
    else:
        d.rectangle((12, head_top + 4, 14, head_top + 5), fill=p["eye"])
        d.rectangle((17, head_top + 4, 19, head_top + 5), fill=p["eye"])
        d.point((12, head_top + 4), fill=(255, 255, 255))
        d.point((17, head_top + 4), fill=(255, 255, 255))
    # Antenna
    antenna_dx = [0, 1, 0, -1][antenna_phase % 4]
    d.line((15, head_top, 15, head_top - 3), fill=p["dark"])
    d.rectangle((14 + antenna_dx, head_top - 5, 16 + antenna_dx, head_top - 3),
                fill=p["antenna"])
    # Neck
    d.rectangle((13, head_top + 9, 18, head_top + 10), fill=p["dark"])
    # Body: bigger rectangle
    body_top = head_top + 10
    d.rectangle((6, body_top, 25, body_top + 10), fill=p["body"])
    d.rectangle((5, body_top + 1, 26, body_top + 9), fill=p["body"])
    # Body shading
    d.line((5, body_top + 9, 26, body_top + 9), fill=p["dark"])
    d.line((5, body_top + 1, 5, body_top + 8), fill=p["dark"])
    d.line((6, body_top, 25, body_top), fill=p["light"])
    # Chest panel
    d.rectangle((10, body_top + 3, 21, body_top + 7), fill=p["dark"])
    d.rectangle((12, body_top + 4, 14, body_top + 5), fill=p["accent"])
    d.rectangle((16, body_top + 4, 18, body_top + 5), fill=p["antenna"])
    # Arms (stubby)
    d.rectangle((3, body_top + 3, 5, body_top + 7), fill=p["body"])
    d.rectangle((26, body_top + 3, 28, body_top + 7), fill=p["body"])


def pixel_idle() -> list[Image.Image]:
    a = new_frame(); pixel_body(a, y_off=0, antenna_phase=0, mood="idle")
    b = new_frame(); pixel_body(b, y_off=-1, antenna_phase=2, mood="idle")
    return [a, b]


def pixel_wave() -> list[Image.Image]:
    """Antenna wiggles + chest light blinks."""
    frames = []
    for (y_off, phase) in [(0, 0), (0, 1), (0, 2)]:
        img = new_frame()
        pixel_body(img, y_off=y_off, antenna_phase=phase, mood="celebrate")
        frames.append(img)
    return frames


def pixel_blink() -> list[Image.Image]:
    a = new_frame(); pixel_body(a, y_off=0, antenna_phase=0, mood="idle")
    b = new_frame(); pixel_body(b, y_off=0, antenna_phase=0, mood="sleep")
    c = new_frame(); pixel_body(c, y_off=0, antenna_phase=0, mood="idle")
    return [a, b, c]


def pixel_look() -> list[Image.Image]:
    return [new_frame_with(lambda i, p=p: pixel_body(i, y_off=0, antenna_phase=p, mood="idle"))
            for p in (1, 3, 0)]


def pixel_nod() -> list[Image.Image]:
    return [new_frame_with(lambda i, y=y: pixel_body(i, y_off=y, antenna_phase=0, mood="celebrate"))
            for y in (-1, 1, 0)]


def pixel_shake() -> list[Image.Image]:
    return [new_frame_with(lambda i, p=p: pixel_body(i, y_off=0, antenna_phase=p, mood="idle"))
            for p in (1, 3, 1, 3)]


def pixel_think() -> list[Image.Image]:
    frames = []
    for phase in range(3):
        img = new_frame()
        pixel_body(img, y_off=0, antenna_phase=phase, mood="idle")
        d = ImageDraw.Draw(img)
        for i in range(phase + 1):
            d.rectangle((22 + i * 2, 2, 23 + i * 2, 3), fill=PIXEL_P["eye"])
        frames.append(img)
    return frames


def pixel_eat() -> list[Image.Image]:
    return [new_frame_with(lambda i, p=p: pixel_body(i, y_off=0, antenna_phase=p, mood="celebrate"))
            for p in (0, 1, 2, 3)]


def pixel_bounce() -> list[Image.Image]:
    """Medium hop."""
    frames = []
    for (y_off, phase) in [(0, 0), (-2, 1), (-3, 2), (-1, 3)]:
        img = new_frame()
        pixel_body(img, y_off=y_off, antenna_phase=phase, mood="celebrate")
        frames.append(img)
    return frames


def pixel_celebrate() -> list[Image.Image]:
    frames = []
    for i, (y_off, phase, sparks) in enumerate([
        (0, 0, []),
        (-2, 1, [(3, 6), (28, 7)]),
        (-4, 2, [(2, 4), (29, 5), (16, 2)]),
        (-1, 3, [(5, 10), (27, 11)]),
    ]):
        img = new_frame()
        pixel_body(img, y_off=y_off, antenna_phase=phase, mood="celebrate")
        d = ImageDraw.Draw(img)
        for (sx, sy) in sparks:
            sparkle(d, sx, sy)
        frames.append(img)
    return frames


def pixel_sleep() -> list[Image.Image]:
    frames = []
    for i, z_y in enumerate([8, 5, 3]):
        img = new_frame()
        pixel_body(img, y_off=2, antenna_phase=0, mood="sleep")
        d = ImageDraw.Draw(img)
        letter_z(d, 22, z_y)
        if i >= 1:
            letter_z(d, 26, z_y - 4)
        frames.append(img)
    return frames


# ══════════════════════════════════════════════════════════════════
# DISPATCH + RUN
# ══════════════════════════════════════════════════════════════════
def _build(prefix: str) -> dict:
    """Collect every sprite-specific draw function matching the prefix
    into a {state: fn} dict. Keeps the dispatch table under 10 lines
    even with 11 states per sprite."""
    import sys
    mod = sys.modules[__name__]
    states = ["idle", "blink", "look", "nod", "shake", "think", "eat",
              "wave", "bounce", "celebrate", "sleep"]
    return {s: getattr(mod, f"{prefix}_{s}") for s in states}


SPRITES = {
    "joby":  _build("joby"),
    "rollo": _build("rollo"),
    "momo":  _build("momo"),
    "hoot":  _build("hoot"),
    "slim":  _build("slim"),
    "pixel": _build("pixel"),
}

DURATIONS = {
    "idle":      220,
    "blink":     100,
    "look":      220,
    "nod":       140,
    "shake":     110,
    "think":     260,
    "eat":       120,
    "wave":      180,
    "bounce":    140,
    "celebrate": 120,
    "sleep":     500,
}


def main() -> None:
    for sprite_key, states in SPRITES.items():
        for state, fn in states.items():
            frames = fn()
            out = OUT_DIR / f"{sprite_key}_{state}.gif"
            save_gif(frames, out, DURATIONS[state])
            print(f"wrote {out.name}  ({len(frames)} frames)")


if __name__ == "__main__":
    main()
