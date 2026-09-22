# Presspeech demo video

Synthetic Mac interaction demo for sharing the Presspeech workflow without
screen-recording the real app. The animation is described declaratively
in `index.html` (a single deterministic timeline driven by
`window.renderAt(t)`); `render.mjs` walks that timeline frame-by-frame
through headless Chrome and hands the PNG sequence to ffmpeg.

Output: `dist/presspeech-demo.mp4` (also `.webm`, `.gif`).

## Render

Prerequisites: Node 20+, Google Chrome, and ffmpeg. The defaults are the
standard macOS locations; set `PRESSPEECH_CHROME` and `PRESSPEECH_FFMPEG` to
their executable paths elsewhere.

```
cd marketing/demo
npm install
node render.mjs
```

The script:

1. Launches headless Chrome (no separate Chromium download — uses the
   system Chrome via `puppeteer-core`).
2. Opens `index.html` and waits for `window.demoReady`.
3. For each of the 465 frames (30 fps × 15.5 s), calls
   `window.renderAt(t)` to set deterministic state and screenshots the
   1920×1080 viewport into `frames/`.
4. Encodes the frame sequence to
   - `dist/presspeech-demo.mp4`  (H.264, yuv420p, CRF 18, +faststart)
   - `dist/presspeech-demo.webm` (VP9, CRF 32)
   - `dist/presspeech-demo.gif`  (1080 px wide, 20 fps, palette-quantised)
5. Copies MP4 and WebM into `docs/`, and creates `docs/demo-poster.jpg` from
   the stable 12-second frame.
6. Run `python3 ../../scripts/check-public-assets.py --update-demo` to bind the
   checked-in outputs to their generator sources. CI rejects stale copies.

Total wall-time on an M-series Mac: ≈ 1 minute.

## Timeline

The demo keeps each beat visually local. A short title card establishes the
product, then the camera stays on the menu-bar icon, Right Option keycap, and
live speaking counter for the hold. It moves to TextEdit as the transcript
appears, then pulls wide for the closing evidence caption. The animation is
synthetic and does not pretend that its narration or keycap is application UI.

The closing subtitle keeps the measured claims scoped: the ~94 ms number is
model transcription on the documented fixture, not complete release-to-paste
latency. Idle-memory and CPU figures describe the released macOS build. The
exact download size stays in release-synchronised text rather than this
long-lived video.

| t (s)       | what happens                                                                  |
|------------:|-------------------------------------------------------------------------------|
| 0.0 – 0.3   | Title card fades in.                                                          |
| 0.3 – 1.7   | Title card holds.                                                             |
| 1.7 – 2.1   | Title fades to the menu-bar and Right Option close-up.                       |
| 2.7         | KEY DOWN. The keycap depresses and the real app icon's recording state is represented in red. |
| 2.7 – 7.7   | Live `Speaking` counter and restrained recording pulse.                       |
| 7.7         | KEY UP. The keycap and menu-bar icon return to idle.                           |
| 7.794       | The transcript appears 94 ms after release and the camera moves to TextEdit.    |
| 9.0 – 9.45  | The keycap narration fades while the camera pulls wide.                       |
| 9.5 – 10.02 | Closing caption and macOS evidence line fade in.                              |
| 10.02 – 15.5 | Hold final frame.                                                            |

The 94 ms gap, the live counter, and the timing of the text reveal are
all derived from the same `t` values, so what the viewer reads on the
chip matches what they just saw happen, to the millisecond.

## Editing

All visuals are HTML/CSS/SVG in `index.html`. The render is fully
deterministic — `renderAt(t)` computes every animated value from `t`,
so two runs produce byte-identical frames. To iterate quickly, open
`index.html` in a regular browser and call `renderAt(seconds)` in the
console.

## Output paths

- `marketing/demo/dist/presspeech-demo.mp4` — for Reddit and most social
- `marketing/demo/dist/presspeech-demo.webm` — for `<video>` embeds
- `marketing/demo/dist/presspeech-demo.gif`  — for places that disallow video

The docs site embeds synchronized copies as `docs/demo-video.{mp4,webm}` plus
`docs/demo-poster.jpg`.
