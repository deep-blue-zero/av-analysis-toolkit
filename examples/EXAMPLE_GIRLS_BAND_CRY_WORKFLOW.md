# Example: fictional performance audit

This is a fictional workflow, with illustrative filenames, stream indices and
intervals. No episode, subtitle text, character reading or source-derived
conclusion is included. Replace these values with an authorized source and its
verified clocks. The historical filename is retained only because the inherited
release builder expects it; the example is not an analysis of a named series.

## 1. Probe and index once

```sh
ave probe "fictional-performance.mkv" "runs/performance-probe"
ave frame-index "fictional-performance.mkv" "runs/performance-frame-index" --video-stream 0
```

## 2. Build the ordinary evidence bundle

```sh
ave bundle "fictional-performance.mkv" "runs/performance-bundle" \
  --subtitles "fictional-performance.ja.ass" \
  --video-stream 0 \
  --audio-stream 1 \
  --interval 2 \
  --shots \
  --audio-storage practical
```

Practical storage preserves/remuxes a native FLAC stream. A compact AAC
derivative remains AAC and is not described as lossless.

## 3. Admit an externally cut clip

```sh
ave admit-external-clip "fictional-performance.mkv" "fictional-performance-clip.mkv" \
  "runs/performance-external-clip" \
  --parent-start 00:01:00 \
  --parent-end 00:03:00 \
  --parent-video-stream 0 --clip-video-stream 0 \
  --parent-audio-stream 1 --clip-audio-stream 1
```

Use the actual mapped interval in `external_clip.json`; requested boundaries do
not substitute for verified boundaries when packet expansion occurred.

## 4. Densify motion-sensitive sections

```sh
ave frames "fictional-performance.mkv" "runs/performance-dense" \
  --mode dense --start 00:01:00 --end 00:01:20 --fps 10 \
  --video-stream 0 --frame-index-run "runs/performance-frame-index"

ave frames "fictional-performance.mkv" "runs/performance-source-frames" \
  --mode source --start 00:02:00 --end 00:02:10 \
  --video-stream 0 --frame-index-run "runs/performance-frame-index"
```

Inspect actual images before recording gaze, posture, instrumental gesture,
camera movement or choreography. Extraction alone is not visual inspection.

## 5. Measure bounded audio questions

```sh
ave audio-metrics "fictional-performance.mkv" "runs/performance-spoken-metrics" \
  --audio-stream 1 --start 00:01:20 --end 00:02:00

ave audio-metrics "fictional-performance.mkv" "runs/performance-song-metrics" \
  --audio-stream 1 --start 00:02:00 --end 00:03:00
```

These describe the final mix. They do not isolate a singer or prove intention.

## 6. Analytical claim discipline

For a hypothetical event, keep each claim's actual support explicit:

- **Direct text:** a supplied, source-bound lyric contains a repeated phrase.
- **Direct visual sequence:** inspected source frames show a gaze transfer in a recorded interval.
- **Direct signal measurement:** a measured level/onset change occurs at a source time.
- **Audiovisual temporal inference:** an inspected gesture coincides with a source-bound musical event within a recorded tolerance.
- **Interpretation:** the event may stage a change in agency; test this hypothesis against alternatives using the preceding evidence together.

These are examples of claim types, not findings about any real performance.
The final mix alone does not establish studio production history or causal
claims about a particular quarrel, chord or arrangement.
