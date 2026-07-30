#!/usr/bin/env python3
"""Build a 1280x640 GitHub social preview card from the LLM dashboard screenshot."""
from PIL import Image, ImageDraw, ImageFont
import os, sys

REPO = "/Users/chris.adkin/projects/k8s-ai-observability"
SRC = os.path.join(REPO, "docs/llm-dashboard.png")
OUT = sys.argv[1] if len(sys.argv) > 1 else "social-preview.png"

W, H = 1280, 640
BG = (13, 14, 18)
FG = (255, 255, 255)
MUTED = (150, 157, 170)
GREEN = (115, 191, 105)   # Grafana's series green, straight off the chart
RULE = (38, 42, 51)

# --- the panel band ------------------------------------------------------
# Crop the whole top row of the board: "Time to first token - p95 (alert fires
# above 2s)" and "Inter-token latency - p95". Coords are in the ORIGINAL 3456x1988
# screenshot. Both panels complete, no awkward cuts.
CROP = (631, 230, 631 + 2682, 230 + 649)   # 2682x649, ~4.13:1
BAND_H = round(1280 / (2682 / 649))         # 310

panel = Image.open(SRC).convert("RGB").crop(CROP).resize((W, BAND_H), Image.LANCZOS)

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
