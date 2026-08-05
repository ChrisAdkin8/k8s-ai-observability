#!/usr/bin/env python3
"""Build a 1280x640 GitHub social preview card from the LLM dashboard screenshot."""
from PIL import Image, ImageDraw, ImageFont
import os, sys

# Both paths resolve from THIS FILE, never from the working directory. The repo
# root was hardcoded to one machine's home directory, so the script ran nowhere
# else — and CONTRIBUTING.md now sends contributors here. Same idiom as
# optimize-images.py and dashboard-logos.py, which are the two scripts beside it.
DOCS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(DOCS, "llm-dashboard.png")

# ⚠️ The default OUT was "social-preview.png" — relative to the CWD. Run from the
# repo root with no argument and it wrote a stray card THERE, exited 0 and printed
# a success line, while docs/social-preview.png, the file that is committed and the
# one GitHub serves, was never touched. Same shape as the crop trap below: a
# plausible result, no failure, wrong file.
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DOCS, "social-preview.png")

W, H = 1280, 640
BG = (13, 14, 18)
FG = (255, 255, 255)
MUTED = (150, 157, 170)
GREEN = (115, 191, 105)   # Grafana's series green, straight off the chart
RULE = (38, 42, 51)

# --- the panel band ------------------------------------------------------
# Crop the top TWO rows of the board: "Time to first token - p95 (alert fires above
# 2s)" and "Inter-token latency - p95", then both "Requests running vs waiting"
# repeats. Four panels complete, no awkward cuts. Two rows rather than one because
# the band wants an aspect near 4.5 to fill the card's top third — one row of this
# board is a 10:1 strip, which leaves the card mostly empty.
#
# Expressed as FRACTIONS of the source, not pixels, because docs/optimize-images.py
# resizes this file to 2400px wide: pixel coordinates would silently move the crop
# on the next optimisation pass.
#
# ⚠️ FRACTIONS SURVIVE A RESIZE, NOT A RE-CAPTURE — and the difference cost a
# release. These numbers used to be (0.1826, 0.1157, 0.9586, 0.4422), justified in a
# comment claiming "the top row occupies this proportion of the board whatever size
# the window was". It does not. The 2026-08-01 retake was taken with a narrower
# Grafana sidebar and a board one panel taller, which moved every boundary: the old
# fractions landed mid-panel, and the card rendered cut through two rows. NOTHING
# FAILED — the script exited 0 and wrote a plausible, wrong card, which is the whole
# reason this warning is here rather than a bare tuple.
#
# So: RE-CHECK THIS CROP WHENEVER llm-dashboard.png IS RETAKEN. The row boundaries
# are measurable — scan the source for rows that are uniformly page-background and
# take the gutters between panels. On the current capture they are 0.0565-0.2252
# (row 1) and 0.2288-0.3968 (row 2), which is where the y values below come from.
FRAC = (0.0908, 0.0565, 0.9792, 0.3968)     # x0, y0, x1, y1

src = Image.open(SRC).convert("RGB")
sw, sh = src.size
box = (round(FRAC[0] * sw), round(FRAC[1] * sh), round(FRAC[2] * sw), round(FRAC[3] * sh))
BAND_H = round(W / ((box[2] - box[0]) / (box[3] - box[1])))   # 282 at any source size

if box[2] - box[0] < W:
    print(f"WARN: crop is {box[2]-box[0]}px wide, upscaling to {W} — card text will be "
          f"soft. Regenerate from a source at least {round(W / (FRAC[2]-FRAC[0]))}px wide.",
          file=sys.stderr)

panel = src.crop(box).resize((W, BAND_H), Image.LANCZOS)

card = Image.new("RGB", (W, H), BG)
card.paste(panel, (0, 0))
d = ImageDraw.Draw(card)

# Hairline under the band so the screenshot reads as a distinct object.
d.line([(0, BAND_H), (W, BAND_H)], fill=RULE, width=2)


def font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    print(f"WARN: falling back to default at {size}px", file=sys.stderr)
    return ImageFont.load_default()


S = "/System/Library/Fonts/Supplemental/"
f_title = font([S + "Arial Bold.ttf", "/System/Library/Fonts/HelveticaNeue.ttc"], 56)
f_tag = font([S + "Arial.ttf", "/System/Library/Fonts/HelveticaNeue.ttc"], 29)
f_meta = font([S + "Arial.ttf"], 21)
f_mono = font(["/System/Library/Fonts/Menlo.ttc", S + "Courier New.ttf"], 23)
f_mono_s = font(["/System/Library/Fonts/Menlo.ttc", S + "Courier New.ttf"], 16)

x = 56
y = BAND_H + 42

d.text((x, y), "k8s-ai-observability", font=f_title, fill=FG)
y += 74
d.text((x, y), "Build and test GPU and LLM observability", font=f_tag, fill=MUTED)
y += 39
d.text((x, y), "without a GPU.", font=f_tag, fill=MUTED)

# --- right column: the one-command story, so the card shows the cost of entry ---
bx0, bx1 = 700, W - 56
by0 = BAND_H + 52
by1 = by0 + 116
d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=(22, 24, 29), outline=RULE, width=2)

d.text((bx0 + 24, by0 + 26), "$", font=f_mono, fill=(90, 98, 112))
d.text((bx0 + 46, by0 + 26), "docker compose up -d", font=f_mono, fill=FG)
d.text((bx0 + 24, by0 + 68), "both boards on :3000  ·  ~1 min  ·  no Kubernetes",
       font=f_mono_s, fill=MUTED)

# --- bottom meta line: the stack, and the claim that does the work ---
meta_y = H - 44
left = "kind · EKS · GKE   ·   Prometheus + Grafana   ·   vLLM + DCGM"
d.text((x, meta_y), left, font=f_meta, fill=MUTED)

claim = "Full stack in CI on a free runner"
cw = d.textlength(claim, font=f_meta)
d.text((W - 56 - cw, meta_y), claim, font=f_meta, fill=GREEN)

card.save(OUT, "PNG", optimize=True)
print(f"wrote {OUT}  {card.size[0]}x{card.size[1]}  {os.path.getsize(OUT)/1024:.0f} KB")
