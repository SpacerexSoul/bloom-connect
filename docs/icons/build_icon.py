"""Build the bloom-connect app icon.

Design (v4 per krishna): orange B + green c on black.
- Pure black background — Terminal dark trading-screen feel.
- "Bc" monogram, set tight, lowercase c.
- B in Bloomberg signature orange (#FE9F00). c in connected-LED
  green (#2ea043, matches LED_COLOUR['happy'] in the UI). The c
  carries the live-connection accent in the letterform.
- Bold sans (Helvetica Neue Bold from the system) — heavy weight
  reads at 16×16 in Finder list view; lighter weights muddy.
- Bloomberg colour usage is descriptive (fair use for an
  interop-targeted tool); we don't use the Bloomberg name or logo
  in the icon. README already disclaims affiliation.

Output:
- icon_{16,32,64,128,256,512,1024}.png in docs/icons/png/
- bloom-connect.iconset/ macOS multi-resolution bundle
- bloom-connect.icns via `iconutil` (macOS built-in)
- bloom-connect.ico via Pillow ICO writer (Windows)

Run:
    python docs/icons/build_icon.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
PNG_DIR = HERE / "png"
ICONSET = HERE / "bloom-connect.iconset"
ICNS = HERE / "bloom-connect.icns"
ICO = HERE / "bloom-connect.ico"

BG = (0x00, 0x00, 0x00, 0xff)     # pure black, the Terminal screen
ORANGE = (0xfe, 0x9f, 0x00, 0xff) # Bloomberg signature orange
GREEN = (0x2e, 0xa0, 0x43, 0xff)  # LED_COLOUR['happy'] from the UI

# Helvetica Neue Bold ships with macOS; index 1 is typically Bold
# in the .ttc. Falls back to Arial Black if absent.
FONT_CANDIDATES = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Black.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, idx in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=idx)
            except Exception:
                continue
    return ImageFont.load_default()


def build(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(size * 0.22)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BG)

    # Pick font size so "Bc" fills ~60% of width. Helvetica Neue Bold
    # has B at ~0.72em wide, c at ~0.55em — total ~1.27em including a
    # tight kerned gap. Aim for total width ≈ 0.62 × size.
    target_width = size * 0.62
    # Iteratively close in on the right pt size.
    pt = int(size * 0.68)
    for _ in range(8):
        font = _load_font(pt)
        bbox_b = d.textbbox((0, 0), "B", font=font)
        bbox_c = d.textbbox((0, 0), "c", font=font)
        w = (bbox_b[2] - bbox_b[0]) + (bbox_c[2] - bbox_c[0])
        if w > target_width:
            pt = int(pt * 0.95)
        elif w < target_width * 0.92:
            pt = int(pt * 1.04)
        else:
            break

    font = _load_font(pt)
    # Geometry: place B + c side-by-side with a tight gap. Compute the
    # actual baseline-aware bbox so the lockup is optically centered.
    bb = d.textbbox((0, 0), "B", font=font)
    bc = d.textbbox((0, 0), "c", font=font)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    cw, ch = bc[2] - bc[0], bc[3] - bc[1]
    # Kerning: tight gap, ~4% of size.
    gap = int(size * 0.02)
    total_w = bw + gap + cw
    # Vertical center is the max of B's full height; align c to the
    # baseline of B (cap-height vs x-height). Empirical offset.
    bx = (size - total_w) // 2 - bb[0]
    by = (size - bh) // 2 - bb[1]
    cx = bx + bw + gap - bc[0]
    # Align c to the baseline of B — c sits lower because lowercase.
    cy = by + (bh - ch) - bc[1] + bb[1]

    d.text((bx, by), "B", font=font, fill=ORANGE)
    d.text((cx, cy), "c", font=font, fill=GREEN)

    return img


def main() -> int:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    ICONSET.mkdir(parents=True, exist_ok=True)

    sizes = {
        16:   ["icon_16x16.png"],
        32:   ["icon_16x16@2x.png", "icon_32x32.png"],
        64:   ["icon_32x32@2x.png"],
        128:  ["icon_128x128.png"],
        256:  ["icon_128x128@2x.png", "icon_256x256.png"],
        512:  ["icon_256x256@2x.png", "icon_512x512.png"],
        1024: ["icon_512x512@2x.png"],
    }

    for size, names in sizes.items():
        img = build(size)
        plain = PNG_DIR / f"icon_{size}.png"
        img.save(plain, "PNG")
        for name in names:
            img.save(ICONSET / name, "PNG")
        print(f"  built {size}×{size}")

    # Windows .ico — multi-resolution, single file.
    ico_base = build(256)
    ico_base.save(
        ICO,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"  wrote {ICO.name} ({ICO.stat().st_size} bytes)")

    if sys.platform != "darwin":
        print("  not on macOS; skipping .icns conversion.")
        return 0
    subprocess.run(["iconutil", "-c", "icns", "-o", str(ICNS), str(ICONSET)], check=True)
    print(f"  wrote {ICNS.name} ({ICNS.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
