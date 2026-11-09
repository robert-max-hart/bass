#!/usr/bin/env python3
"""Convert a chroma-keyed image (default magenta #FF00FF) to a transparent PNG.

Uses color unmixing rather than a hard threshold: each pixel is modeled as
C = a*F + (1-a)*K (K = key color), so we solve for alpha and the true
foreground F. Anti-aliased edges get partial alpha and lose their pink fringe.

Usage: unkey.py input.(png|jpg) [output.png] [--tolerance N] [--key COLOR]

COLOR is hex (FF00FF, #0f0) or a CSS name (lime, cyan).
Requires only Pillow.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageColor


def parse_color(text):
    """Parse 'RRGGBB', '#RRGGBB', 'RGB' shorthand, or a CSS color name."""
    t = text.strip()
    h = t.lstrip("#")
    if len(h) == 3 and all(c in "0123456789abcdefABCDEF" for c in h):
        h = "".join(c * 2 for c in h)
    if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    try:
        return ImageColor.getrgb(t)[:3]
    except ValueError:
        sys.exit(f"Invalid key color: {text!r}")


def unkey(img, key=(255, 0, 255), tolerance=0):
    kr, kg, kb = key
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    corner = all(k in (0, 255) for k in key)
    clamp = lambda v: max(0, min(255, int(round(v))))
    for y in range(h):
        for x in range(w):
            r, g, b, a0 = px[x, y]
            if corner:
                # Key is a cube corner (each channel 0 or 255): keyness is how
                # far the key's "on" channels exceed its "off" channels.
                # For magenta this is min(R,B) - G.
                on = min([c for c, k in ((r, kr), (g, kg), (b, kb)) if k], default=255)
                off = max([c for c, k in ((r, kr), (g, kg), (b, kb)) if not k], default=0)
                m = (on - off) / 255.0
            else:
                # Arbitrary key: largest m for which F = (C - m*K)/(1-m)
                # stays within 0..255 in every channel.
                m = 1.0
                for c, k in ((r, kr), (g, kg), (b, kb)):
                    if k > 0:
                        m = min(m, c / k)
                    if k < 255:
                        m = min(m, (255 - c) / (255 - k))
            m = min(max(m, 0.0), 1.0)
            # Snap near-key pixels fully transparent; ignore faint tints.
            if m >= 1.0 - tolerance / 255.0:
                px[x, y] = (0, 0, 0, 0)
                continue
            alpha = 1.0 - m
            # Recover foreground: F = (C - m*K) / alpha
            px[x, y] = (clamp((r - m * kr) / alpha),
                        clamp((g - m * kg) / alpha),
                        clamp((b - m * kb) / alpha),
                        int(round(alpha * a0)))
    return img


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input")
    p.add_argument("output", nargs="?", help="default: <input>_alpha.png")
    p.add_argument("--tolerance", type=int, default=8,
                   help="0-255; pixels this close to the key become fully transparent (default 8)")
    p.add_argument("--key", default="FF00FF",
                   help="key color: hex (RRGGBB, #RGB) or CSS name (default FF00FF/magenta)")
    a = p.parse_args()

    src = Path(a.input)
    out = Path(a.output) if a.output else src.with_name(src.stem + "_alpha.png")
    if out.suffix.lower() != ".png":
        sys.exit("Output must be .png (JPG cannot store transparency)")
    key = parse_color(a.key)
    unkey(Image.open(src), key, a.tolerance).save(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
