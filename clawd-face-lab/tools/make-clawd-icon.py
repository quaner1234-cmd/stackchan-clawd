# -*- coding: utf-8 -*-
"""Generate clawd.ico (desktop shortcut icon) from the project's own Clawd render.

Source : tools/render-check/happy-standby.png  (320x240, casual-happy front face
         rendered by tools/render-check.js from index.html)
Output : ../clawd.ico  (multi-size Windows icon, transparent background)
Method : key out the cream background (#FAF7F2) and the soft floor shadow
         (desaturated + bright pixels), keep a smooth alpha edge, crop to the
         character, pad to a square, export ICO sizes 16..256 with Pillow's
         LANCZOS downscale.

Run: python tools/make-clawd-icon.py
Deps: Pillow.

Provenance
  Agent   : Hermes (opencode)
  Date    : 2026-10-18 (Asia/Shanghai)
  Session : ses_eb2f45779ffeK8kronrATo1eta
  Task    : 启动小克聊天 desktop shortcut icon -> orange Clawd
"""
from pathlib import Path
from PIL import Image

LAB = Path(__file__).resolve().parent.parent
RENDER = LAB / "tools" / "render-check"
SRC = RENDER / "happy-standby.png"      # face-forward happy (sizes >= 32)
SRC_SMALL = RENDER / "neutral.png"      # square eyes stay legible at <= 24 px
OUT = LAB / "clawd.ico"
MASTER = 256
FILL_W = 0.88          # character width as a fraction of the square
BG = (250, 247, 242)   # canvas cream from index.html
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (40, 40),
         (32, 32), (24, 24), (20, 20), (16, 16)]
SMALL_MAX = 24         # sizes <= this use the neutral face


def extract_character(im: Image.Image) -> Image.Image:
    """RGBA character only: cream bg and floor shadow removed, cropped to bbox."""
    im = im.convert("RGBA")
    w, h = im.size
    src, out = im.load(), Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dst = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _ = src[x, y]
            sat = max(r, g, b) - min(r, g, b)
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if sat <= 25 and lum >= 185:      # background / soft floor shadow
                continue
            d = max(abs(r - BG[0]), abs(g - BG[1]), abs(b - BG[2]))
            if d <= 6:
                continue
            a = 255 if d >= 40 else int(round(255 * (d - 6) / 34))
            dst[x, y] = (r, g, b, a)
    return out.crop(out.getbbox())


def to_square(im: Image.Image, size: int, fill_w: float) -> Image.Image:
    tw = max(1, int(size * fill_w))
    scale = tw / im.width
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    if nw > size or nh > size:
        s = min(size / nw, size / nh) * 0.98
        nw, nh = max(1, int(nw * s)), max(1, int(nh * s))
    im2 = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im2, ((size - nw) // 2, (size - nh) // 2), im2)
    return canvas


def main() -> None:
    assert SRC.exists() and SRC_SMALL.exists(), "missing source renders under tools/render-check"
    big = extract_character(Image.open(SRC))
    small = extract_character(Image.open(SRC_SMALL))
    print(f"character bbox: big {big.size[0]}x{big.size[1]}, small {small.size[0]}x{small.size[1]}")
    master = to_square(big, MASTER, FILL_W)
    # Provide every size explicitly; the neutral face is used for the smallest
    # sizes where the smile+curved eyes mush together.
    frames = {s[0]: to_square(small if s[0] <= SMALL_MAX else big, s[0], FILL_W)
              for s in SIZES}
    master.save(OUT, format="ICO", sizes=SIZES,
                append_images=[frames[n] for n, _ in SIZES if n != MASTER])
    print(f"saved {OUT} ({OUT.stat().st_size} bytes, {len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
