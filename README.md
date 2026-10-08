# AV Evidence Toolkit 1.6.0-alpha2-music-identity

Canonical software source: [deep-blue-zero/av-analysis-toolkit](https://github.com/deep-blue-zero/av-analysis-toolkit).
Git contains source, tests, schemas and generic workflows. Media, analytical
conclusions, credentials, model weights and installed binaries stay outside it.
The original `1.5.0a2` behavior is preserved by tag `v1.5.0a2-git-baseline`;
the migration and its platform-specific results are recorded in
[the migration manifest](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md).

This prerelease connects text, sound, performance, motion and visual structure
through one bounded dramatic event: **separate provenance; integrated interpretation**.
Scene packets reference hashed evidence; observation dependencies localize errors;
reconciliation records competence, authority and contextual dependence; claim
deltas compare the reading against a frozen baseline. A reasoning analyst still
performs the interpretation and records the actual review.

It adds six auditory task profiles, immutable context-minimized/contextual passes,
scoped human judgments and formal performance sections. Hosted execution remains
opt-in, authorized, capability-gated and budgeted. Current task capabilities remain
unqualified; historical failed probes remain failed. Planning and normal CI cost $0.
Temporal inspection offers 2/8/12 fps and bounded complete-frame windows, retaining
original source PTS. Generated frames remain OPEN until an attributed review is
validated; sparse samples do not confirm exact contact or continuous motion.

**Source-bound music identification** adds local metadata/reuse, optional
Chromaprint, authorized fingerprint-only AcoustID lookup, cached MusicBrainz
work relations and conservative cover families. Composition, arrangement,
performance, phrase and recording identity stay separate. `MID` scene evidence
requires scoped adjudication; a lookup/nonmatch alone cannot establish musical
truth or originality. See [the music workflow](docs/MUSIC_IDENTITY.md) for CLI,
privacy, portable replay and the proposed real-media validation.

```text
python -m pip install ".[all,reuse-parquet,dev]"
ave doctor
ave music identify source.mkv music-run --audio-stream 1 --start 12 --end 42
ave scene validate event.json
ave scene prepare event.json runs/event
ave scene reconcile event.json runs/reconciliation
ave scene plan event.json runs/queries
ave scene delta before.json after.json runs/delta --decisions decisions.json
```

See [the holistic workflow](docs/HOLISTIC_CROSS_MODAL_ANALYSIS.md),
[auditory profiles](docs/AUDITORY_TASK_PROFILES.md),
[temporal inspection](docs/TEMPORAL_INSPECTION.md),
[compatibility notes](docs/BACKWARD_COMPATIBILITY.md), and
[packaging/CI](docs/PACKAGING_AND_CI.md). Portable offline ZIPs are build artifacts,
not canonical source. FFmpeg and Python are external dependencies; no virtual
machine is needed. The inherited component guides below describe earlier layers.

The inherited method-precedence patch preserves an explicitly supplied analytical method byte-for-byte in
`deep-read` and `blind-export`, records matching source/copy hashes, and keeps
method selection independent of the episode-isolated semantic evidence boundary.
The embedded method is a fallback only; invalid explicit paths fail early.

This iteration adds modality/capability preflight, atomic claim validation,
bounded cached-contour queries, contrastive measurements, pitch quality gates,
native-quality listening witnesses, temporal inspection packets, attributed
observer contracts, musical-relation records, and independent ablation packets.
Unknown-duration recovery and subtitle normalization are explicit opt-ins.
See [the claim-directed workflow](docs/CLAIM_DIRECTED_ANALYSIS.md) and its
[task-by-task scope](docs/CLAIM_DIRECTED_CONFORMANCE.md).

The high-level `deep-read` command prepares evidence for a reasoning analyst.
It does not itself generate a literary interpretation. Paid calls require the
explicit hosted execution route and all its authorization/budget gates.
Mock observers never establish hearing. Advanced auditory inference, validated
CPP, automatic semantic visual tracking and alternate-aligner comparisons remain
explicitly unvalidated rather than represented by placeholder successes.

The preceding revision added local recording-reuse detection, fragment correspondence,
separate performance/media-edit identities, reversible decisions, and baseline
contributions selected separately for pitch, level, timbre and timing. It handles
transcript-free reactions and imports verified performance runs. See the
[reuse guide](docs/PERFORMANCE_REUSE.md) and
[migration validation record](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md).

```powershell
.\.venv\Scripts\python.exe -m avevidence reuse scan examples/reuse-synthetic.json runs/reuse-synthetic --cache-dir cache/reuse --database reuse.sqlite
.\.venv\Scripts\python.exe -m avevidence reuse audit reuse.sqlite runs/reuse-audit
.\.venv\Scripts\python.exe -m avevidence performance-search evidence.sqlite "人間" --reuse-database reuse.sqlite
```

Repetition is evidence about media use. A shared recording contributes once to
the relevant independent-performance baseline; every occurrence remains available.
Scores are not confidence probabilities, and uncertain matches are not silently merged.
The reuse example contains original synthetic non-speech signals: generate its local
input using the recipe in the reuse guide before running the example. It demonstrates
recording correspondence and does not establish an acted-performance baseline.

The preceding revision added source-linked performance worksheets, synchronized episode
video, Japanese caption playback, and validated return of human review notes.
See the [performance review guide](docs/PERFORMANCE_REVIEW.md) and
[migration validation record](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md).

This extension of 1.2.0-rc1-renderbind.1 adds local Japanese forced alignment,
reusable pitch and digital-level contours, and a searchable SQLite evidence index.
No paid API or hosted inference is used. See the [local performance guide](docs/LOCAL_PERFORMANCE.md)
and the [historical validation scope recorded by the migration](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md).

```powershell
.\.venv\Scripts\python.exe -m avevidence performance "episode.mkv" "runs/episode-01" --cache-dir cache --audio-stream 1 --subtitle-stream 10 --model-dir models/Qwen3-ForcedAligner-0.6B
.\.venv\Scripts\python.exe -m avevidence performance-index "runs/episode-01" "evidence.sqlite"
.\.venv\Scripts\python.exe -m avevidence performance-search "evidence.sqlite" "先輩"
```

Select the actual Japanese audio and subtitle stream numbers using `ave probe`.
The command emits an offline `performance.html` browser plus machine-readable results.
Omit `--model-dir` to compute contours without installing a language model.
The [earlier render binding fix](docs/RENDER_BINDING_FIX.md) remains included.

A portable Python command-line toolkit for preparing, measuring, locating, mapping, and auditing audiovisual evidence. It combines source identity, original presentation timestamps, reusable frame indexes, bounded seek extraction, practical audio-storage profiles, external LosslessCut/stream-copy admission, and strict declared-review accounting.

**Computed measurements are not direct listening. Generated frames are not inspected frames. Packet identity is not perceptual equivalence.** The toolkit records those boundaries while still enabling substantial audiovisual work in execution environments that have FFmpeg, Python, and an image-review interface but no conventional media player.

## Start here: Audio Instruments revision

The inherited audio instruments came from **1.2.0-rc1-renderbind.1**, based on 1.2.0-rc1 and its supplied 1.1.0-rc2-retiming.1 predecessor.
It adds deterministic audio tracks, music candidates, and acoustic evidence images.
The canonical decoder, retiming defenses, old commands, and review gates remain.
The current extension contract is [Audio Analysis Spec](AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md).
For first use, read [Audio quickstart](docs/AUDIO_QUICKSTART.md), then
[methods and limitations](docs/AUDIO_TRACKS.md). Current-byte test scope is in
[migration record](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md); old reports are not inherited.

```powershell
# From the extracted toolkit directory; a fresh virtual environment is recommended.
python -m pip install ".[instruments]"
python -m avevidence audio-track "performance.mkv" "runs/audio-01" --profile music
python -m avevidence verify "runs/audio-01"
```

`instruments` adds only NumPy to the existing Pillow core. The broader `audio`
extra still supplies librosa/SoundFile for legacy optional functions. FFmpeg and
ffprobe must be installed separately. No CUDA, PyTorch, models, or cloud account
are needed for these additions. Audio files or verified extract-audio/clip-audio
run directories are accepted. Explicit stream indices are required when ambiguous.

## Inherited local retiming fix

This local patch rejects material packet-timing drift before duration normalization
and rejects already-generated fragmented rate-one mappings that hide retiming.
See [patch notes](docs/RETIMING_FIX.md) for behavior, validation and packaging scope.
Its safeguards remain in this release candidate. Upstream RC2 or retiming-patch
reports are not evidence for these modified bytes. Baseline migration checks and
known non-equivalence are documented in `docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md`.

## RC2 contract repairs

RC2 fixes release-member binding and Windows filename handling; requires union
coverage at external-clip admission; separates AAC priming packets from reviewable
samples; byte-copies suitable complete WAV sources; packages portable review
dependency closure; and qualifies estimated final-frame extents. See
[Mapping and portability contract](docs/RC2_MAPPING_AND_PORTABILITY.md) and
[Release validation workflow](docs/RC2_RELEASE_VALIDATION_WORKFLOW.md).

This retains the RC2 features with a targeted retiming correction. Validation
claims apply only to the version and implementation hashes named in each report.

## Install and verify

Requirements:

- Python 3.10+
- FFmpeg and ffprobe 7.1+
- Pillow
- optional audio features: NumPy, SoundFile, librosa

No media, model weights, FFmpeg binaries, fonts, cloud credentials, or upload logic are bundled.

```sh
python -m pip install ".[audio]"
ave doctor
python scripts/self_test.py
```

Without installation, substitute `python avtool.py` or `python -m avevidence` for `ave`.

## Baseline bundle

```sh
ave bundle "episode.mkv" "runs/episode-01" \
  --subtitles "episode.ja.ass" \
  --interval 2 \
  --shots \
  --cue-features \
  --audio-storage practical
```

The default bundle `practical` audio profile preserves/remuxes the selected native audio stream instead of persisting a whole-episode float64 WAV. Use `--audio-storage forensic` only when equality-sensitive persistent float64 PCM is justified. Use `verified-flac` only when exact decoded-sample preservation succeeds.

When a media type has multiple streams, choose the intended **absolute ffprobe stream index**, such as `--audio-stream 1 --video-stream 0`. Ambiguity is refused.

Every output is a **new immutable run directory**. Existing outputs, input/output collisions, stale artifacts, and incomplete staging directories are refused as current evidence.

## Reusable frame index and bounded seek

Create one complete original-PTS frame index:

```sh
ave frame-index "episode.mkv" "runs/episode-frame-index"
```

Reuse it for late or dense questions:

```sh
ave frames "episode.mkv" "runs/gesture-01" \
  --mode source \
  --start 00:18:40 \
  --end 00:19:05 \
  --frame-index-run "runs/episode-frame-index" \
  --seek-preroll 2
```

The extraction selects by **original integer PTS**, seeks near the requested region where possible, verifies the exact expected PTS set, and records whether bounded seek or full-decode fallback was used. No hidden global cache is created.

## External LosslessCut or stream-copy clips

```sh
ave admit-external-clip "episode.mkv" "concert-losslesscut.mkv" "runs/concert-admission" \
  --parent-start 00:18:40 \
  --parent-end 00:23:20 \
  --parent-video-stream 0 \
  --clip-video-stream 0 \
  --parent-audio-stream 1 \
  --clip-audio-stream 1
```

The command copies the exact clip bytes into the run, hashes both files, fingerprints selected packet payloads, and looks for one unique contiguous packet subsequence in the parent. Packet identity alone does not establish review readiness. A verified mapping also requires playable coverage and the shared admission/review timing contract. No match or an ambiguous match remains `INDETERMINATE`; re-encoding is never mislabeled as a stream copy. A packet-identical clip whose actual packet coverage does not include the user-declared parent interval is recorded as `PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED` and does not receive a successful CLI exit status.

## Audio preparation

```sh
ave extract-audio "episode.mkv" "runs/audio-practical" --storage-profile practical
ave extract-audio "episode.mkv" "runs/audio-forensic" --storage-profile forensic
ave extract-audio "episode.mkv" "runs/audio-flac" --storage-profile verified-flac
```

Profiles:

- `practical`: selected native codec copied/remuxed, or a suitable complete single-audio WAV copied byte-for-byte; a lossy source remains lossy.
- `forensic`: native-rate/native-channel float64 WAV, accepted only after exact decoded-sample verification.
- `verified-flac`: FLAC accepted only after exact f64 decoded-sample/rate/channel verification.

The legacy aliases remain available: `--format preserve`, `--format wav`, and `--format flac`.

## Review accounting

```sh
ave review-templates "review-records"
ave review-check "review-records/reviews.json" \
  "runs/episode-01/inventory" \
  "review-records/capabilities.json" \
  "runs/review-check-01"
```

The validator checks declarations, source and artifact hashes, selected streams,
modalities, presentation receipts, and source/derivative mappings. It cannot prove
perception or interpretation. Output includes hash-verified inventory and derivative
run closure with rebased declaration references for relocation. Estimated frame tails
produce qualified coverage and a nonzero review-check status, not an unqualified
exact-extent result.

## Share and verify

```sh
ave verify "runs/episode-01"
ave pack "runs/episode-01" "episode-evidence.zip"
ave verify-archive "episode-evidence.zip"
```

## Documentation

- [Current audio extension contract](AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md)
- [Inherited hardening contract](HARDENING_SPEC_1.1.0_RC2.md)
- [Command reference](docs/COMMANDS.md)
- [Methods and limits](docs/METHODS.md)
- [Review records](docs/REVIEW_RECORDS.md)
- [How agents analyze media with this toolkit](docs/HOW_AGENTS_ANALYZE_MEDIA_WITH_THIS_TOOLKIT.md)
- [Copy-paste agent bootstrap prompt](PROMPT_FOR_OTHER_AGENTS.md)
- [Fictional performance example workflow](examples/EXAMPLE_GIRLS_BAND_CRY_WORKFLOW.md)
- [Origins and changes](docs/ORIGINS_AND_CHANGES.md)
- [Migration and validation record](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md)
- [Public source safety](docs/PUBLIC_SOURCE_SAFETY.md)

This source baseline preserves the supplied release identity. Consult the migration
record for executed checks and their exact scope. Historical archive reports remain
outside the public source tree; predecessor results do not certify changed bytes.
