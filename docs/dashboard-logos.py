#!/usr/bin/env python3
"""Build the grafana.com logos: one per dashboard, plus the org mark.

    pip install pillow && python3 docs/dashboard-logos.py     # -> docs/logos/*.png

One square PNG per board and one for the organisation, generated rather than
drawn by hand for the same reason the boards themselves are files rather than
clicks: it is re-runnable, it diffs, and the marks cannot drift apart into a
mismatched set.

WHY 512x512. The catalog renders a dashboard's logo small — think listing-card
size — but the upload wants something it can scale down itself, and a 512px master
downsamples cleanly to anything below it. It is a few KB either way. Everything
here is sized in FRACTIONS of the canvas (see U), so changing SIZE re-renders the
same mark rather than a differently-proportioned one.

WHY A DARK TILE rather than transparency. grafana.com renders in both light and
dark, and a transparent mark drawn in dark strokes disappears on one of them. A
solid tile with its own background is the only version that is legible on both
without shipping two files.

WHY SUPERSAMPLING. PIL's ImageDraw has no antialiasing: a diagonal drawn at final
size comes out visibly stepped, which is exactly what a logo cannot afford. So
everything is drawn at SS times the target and downsampled with LANCZOS, which is
the cheapest way to get clean curves without pulling in a real vector renderer.

DESIGN, and what it is trying to survive
----------------------------------------
Legibility at ~64px is the binding constraint, not detail at 512. Three rules
follow from it, and they are why this looks sparser than it could:

  * few elements, none thin — strokes are >= 0.03 of the canvas, so nothing
    drops below ~2px on a small render;
  * the marks share a tile, a corner radius, a margin and a stroke weight, so
    they read as a set on an org page. What differs is the SUBJECT — a chip with
    pins for the hardware board, a panel frame for the serving board, and for the
    org the two of them fused (see org_logo);
  * colour carries the meaning, and it is each board's own: DCGM green for GPU,
    and for LLM the story the first panel tells — a healthy tenant in green under
    the threshold, a saturated one in red above it.

No text. At listing size any wordmark turns to mud, and the catalog prints the
dashboard's title beside the logo regardless.
"""
from PIL import Image, ImageDraw
import os
import sys

SIZE = 512          # master; the catalog scales down from here
SS = 4              # supersample factor — see WHY SUPERSAMPLING above
S = SIZE * SS

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos")

# Grafana's own series palette, so a mark sits beside a screenshot of the board it
# stands for without a colour clash. Straight off the chart, same as social-preview.py.
BG = (13, 14, 18)
GREEN = (115, 191, 105)
BLUE = (87, 148, 242)
RED = (242, 73, 92)
DIM = (78, 87, 102)


def U(*fracs):
    """Fractions of the canvas -> pixels. Every coordinate below goes through this."""
    return [round(f * S) for f in fracs] if len(fracs) > 1 else round(fracs[0] * S)


def canvas():
    """A tile with the shared corner radius. Both marks start here."""
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    return img, d


def finish(img, name):
    out = os.path.join(OUT_DIR, name)
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, "PNG", optimize=True)
    print(f"  {out}  {SIZE}x{SIZE}  {os.path.getsize(out) / 1024:.1f} KB")


def curve(d, pts, colour, width):
    """A polyline with rounded joins — `joint="curve"` is PIL's only smoothing."""
    d.line([tuple(U(x, y)) for x, y in pts], fill=colour, width=U(width), joint="curve")


# ---- GPU: a die with pins, utilisation inside it -----------------------------
# The chip says hardware; the bars say "this is a metrics board", and their uneven
# heights say the fleet is not uniformly loaded — which is the first thing the real
# panel shows (four driven GPUs, the rest flat).
def gpu_logo():
    img, d = canvas()

    frame = 0.28, 0.72            # the die
    stroke = 0.036
    d.rounded_rectangle(U(frame[0], frame[0], frame[1], frame[1]),
                        radius=U(0.055), outline=GREEN, width=U(stroke))

    # Pins: three a side, on the same three offsets every side, so the mark is
    # symmetric under rotation and reads as a package rather than as decoration.
    pin_len, pin_w = 0.075, 0.032
    for off in (0.40, 0.50, 0.60):
        a, b = off - pin_w / 2, off + pin_w / 2
        d.rectangle(U(frame[0] - pin_len, a, frame[0], b), fill=GREEN)          # left
        d.rectangle(U(frame[1], a, frame[1] + pin_len, b), fill=GREEN)          # right
        d.rectangle(U(a, frame[0] - pin_len, b, frame[0]), fill=GREEN)          # top
        d.rectangle(U(a, frame[1], b, frame[1] + pin_len), fill=GREEN)          # bottom

    # Utilisation bars, baseline inset from the die's inner edge. Three, not four:
    # a fourth drops each bar under ~4px at listing size and they merge into a block.
    base, bw, gap = 0.645, 0.082, 0.048
    x = 0.5 - (3 * bw + 2 * gap) / 2
    for height in (0.20, 0.115, 0.265):
        d.rectangle(U(x, base - height, x + bw, base), fill=GREEN)
        x += bw + gap

    finish(img, "gpu-sim-dcgm.png")


# ---- LLM: the first panel, reduced to its argument ---------------------------
# Two tenants, one threshold, one of them through it. That IS the board — same
# code, same panel, one degraded — so the mark is that panel with everything
# removed that does not carry the point.
def llm_logo():
    img, d = canvas()

    # The panel frame is DIM rather than a series colour on purpose: it is the
    # container, and colouring it competes with the two tenants for the eye. It
    # also earns its place — it is what makes this mark and the die read as a
    # pair, both being "a rounded outline with content inside".
    frame, stroke = (0.18, 0.82), 0.03
    d.rounded_rectangle(U(frame[0], frame[0], frame[1], frame[1]),
                        radius=U(0.055), outline=DIM, width=U(stroke))

    # The threshold. Dashed, because a solid rule at this weight reads as an axis;
    # dashes read as a limit somebody chose.
    ty, dash, space = 0.47, 0.06, 0.042
    x = 0.26
    while x < 0.74:
        d.rectangle(U(x, ty - 0.017, min(x + dash, 0.74), ty + 0.017), fill=RED)
        x += dash + space

    # Saturated: climbs off the bottom and through the line, ending above it. It
    # crosses at ~60% of the width, so the breach — the thing the mark is about —
    # lands in open space rather than in a corner.
    #
    # The top end stops at 0.345, and that number is a clearance, not a taste:
    # half the stroke (0.025) has to clear the frame's inner edge at
    # frame[0] + stroke. Run the curve any higher and it fuses with the frame at
    # small sizes, which is what the first draft did.
    curve(d, [(0.27, 0.685), (0.40, 0.66), (0.51, 0.555), (0.60, 0.43), (0.71, 0.345)],
          RED, 0.05)

    # Healthy: flat, low, well clear of the line. Drawn second so it sits on top
    # where they pass close, keeping both readable at small sizes.
    curve(d, [(0.27, 0.71), (0.42, 0.688), (0.56, 0.697), (0.71, 0.683)],
          GREEN, 0.05)

    finish(img, "llm-sim-overview.png")


# ---- ORG: the two boards' subjects fused -------------------------------------
# This one sits ABOVE the other two on the org page, so it has a different job:
# it must read as the thing they both came FROM, not as a third of the same kind.
# The first attempt was a frame with a threshold and two tenants through it — and
# that IS the LLM mark, so it read as a sibling. This is the union instead: the
# GPU mark's die, with the LLM mark's signal running through it.
#
# ⚠️ THE DIE IS BLUE, NOT DIM, AND THAT IS A LEGIBILITY FIX RATHER THAN A
# PREFERENCE. The LLM mark makes its container DIM so the frame does not compete
# with the two tenants, and the same choice here was right at 512 and wrong at
# 64: DIM (78,87,102) against this tile is too close in value to survive the
# downsample, so the die dissolved and the mark became "a green squiggle".
# Checked by rendering the 64px version, which is the only size that settles it.
# Blue also belongs to neither board as a subject colour, so the family reading
# survives — the parent is not wearing either child's colour.
#
# The signal runs EDGE TO EDGE, past the die on both sides. That is what makes it
# read as passing THROUGH the hardware rather than as being drawn inside it,
# which is the whole claim: this repo observes the thing, it does not contain it.
#
# TWO pins a side, not the GPU mark's three, because the middle offset is where
# the signal crosses — a pin there collides with it at small sizes. They sit at
# 0.365/0.635 rather than tighter, which keeps them off the frame's rounded
# corners where they otherwise read as a smudge.
def org_logo():
    img, d = canvas()

    frame, stroke = (0.255, 0.745), 0.044
    d.rounded_rectangle(U(frame[0], frame[0], frame[1], frame[1]),
                        radius=U(0.06), outline=BLUE, width=U(stroke))

    pin_len, pin_w = 0.072, 0.040
    for off in (0.365, 0.635):
        a, b = off - pin_w / 2, off + pin_w / 2
        d.rectangle(U(frame[0] - pin_len, a, frame[0], b), fill=BLUE)
        d.rectangle(U(frame[1], a, frame[1] + pin_len, b), fill=BLUE)
        d.rectangle(U(a, frame[0] - pin_len, b, frame[0]), fill=BLUE)
        d.rectangle(U(a, frame[1], b, frame[1] + pin_len), fill=BLUE)

    # ONE polyline for the whole path. Drawn as several overlapping calls it
    # leaves a visible notch at each handover, because joint="curve" only
    # smooths joints WITHIN a call — which is exactly what the first draft did.
    curve(d, [(0.075, 0.50), (0.36, 0.50), (0.44, 0.50),
              (0.50, 0.315), (0.575, 0.685), (0.635, 0.50),
              (0.70, 0.50), (0.925, 0.50)], GREEN, 0.062)

    finish(img, "org-chrisadkin.png")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    gpu_logo()
    llm_logo()
    org_logo()
    print("\nBoard logos upload with their boards — see manifests/dashboards/README.md.")
    print("org-chrisadkin.png is the ORG logo: grafana.com -> your org -> Settings.")


if __name__ == "__main__":
    sys.exit(main())
