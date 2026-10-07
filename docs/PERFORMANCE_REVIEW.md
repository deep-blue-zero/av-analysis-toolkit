# Performance review with synchronized video

This local extension joins verified `performance` runs to a project's recorded
questions. It prepares a worksheet; it does not fill in observations or settle
interpretations. It uses the installed local tools and makes no paid API calls.

## Prepare a review

Each row in a JSON configuration identifies a performance run, its exact source
hash, a requested interval, and the questions to review. Optional context,
frames, claim IDs and debt IDs preserve the route back to the analytical project.

```json
{
  "schema": "ave.performance-review.config.v1",
  "title": "Performance review",
  "reviews": [{
    "review_id": "EP01-01",
    "source_id": "EP01",
    "source_sha256": "COPY_THE_SOURCE_SHA256_FROM_PERFORMANCE_JSON",
    "run": "measurements/ep01",
    "start_s": 501,
    "end_s": 533,
    "audition_start_s": 499,
    "audition_end_s": 536.74,
    "questions": ["Describe the delivery with source times and uncertainty."],
    "debt_ids": ["PROJECT-D0001"]
  }]
}
```

Paths are relative to the configuration file unless absolute. Use a new output
directory for every build. The audition context can extend up to 30 seconds on
either side; observations remain bounded to the requested interval. Native
rate and channel order are retained. Each PCM16 audition is checked against
the selected native samples; any quantization is reported, never hidden.

```powershell
python -m avevidence performance-review review-config.json review
python -m avevidence verify review --verify-sources
python -m avevidence performance-review-serve review --port 8875
```

Open the printed local URL. The server binds only to this computer and supports
byte ranges for media seeking. A plain Python static server lacks the needed
range behavior in the tested environment. No notes are written through HTTP.

## Bind episode video

First probe the actual source and select the correct absolute stream indices.
This conservative route accepts zero-based H.264 yuv420p video with AAC audio.
It copies encoded packets into an MP4, omitting subtitle and attachment streams.
Every decoded soundtrack frame is compared with the witness's native sample
clock and exact sample count. Container duration is not used as an audio bound:
an English subtitle tail can extend beyond the selected soundtrack.
The clock comparison defaults to 1.1 ms. If a known witness has larger small
timestamp residuals, `--audio-clock-tolerance-ms` explicitly records a different
bound (at most 20 ms); it never changes samples or timestamps. This does not
relax the separate 1.1 ms encoded-packet timestamp check. Choose any different
bound from the recorded residuals of the actual source; do not copy a tolerance
from another study without checking it.
Earlier video bindings are checked at worksheet build time; use
`video_audio_clock_tolerance_ms` in their review rows to declare that bound.
It refuses changed packet content, timestamps outside the recorded 1.1 ms
timebase-rounding tolerance, or audio that differs from the measured witness
after PCM16 conversion. Unsupported sources fail rather than silently transcode.

```powershell
python -m avevidence performance-video episode.mkv measurements/ep01 video/ep01 --video-stream 0 --audio-stream 1
```

Add `"video_run": "video/ep01"` to the corresponding review row, then build the
review. The entire episode is available for context. Caption and graph selection
seek the audio and video to the same source time. Passage playback starts with
the audition context and pauses after its end. Aligned Japanese captions are
optional and use the existing caption clock; do not apply its offset again.

The builder checks all packet payloads and presentation/decode timestamps and
compares the decoded audio of the source video, MP4 and measured witness. These
checks preserve the source relationship; they do not adjudicate perceived
synchrony or caption correctness. PCM16 equality concerns the 16-bit witness,
not every floating-point AAC decoder bit. Same-volume video files may be hard
links to reduce storage; treat all verified run files as immutable. Copy the
whole review folder for portable playback. Revalidation of original sources
still requires those sources.

## Review and return notes

1. Listen before revealing caption text, images or measurements. Describe what
   is audible with exact times, and distinguish observations from inference.
2. Reveal the evidence and inspect candidate alignment. Mixed-signal pitch can
   follow music or another speaker; digital level is not vocal effort.
3. If useful, watch the synchronized video and note gestures, reaction shots,
   pauses and editing. Select the actual inspection mode in the worksheet.
4. Record the reviewer, actual inspected times, observation and uncertainty.
   Give an alternative explanation for an interpretation. Saving as a draft or
   playing media does not claim completed review.
5. Export the notes. Download the JSON or copy its visible text. Browser-local
   storage is a convenience; export for a durable copy. Importing notes into the
   browser replaces its notes for that exact bundle.

```powershell
python -m avevidence performance-review-import review returned-notes.json imported-review-01
```

The importer validates bundle identity, record IDs, bounded ordered intervals,
required attribution, uncertainty and alternative explanations. An AV declaration
requires a bound video. Outputs are explicitly **declared human observations**;
the toolkit cannot certify that a reviewer listened. Canonical adoption and debt
closure require a separate analytical judgment. The original project is never
edited by this command.

## Project integration boundary

Supply the project's questions, source-bound captions and claim dependencies in
its own review configuration. Keep title-specific observations, interpretations
and large evidence runs in the project's evidence plane. The software repository
provides the generic worksheet and import contract. Comparing subtitle witnesses
with aligned derivatives does not establish audible wording or speaker identity;
new review observations start empty. Source media and private study results are
not included in this public source baseline.
