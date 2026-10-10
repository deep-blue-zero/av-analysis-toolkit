# Command reference

Run `ave COMMAND --help` for exact flags. `python avtool.py` and `python -m avevidence` are equivalent entry points from the source directory.

## Analytical method and semantic evidence boundary

`ave deep-read INPUT OUTPUT --method PATH --episode-isolated` preserves the
explicit UTF-8 method byte-for-byte. `--method` chooses how to analyze;
`--episode-isolated` independently limits evidence to that source. Missing or
unreadable explicit methods fail early. Without `--method`, the embedded
abbreviated method is a fallback only.

`ave blind-export CONFIG OUTPUT --method PATH` now accepts the same explicit
method. Omitting it retains the fallback behavior. Both commands record
`method-receipt.json`, source/copy SHA-256, selection and semantic scope.
See [the current workflow](CLAIM_DIRECTED_ANALYSIS.md).

Version 1.3 adds `performance`, `performance-index` and `performance-search`.
See [local performance](LOCAL_PERFORMANCE.md) for Japanese alignment, reusable
contours, transcript interchange, cache/storage limits and search examples.

The review extension adds `performance-review`, `performance-video`,
`performance-review-serve` and `performance-review-import`. See the
[performance review guide](PERFORMANCE_REVIEW.md) for configurations, synchronized
playback and the boundary between prepared evidence and declared observations.

## Conventions

- Outputs must be new paths. Existing outputs and input/output nesting are refused.
- Times accept nonnegative seconds, `MM:SS`, or `HH:MM:SS`, including fractions.
- Stream arguments are absolute ffprobe indices.
- Canonical source time is original presentation timestamp minus the recorded source origin.
- Successful preparation writes `run.json`, `commands.json`, hashes, parameters, environment identity, and operation-specific results.
- Generated artifacts remain `GENERATED_NOT_REVIEWED`.

## Environment and identity

```sh
ave doctor
ave probe INPUT OUTPUT
ave inventory CONFIG OUTPUT
ave verify RUN [--verify-sources]
```

`doctor` reports software availability, implementation hashes, and unverified perceptual-capability status. `probe` records source SHA-256, streams, clock origin, duration basis, and uncertainty.

## Frame index, extraction, and navigation

```sh
ave frame-index INPUT OUTPUT [--video-stream N]
ave frames INPUT OUTPUT [options]
ave contacts FRAME_RUN OUTPUT [--columns 4 --rows 4 --thumb-width 360]
```

`frames` modes:

| Mode | Main inputs | Selection |
|---|---|---|
| `interval` | optional start/end, `--interval` | first source frame at or after each regular request |
| `dense` | required start/end, `--fps` | dense request grid while retaining actual PTS |
| `source` | required start/end | every source frame in a bounded interval |
| `exact` | `--timestamps ...` | first source frame at or after each request |
| `shots` | optional start/end, `--threshold` | scene-change candidates at original PTS |

Cache/seek options:

```text
--frame-index-run RUN
--seek-preroll SECONDS
--no-input-seek
```

A supplied frame-index run is hash/stream/geometry/clock verified. Bounded extraction seeks near the earliest selected frame, selects by original integer PTS, and falls back once to full decode if exact PTS recovery fails. The run records the path actually used.

## Subtitles and aligned timeline

```sh
ave subtitles INPUT OUTPUT [--media --subtitle-stream N] [--source-media MEDIA] [--offset S]
ave timeline SUBTITLE_RUN FRAME_RUN OUTPUT [--audio-run FEATURE_RUN] [--max-distance S]
```

ASS/SRT parsing is strict. External subtitles can be bound to source media. Embedded streams require explicit selection. Timeline rows preserve actual frame time and distance from cue boundaries.

## Audio

```sh
ave extract-audio INPUT OUTPUT [--audio-stream N] [--storage-profile PROFILE]
ave audio-metrics INPUT OUTPUT [--audio-stream N] [--start T --end T]
ave cue-features AUDIO_OR_RUN DIALOGUE_CSV OUTPUT [--pitch --confirm-isolated-speech]
ave compare-audio A B OUTPUT [--a-stream N --b-stream N]
ave clip-audio INPUT OUTPUT --start T --end T [--pad S]
```

Storage profiles:

| Profile | Artifact | Meaning |
|---|---|---|
| `practical` | copied/remuxed selected native stream | compact; source codec fidelity retained; lossy remains lossy |
| `forensic` | float64 WAV | equality-sensitive persistent PCM |
| `verified-flac` | FLAC | accepted only after exact decoded-sample preservation |

Legacy aliases: `--format preserve|wav|flac`.

Metrics and features describe the final mixed signal. They do not establish speaker identity, emotion, or direct listening. Pitch requires an operator declaration that the window is isolated speech; the declaration itself is not verified separation.

## Review derivatives

```sh
ave clip-av INPUT OUTPUT --start T --end T [--pad S]
ave admit-external-clip PARENT CLIP OUTPUT --parent-start T --parent-end T [stream options]
```

`clip-av` creates FFV1 + float64 PCM review media and derives frame/audio extents using FFmpeg-7.1-compatible timestamp rules.

`admit-external-clip` verifies existing stream-copy clips by unique encoded-packet SHA-256 subsequence. It records actual packet-bound parent/derivative intervals, keyframe boundary expansion, codec identity, ambiguity, and cross-modal offset consistency. Success also requires the actual mapping to cover the declared parent interval; packet identity with a wrong claimed interval is retained but does not pass. Packet matching is not complete AV or perceptual equivalence.

## Bundle

```sh
ave bundle INPUT OUTPUT [--subtitles FILE | --subtitle-stream N] \
  [--audio-storage practical|forensic|verified-flac] \
  [--interval S --width PX --shots --cue-features]
```

The bundle creates an inventory, practical or forensic audio derivative, metrics, one reusable frame-index run, interval frames, contact sheets, optional shots, subtitles/features, and a joined navigation timeline.

## Review declarations

```sh
ave review-templates OUTPUT
ave review-check REVIEWS INVENTORY_OR_RUN CAPABILITIES OUTPUT
```

Templates are invalid placeholders until completed after actual presentation/review. Validation checks structure and declared coverage but does not prove perceptual truth.

## Packaging

```sh
ave pack RUN ARCHIVE.zip
ave verify-archive ARCHIVE.zip
```

Only verified manifest-owned artifacts are packed. Archives use deterministic ordering, timestamps, and SHA-256 membership.


## RC2 status and portability additions

See [RC2 mapping contract](RC2_MAPPING_AND_PORTABILITY.md). External admission exit
0 now requires review-ready continuous union coverage with no estimated tail;
packet identity with gaps/uncertain timing returns exit 3. Review-check likewise
returns 3 for an estimated-tail-qualified full declaration. Both still publish
qualified diagnostic output. No status asserts actual perception.

`python scripts/build_release.py DESTINATION [--candidate]` builds a fresh source
archive outside the source tree. Verified mode requires exact current report/source
bindings. Candidate mode is explicitly unvalidated. `scripts/verify_release.py`
and `scripts/verify_release_windows.ps1` exercise two builds, clean tests, and
clean-tree reconstruction; see the [release workflow](RC2_RELEASE_VALIDATION_WORKFLOW.md).

## Audio Instruments 1.2.0-rc1

`audio-track INPUT OUTPUT [--audio-stream INDEX] [--start TIME --end TIME]`
`[--profile general|speech|music] [--window-ms N] [--hop-ms N]`
`[--spectral-scales-ms N ...] [--stereo auto|off|channels-0-1]`
`[--loudness auto|require|off] [--no-render] [--energy-threshold-dbfs -45]`
`[--max-duration 300] [--max-frames 250000]`

`render-audio TRACK_RUN OUTPUT` validates input hashes before rendering.

See [quickstart](AUDIO_QUICKSTART.md), [methods](AUDIO_TRACKS.md) and the current
[extension contract](../AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md). These commands do not
establish listening and do not automatically feed the legacy cue-only timeline.

## Layered auditory admission (1.6.0a3)

`ave observer provider-witness FORENSIC_RUN OUTPUT` creates the bounded, verified
PCM16 derivative. `fixtures --protocol v2 --trials 24` prepares locally generated
influence tests. `benchmark CONFIG OUTPUT` and `qualification RUN` recompute
task-specific benchmark evidence without sending audio. `observe` accepts
`--observation-lane EXPERIMENTAL` or the conservative `QUALIFIED` default, with
`--qualification RUN --media-category CATEGORY` for routine execution. Hosted
media permission, credentials and budgets remain required. See
[AUDITORY_QUALIFICATION.md](AUDITORY_QUALIFICATION.md) for complete commands and
[Schemas](AUDITORY_SCHEMAS.md) for atomic review/proof contracts.
