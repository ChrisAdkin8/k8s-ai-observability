# Rebuilding `demo.gif`

`demo.gif` is the walkthrough embedded in the README's "Try it". This page is the recipe
that produces it, and the measurements the recipe's constants come from. It exists because
those constants are not guessable: every one of them was derived from the recording, and a
rebuild that re-guesses them produces a worse file.

## The source chain

| Stage | What | Where |
|---|---|---|
| master | macOS screen capture, 416s, 3456x2234, 74 MB | outside the repo, `*.mov` is gitignored |
| cut | 45.47s, 1728x1118, 15fps, `sha256:d2602d89…c6a669a` | outside the repo, `*.mp4` is gitignored |
| shipped | `demo.gif`, 1184x1040, 114 frames, 28.5s | tracked |

The master is a Retina capture, so 3456x2234 is 1728x1118 of logical screen at 2x. The cut
is the master with its dead air removed: a macOS capture only writes a frame when the
screen changes, so the gaps between frame timestamps *are* the inactive stretches, and
holds over 2s were capped at 1.5s. See the `[Unreleased]` entry in `CHANGELOG.md` for that
part.

⚠️ **Unverified:** neither source is in the repo, so this page is not runnable from a clean
clone. That is deliberate for the master, which git would keep forever, but it means a
rebuild depends on files only on the author's machine. Whether the 45s cut is small enough
to be worth tracking has never been decided.

## The constants, and where they come from

**Crop, terminal: `crop=1184:1040:0:60`.** Measured, not eyeballed. Dumping the recording
as 8-bit gray and counting pixels brighter than the terminal background per column and per
row shows text never leaves x<1184 or y 60..1100. The right third of the frame holds
nothing but the `]` the shell prompt parks at the far edge, and the top 60 rows are the
macOS menu bar.

**Crop, Grafana: `crop=1324:938:316:88`.** The last 2s are a Safari window on a desktop.
Column and row means across the window edge put it at exactly 316..1639 by 88..1025; the
wallpaper either side is bright noise that costs bytes and says nothing.

**Ramp: source 0..20.07s at 6x, then 1x.** Recovered rather than reinvented, by matching
per-row bright-pixel signatures between the shipped GIF's frames and the cut's. The
arithmetic confirms it: `20.07/6 + (45.47-20.07) = 28.75s`, the previous file's exact
duration. The install scroll is fast-forwarded and the `verify.sh` cascade runs at the
recording's own pace, because the checks are the point.

**Cut at 43.55s, resume at 43.70s.** The dropped 0.15s is a single white frame, the app
switch flashing. It was visible in the previous file.

## Why each lever is there

The whole point is legibility, and the thing that was destroying it was not the encoder.
The old file scaled the full 1728px-wide screen to 900px, rendering every glyph at 52% and
collapsing the strokes. Cropping to content means the terminal now ships at 1:1 with the
recording, with no scaling step at all.

| Lever | Measured effect |
|---|---|
| crop to content | the enabler: 1:1 text at an output only 32% wider than the old file |
| `hqdn3d=0:0:2:2` | verify segment 4.98 MB → 2.58 MB |
| `dither=none` | flat terminal colour needs none, and the dither dots read as blur |
| palette weighted 8x toward Grafana | fixes wrong series colours, costs ~0.4 MB |

**`hqdn3d` is not cosmetic, and it is temporal only.** The `verify.sh` cascade is
near-static, but h264 noise makes every frame differ pixel-for-pixel, and GIF's frame
differencing needs exact equality to skip a pixel. The file was paying full price for 90
frames of noise. Spatial denoise would soften the text, so both spatial terms are 0.

**The palette has to be weighted.** The Grafana shot is 7 frames of 114 and its series
colours are thin lines, so a frequency-weighted global palette spends nothing on them:
blue rendered grey and orange rendered pink. Repeating those frames 8x in the stream
`palettegen` sees buys them slots proportional to what they need rather than to how many
pixels they cover. A local palette per frame fixes the colour too, and costs 35.9 MB,
because a new palette every frame defeats frame differencing entirely.

## The build

```sh
ffmpeg -i "<the 45s cut>" -filter_complex \
"[0:v]hqdn3d=0:0:2:2,split=3[s1][s2][s3];
[s1]trim=0:20.07,setpts=(PTS-STARTPTS)/6[a0];
[s2]trim=20.07:43.55,setpts=PTS-STARTPTS[b0];
[a0][b0]concat=n=2:v=1:a=0,crop=1184:1040:0:60,fps=4[ab];
[s3]trim=43.70:99,setpts=PTS-STARTPTS,crop=1324:938:316:88,scale=1184:-2:flags=bicubic,pad=1184:1040:(ow-iw)/2:(oh-ih)/2:color=0x0f1116,fps=4[c];
[ab]split=2[ab_u][ab_p];[c]split=2[c_u][c_p];
[ab_u][c_u]concat=n=2:v=1:a=0[use];
[c_p]setpts=PTS*8,fps=4[c_rep];
[ab_p][c_rep]concat=n=2:v=1:a=0[palsrc];
[palsrc]palettegen=max_colors=256:stats_mode=full[p];
[use][p]paletteuse=dither=none" \
  -loop 0 demo.gif
```

To trade size for width, replace the terminal branch's `crop=1184:1040:0:60` with
`crop=1184:1040:0:60,scale=W:H:flags=bicubic` and set the Grafana branch's `scale`/`pad` to
match. Measured points on that curve: 1040x912 is 5.6 MB, 900x790 is 4.1 MB, and 820x720 is
3.3 MB, which is the size of the file this one replaced.

## How large a GIF GitHub will actually render

6.7 MB renders. That was tested rather than assumed, by pushing the candidate to a branch
and loading the README GitHub rendered from it.

The two limits usually cited do not apply to this file. **camo's 5 MB** `CAMO_LENGTH_LIMIT`
governs externally hosted images, and a repo-relative image is not one: GitHub rewrites it
to its own `/raw/` path, which the rendered HTML confirms, and tags it
`data-animated-image=""`, its animated-image player. **The 10 MB figure** is the upload cap
for files attached to issues, PRs and comments. Fetching the 6.7 MB file back from
`/raw/` returned all 6,664,925 bytes as `image/gif`, hash-identical to the local build.

⚠️ **Unverified:** where the ceiling actually is. No public README GIF above 2 MB turned up
to probe the top end against, so what is established is that 6.7 MB works and that the next
hard limits are git's own, a warning at 50 MB and rejection at 100 MB.
