"""Build the bloom-connect app icon.

Design rationale:
- The defining UI element across both server and client is the
  status LED ("● Connected" / "● Disconnected"). The icon picks up
  that motif: a single solid LED dot on a dark rounded-square
  background. Self-referential and instantly tells someone "this
  is an app about connection status".
- Charcoal background (#1f2328) matches the macOS dark-mode menu
  bar tint and reads cleanly in both light + dark Dock.
- LED uses the same green (#2ea043) as the "happy / Connected"
  state from LED_COLOUR in blpremote_client.ui — visual consistency
  between the icon and the running UI.
- No letterforms, no Bloomberg-orange (that's a trademark concern),
  no chart line cliché. The dot speaks for itself.

Output:
- icon_1024.png, icon_512.png, icon_256.png, icon_128.png,
  icon_64.png, icon_32.png, icon_16.png in docs/icons/png/
- bloom-connect.iconset/ macOS-style multi-resolution bundle
- bloom-connect.icns produced via `iconutil` (macOS built-in)

Run:
    python docs/icons/build_icon.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
PNG_DIR = HERE / "png"
ICONSET = HERE / "bloom-connect.iconset"
ICNS = HERE / "bloom-connect.icns"

BG = (0x1f, 0x23, 0x28, 0xff)   # GitHub Primer charcoal — matches macOS dark menu bar
LED = (0x2e, 0xa0, 0x43, 0xff)  # GitHub Primer green — matches LED_COLOUR['happy']
LED_HIGHLIGHT = (0x5a, 0xc7, 0x70, 0xff)


def build(size: int) -> Image.Image:
    """Render the icon at one size. The proportions are picked so
    the LED reads cleanly down to 16×16 (Finder list view)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square background. macOS app icons use a "squircle"
    # not a true rounded rect; with PIL we approximate via rounded
    # rectangle with a corner radius ~22% of size — matches the
    # macOS Big Sur+ icon shape closely enough.
    radius = int(size * 0.22)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BG)

    # LED dot — 50% diameter, centered. A tiny lighter highlight at
    # the upper-left gives it the "glowing pixel" feel without being
    # cartoonish.
    cx, cy = size / 2, size / 2
    r = size * 0.25
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LED)

    # Specular highlight — small, offset toward upper-left.
    if size >= 64:
        hr = r * 0.32
        hx, hy = cx - r * 0.32, cy - r * 0.32
        d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=LED_HIGHLIGHT)

    return img


def main() -> int:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    ICONSET.mkdir(parents=True, exist_ok=True)

    # macOS iconset wants these specific filenames + sizes.
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
        # also save plain PNGs for repo/docs use
        plain = PNG_DIR / f"icon_{size}.png"
        img.save(plain, "PNG")
        for name in names:
            img.save(ICONSET / name, "PNG")
        print(f"  built {size}×{size} ({len(names)} iconset variant{'s' if len(names) > 1 else ''})")

    # Windows .ico — multi-resolution single file. Pillow handles
    # this in one save call; same source PNGs, no extra tooling.
    ico_path = HERE / "bloom-connect.ico"
    ico_base = build(256)
    ico_base.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"  wrote {ico_path.name} ({ico_path.stat().st_size} bytes)")

    print(f"\nrunning iconutil → {ICNS.name}")
    if sys.platform != "darwin":
        print("  not on macOS; skipping .icns conversion. PNGs are in docs/icons/png/.")
        return 0
    subprocess.run(
        ["iconutil", "-c", "icns", "-o", str(ICNS), str(ICONSET)],
        check=True,
    )
    print(f"  wrote {ICNS.name} ({ICNS.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
