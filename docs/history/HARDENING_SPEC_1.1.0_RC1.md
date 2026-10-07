# AV Evidence Toolkit 1.1.0-rc1 Hardening Specification

**Document status:** implementation contract  
**Target package version:** `1.1.0rc1` (PEP 440)  
**Target release label/archive root:** `1.1.0-rc1`  
**Supersedes as an implementation target:** the unnumbered `1.0.0_trial_patch` package  
**Date:** 2026-09-10

## 1. Purpose

This release hardens the combined AV Evidence Toolkit into one coherent, reproducible release candidate suitable for transfer among ChatGPT chats, Codex agents, local shells, and other execution environments.

The release must preserve the toolkit's central epistemic boundary:

> Computation is not perception. Generated media is not reviewed media. Signal measurements, frame extraction, source-clock mappings, and packet identity can support audiovisual analysis without being mislabeled as direct human-like listening or continuous viewing.

The release must also make the useful workflow practical on long-form anime material rather than only on short synthetic fixtures.

## 2. Governing invariants

1. **One release identity.** Code, package metadata, documentation, validation reports, archive root, and checksums must identify the same release candidate.
2. **Source identity before interpretation.** Every mapping or derivative claim must bind to source SHA-256, absolute stream index, and an explicit clock.
3. **No invented timing.** Requested times, actual frame timestamps, packet boundaries, decoded-sample coverage, and inferred final-frame extents must remain distinguishable.
4. **No invented fidelity.** Stream copy, decoded-sample equality, packet identity, layout equality, and perceptual equivalence are separate predicates.
5. **No invented review.** Generated artifacts remain `NOT_REVIEWED` until a valid capability, presentation, and review declaration exists.
6. **Transactional outputs.** Existing outputs are never overwritten; failed work is not published as a successful run.
7. **Portable verification.** A clean extraction of the release archive must be able to run its regression suite and rebuild the same source archive bytes on the same software platform.

## 3. Required changes

### H1. FFmpeg 7.1-compatible clip-frame extent mapping

The `clip-av` mapping path must support FFmpeg/ffprobe 7.1 when decoded video-frame records omit `duration_time` and `pkt_duration_time`.

For each decoded video frame, extent resolution must use this priority:

1. a positive explicit decoded-frame duration;
2. the next strictly later decoded presentation timestamp;
3. the selected derivative stream's verified canonical endpoint for the final frame;
4. the immediately preceding positive frame interval as an explicitly marked estimate for the final frame.

Requirements:

- Non-increasing timestamps, unexplained overlap, or a non-positive derived extent must fail.
- The output must record counts of extent methods and any estimated final-frame extent.
- Adjacent windows may be merged only within the recorded timestamp tolerance.
- The mapping must not claim frame-exact requested endpoints when source frames cross those endpoints.

Acceptance:

- All existing `clip-av` integration tests pass under FFmpeg 7.1.5.
- A regression test directly exercises missing decoded-frame duration fields.

### H2. JSON-stable librosa/pYIN reporting

The toolkit must not serialize arbitrary third-party signature-default objects.

Requirements:

- pYIN receives an explicit, toolkit-owned parameter dictionary containing JSON-native values only.
- The report records the actual explicit parameters passed, plus the installed librosa version.
- Optional pitch failure remains a recorded result rather than corrupting the entire run.

Acceptance:

- The pitch test passes with librosa 0.11.0.
- `features.json` is strict JSON with `allow_nan=False` semantics.

### H3. Reusable frame-index cache and bounded seek extraction

The toolkit must include the actual long-form frame-index/cache and seek workflow rather than referring to an external local adapter.

Required interface:

```text
ave frame-index INPUT OUTPUT [--video-stream N]
ave frames INPUT OUTPUT ... [--frame-index-run RUN] [--seek-preroll SECONDS] [--no-input-seek]
```

Requirements:

- `frame-index` creates an immutable, verified run containing every admitted original frame PTS, source-relative timestamp, duration when available, geometry, source hash, stream index, and time base.
- `frames --frame-index-run` verifies the supplied run and refuses a source hash, stream index, geometry, or clock mismatch.
- Bounded `dense`, `source`, and `exact` extraction should seek near the earliest selected frame, select by original PTS rather than global decoded frame number, and verify that the exact expected PTS set was produced.
- If the bounded seek path cannot recover every selected PTS, it may retry once through the full-decode path. The final run must record whether seek or fallback was used.
- No implicit untracked cache in a user home directory is permitted.
- `bundle` creates one frame-index child run and reuses it for interval frames and optional shot work where applicable.

Acceptance:

- Frame-index reuse produces the same source PTS and image hashes as an uncached extraction.
- A late bounded request records input-seek use.
- A mismatched cache is refused.

### H4. Practical audio-storage controls

The default bundle must stop persisting a whole-episode float64 WAV unless forensic storage is explicitly requested.

Required storage profiles:

| Profile | Persistent artifact | Purpose |
|---|---|---|
| `practical` | verified stream-copy of the selected native audio stream | compact transfer and preservation of source codec bytes where container remux permits |
| `forensic` | native-rate/native-channel float64 WAV with exact decoded-sample verification | equality-sensitive and forensic analysis |
| `verified-flac` | FLAC accepted only when native f64 decoded samples/rate/channels remain exactly equal | compact exact derivative for representable sources; otherwise fail |

Requirements:

- `extract-audio` accepts `--storage-profile`; the existing `--format` interface remains as a documented compatibility alias.
- `bundle` accepts `--audio-storage` and defaults to `practical`.
- Reports distinguish source codec, artifact codec, packet-copy/remux status, decoded-sample equality, and source-lossless/source-lossy provenance.
- A lossy source copied into a compact native derivative must never be labeled restored lossless audio.
- Cue features and metrics must continue to work from any accepted extraction run.

Acceptance:

- A default bundle does not contain a float64 whole-episode WAV.
- A forensic bundle does.
- Existing exact-preservation tests remain valid.

### H5. Admission of preexisting LosslessCut/stream-copy clips

The toolkit must support a clip created outside the toolkit, especially a LosslessCut stream-copy derivative.

Required interface:

```text
ave admit-external-clip PARENT CLIP OUTPUT \
  --parent-start TIME --parent-end TIME \
  [--parent-video-stream N --clip-video-stream N] \
  [--parent-audio-stream N --clip-audio-stream N]
```

Requirements:

- The command probes and hashes both files.
- For each selected modality it fingerprints encoded packets with ffprobe data hashes and attempts to find the clip packet sequence as one unique contiguous subsequence of the parent stream.
- A unique packet-subsequence match yields a verified parent-to-derivative mapping using actual packet PTS and durations, not merely the user-entered interval.
- No match or multiple matches yields `INDETERMINATE`, not a false failure or false equivalence.
- Claimed interval, actual mapped packet interval, boundary deltas, codec metadata, keyframe status, and per-modality result must be recorded.
- Cross-modal offset consistency may be reported, but packet matching must not be mislabeled as complete audiovisual or perceptual equivalence.
- Verified mappings must be compatible with the existing review-record validator as derivative audio/motion evidence.

Acceptance:

- A synthetic `-c copy` AV clip is admitted with verified packet-subsequence mappings.
- A re-encoded or deliberately changed clip is `INDETERMINATE` or `DIFFERENT`, never `VERIFIED_PACKET_SUBSEQUENCE`.
- Review validation accepts a verified external-clip mapping for the appropriate modality.

### H6. Agent-facing explanatory and bootstrap documentation

Restore the human/agent handoff layer that explains what the execution environment can actually do.

Required documents:

- `docs/HOW_AGENTS_ANALYZE_MEDIA_WITH_THIS_TOOLKIT.md`
- `PROMPT_FOR_OTHER_AGENTS.md`
- `examples/EXAMPLE_GIRLS_BAND_CRY_WORKFLOW.md`

They must distinguish:

- direct source access;
- direct visual inspection after frame materialization;
- dense/source-frame motion reconstruction;
- direct signal measurement;
- synchronized audiovisual inference;
- interpretive inference;
- direct auditory perception when genuinely available;
- absence of a media-player interface versus absence of analytical capability.

### H7. Coherent release identity and reproducible release construction

Requirements:

- `pyproject.toml`, `avevidence.__version__`, README title, archive root, spec, and reports identify `1.1.0rc1` / `1.1.0-rc1` consistently.
- Stale base/trial reports are removed from the release root. Historical trial information may survive only as clearly labeled history, not current validation.
- `scripts/build_release.py` includes all current authoritative documentation and reports, including `.txt` or `.log` files referenced by a report.
- The builder refuses unlisted current-release report references and missing required files.
- Current reports bind to implementation/test hashes.
- The wheel smoke report must be generated from the same source bytes as the release candidate.

Acceptance:

- Building twice from unchanged source produces byte-identical ZIPs.
- A fresh extraction verifies all checksums.
- The extracted source runs the same regression suite successfully.
- The installed wheel reports `1.1.0rc1` and exposes `ave`.

### H8. Validation and compatibility matrix

The release must include current, release-bound reports for:

- full synthetic regression suite;
- clean-tree release build;
- deterministic rebuild comparison;
- clean extraction regression;
- wheel build/install/smoke;
- at least one real-media smoke test when an authorized local source is available.

The reports must state that:

- Linux/Python 3.13/FFmpeg 7.1.5 is directly tested in this build environment;
- Windows wrappers are included but native Windows execution is not claimed unless separately performed;
- no regression result establishes perceptual review.

## 4. Out of scope for this release candidate

- Automatic literary or character-claim promotion.
- Automatic uploads to Drive, GitHub, or other services.
- General HDR tone mapping or arbitrary display-matrix normalization.
- Automatic speaker diarization or emotion classification.
- Claiming that packet identity proves matching subjective experience.
- A hidden global cache outside explicit immutable runs.

## 5. Release deliverables

1. `HARDENING_SPEC_1.1.0_RC1.md` — this implementation contract.
2. Updated source tree implementing H1-H8.
3. Current regression and validation reports.
4. Deterministic source ZIP: `AV_Evidence_Toolkit_1.1.0-rc1.zip`.
5. Built wheel for local installation.
6. A concise implementation conformance report mapping every requirement to code, tests, and remaining limitations.

## 6. Completion rule

A requirement is complete only when:

- implementation exists;
- an automated test or explicit bounded validation covers it;
- user-facing documentation describes it;
- release reports bind the tested bytes to this release identity.

Any unmet item must be listed as a release-candidate limitation rather than silently treated as complete.
