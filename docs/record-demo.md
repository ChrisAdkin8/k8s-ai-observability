# Recording the README demo

The README's hero image is a **still**, and a still cannot show the one thing this rig
exists to demonstrate: a healthy tenant and a saturated one *diverging* across the 2s
threshold. That is a motion argument. This page is the recipe for capturing it, written
down because two of the steps fail silently and cost a re-record to discover.

Output: `docs/llm-demo.gif`, embedded above the fold in [`README.md`](../README.md).

## ⚠️ Record against compose, not against kind

The obvious approach — `task local:up`, then `./scripts/drive-llm-load.sh ramp` — does not
work, for two independent reasons:

- **`ramp` takes 16.5 minutes.** It walks eleven arrival rates and holds each for
  `STEP_SECONDS`, which defaults to **90**. 11 x 90 = 990s.
- **You cannot simply shorten it.** `drive-llm-load.sh`'s own header records that
  Kubernetes takes up to **~60s** to propagate a ConfigMap into a running pod. Set
  `STEP_SECONDS` below that and the profile for step *n* is still in flight when step *n+1*
  overwrites it — the curve on the board is not the curve in the script, and nothing warns
  you. You get a recording of a load ramp that never happened.

The compose stack has neither problem. It **mounts the profile directory straight into the
simulators**, and each polls its own file every **10 seconds** (see
[`compose/README.md`](../compose/README.md)). An edit lands in about the time it takes to
alt-tab.

```sh
(cd compose && PROMETHEUS_PORT=19090 GRAFANA_PORT=13000 docker compose up -d)
```

⚠️ **Both compose commands run in a subshell on purpose.** A bare `cd compose` persists in your shell, and the profile edit below is written from the REPO ROOT — after a leaked `cd` it resolves to `compose/compose/.generated/...`, which does not exist. Every path in this file is root-relative; keep it that way.

The non-default ports are deliberate: 3000 and 9090 collide with `scripts/grafana.sh` and
`scripts/prometheus.sh`, and a loopback-bound port-forward wins silently — you would record
the *cluster's* Grafana while believing it was compose's.

**Let it run ~90 seconds before recording**, or an immediate capture shows a rig that
looks broken: `rate()` needs two scrapes before it reports anything at all, and the
saturated tenant's queue takes longer than that to reach the plateau the board is meant to
show.

⚠️ It does **not** need the full `[5m]` window. `rate()` extrapolates within whatever the
window holds, so a steady counter reads correctly from about the second scrape — waiting
five minutes buys nothing. 90s is what the queue needs, not what the range needs.

## The frame

Open the board in **kiosk mode**, which strips Grafana's nav and sidebar:

```text
http://localhost:13000/d/llm-sim-overview?kiosk&from=now-5m&to=now&refresh=5s
```

Three parameters, and all three matter:

| | Why |
|--|--|
| `?kiosk` | removes the chrome, so the frame is all board. It also fixes the framing variable that broke `social-preview.py`'s crop once already — a capture whose sidebar width differs from the last one moves every fraction in that file |
| `refresh=5s` | ⚠️ **the board ships `refresh: 30s`.** At that rate a twelve-second GIF contains at most one repaint, and usually none. This is the trap that costs a re-record |
| `from=now-5m` | the shipped default is `now-15m`, which squeezes the interesting part into the right-hand third |

Kiosk mode is Grafana 11 syntax; the compose stack pins `grafana/grafana:11.6.0`.

## The twelve seconds

Frame the **Time to first token — p95** panel, start recording, then push the healthy
tenant over capacity. It lands within ~10s:

```sh
python3 - <<'PY'
import json
p = 'compose/.generated/profiles/steady.json'
d = json.load(open(p)); d['arrival_rate_rps'] = 6.0
json.dump(d, open(p, 'w'), indent=2)
PY
```

The queue builds, TTFT climbs, and the line crosses the 2s threshold. 6.0 rps is
`llm-saturated`'s rate — 2.19x capacity — so the healthy tenant walks up to meet the
saturated one, which is the whole demonstration in one gesture.

Restore afterwards; `generate` rewrites the profiles from the manifests on every start:

```sh
(cd compose && docker compose up -d --force-recreate generate)
```

`.generated/` is gitignored, so nothing here can be committed by accident.

## Capturing

Nothing needed ships with macOS beyond the recorder itself. Either:

- **[Kap](https://getkap.co)** (`brew install --cask kap`) — records a region and exports
  GIF directly. Fewest steps, less control over size.
- **`Cmd+Shift+5`** to record a region to `.mov`, then encode with `ffmpeg`
  (`brew install ffmpeg`). Better quality per byte, and reproducible — which is why the
  flags below are written down rather than left to a GUI.

## Encoding

Two passes. A single-pass GIF encode picks its 256 colours from one frame and bands
everything else:

```sh
# 1. build a palette from what actually CHANGES between frames
ffmpeg -i demo.mov -vf "fps=12,scale=1200:-1:flags=lanczos,palettegen=stats_mode=diff" \
  -y /tmp/pal.png

# 2. encode against it
ffmpeg -i demo.mov -i /tmp/pal.png -lavfi \
  "fps=12,scale=1200:-1:flags=lanczos,paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  -y docs/llm-demo.gif
```

`stats_mode=diff` and `diff_mode=rectangle` are the two that earn their keep on a screen
recording: they spend the palette on the moving region and leave the static dashboard
behind it alone. On a board where most pixels never change that is routinely a 3-5x size
reduction at identical quality. Same reasoning as
[`optimize-images.py`](optimize-images.py)'s 256-colour argument — flat UI quantises
losslessly and there is no gradient to dither — applied per frame instead of per file.

Useful additions: `-ss 3 -t 12` to trim, `setpts=0.25*PTS` inside the filter chain for a
4x speed-up.

**Target under 3 MB, hard ceiling 5 MB.** GitHub serves larger, but the README hero is
precisely where a slow load costs the visitor. 1200px wide is ample: GitHub renders READMEs
in a ~896 CSS-px column, so that is already a 1.34x supersample.

⚠️ **Do not add the GIF to `IMAGES` in [`optimize-images.py`](optimize-images.py).** That
script opens each file with Pillow, converts to `RGB` and saves as `"PNG"` regardless of
the path — on an animated GIF that writes single-frame PNG data into a `.gif` filename.
It would exit 0 and report a large saving. The animation would simply be gone.

## Why a GIF rather than an MP4

Dragging an `.mp4` into a GitHub issue gives a real video player, far smaller and sharper.
It is also hosted outside the repository, so it breaks on mirrors and on any renderer that
is not github.com, and it cannot appear in the social preview card. A hero image that only
works in one place is not a hero image.

## Why this asset is not generated

Every other visual here is re-rendered from a script —
[`optimize-images.py`](optimize-images.py), [`social-preview.py`](social-preview.py),
[`dashboard-logos.py`](dashboard-logos.py) — so none of them can drift from its source.
A screen recording cannot be, because a human has to drive the board.

This page is the substitute: the parameters that decide whether the recording is usable
(`?kiosk`, `refresh=5s`, compose rather than kind, the encode flags) are written down, so
re-recording after a board change is one pass rather than a rediscovery of the same two
traps. **If a change alters what the board looks like — a panel added or removed, but equally a query, threshold, title, layout or refresh interval — re-record it** — a demo of a board that no
longer exists is worse than a still of one that does.
