#!/usr/bin/env python3
"""Optimise the dashboard screenshots for GitHub. Run after retaking one.

    pip install pillow && python3 docs/optimize-images.py

Idempotent: a file already at the target width and colour depth is left alone, so
this is safe to re-run and safe to wire into a pre-commit hook.

WHY 2400px, when GitHub renders READMEs in a ~896 CSS-px column and 2x that is
1792? Because these files have a second consumer. docs/social-preview.py crops the
top ~78% of llm-dashboard.png and renders it 1280px wide, so a 1792px source leaves
that crop at 1391px — a 1.09x downscale, which measurably softens the card's text.
2400px keeps it a genuine 1.45x supersample. The cost is ~220 KB across both files
versus 1792px; the alternative is a second full-resolution copy of the same
screenshot, which is the kind of duplicate this repo avoids everywhere else.

WHY palette-256 rather than a quality knob: these are flat UI screenshots with
~2700 distinct colours, so a 256-entry palette is visually lossless here — measured
mean channel error 0.19/255, and dithering changes nothing because there is almost
no gradient to dither. It is not a lossy setting that happens to look acceptable.
Re-check that assumption if the boards ever gain a heat-map or a photographic panel.
"""
from PIL import Image
import os
import sys

WIDTH = 2400
COLOURS = 256
DOCS = os.path.dirname(os.path.abspath(__file__))
IMAGES = ("gpu-dashboard.png", "llm-dashboard.png")


def optimise(path):
    before = os.path.getsize(path)
    im = Image.open(path)

    if im.size[0] <= WIDTH and im.mode == "P":
        print(f"  {os.path.basename(path):22} already optimised, skipped")
        return 0, 0

    im = im.convert("RGB")
    if im.size[0] > WIDTH:
        h = round(im.size[1] * WIDTH / im.size[0])
        im = im.resize((WIDTH, h), Image.LANCZOS)

    # MEDIANCUT + no dither: dithering buys nothing on flat UI and costs bytes.
    im = im.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT,
                     dither=Image.Dither.NONE)
    im.save(path, "PNG", optimize=True)

    after = os.path.getsize(path)
    print(f"  {os.path.basename(path):22} {im.size[0]}x{im.size[1]}  "
          f"{before/1024:6.0f}K -> {after/1024:5.0f}K  ({100 - 100*after/before:.0f}% smaller)")
    return before, after


if __name__ == "__main__":
    print(f"optimising to {WIDTH}px wide, {COLOURS}-colour palette")
    total_b = total_a = 0
    for name in IMAGES:
        p = os.path.join(DOCS, name)
        if not os.path.exists(p):
            sys.exit(f"ERROR: {p} does not exist")
        b, a = optimise(p)
        total_b += b
        total_a += a
    if total_b:
        print(f"  {'total':22} {total_b/1024:6.0f}K -> {total_a/1024:5.0f}K")
        print("\nNOTE: llm-dashboard.png feeds docs/social-preview.py — regenerate the")
        print("card so it matches the source it claims to come from:")
        print("  python3 docs/social-preview.py docs/social-preview.png")
