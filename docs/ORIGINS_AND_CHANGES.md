# Origins and changes

## Release identity

This source tree is **AV Evidence Toolkit 1.2.0-rc1-renderbind.1** (`1.2.0rc1+renderbind.1` in Python metadata), a local candidate patch of 1.2.0-rc1. The earlier release sections below describe history. Only validation explicitly bound to this patch applies to its changed bytes.

The patch binds re-render inputs before consumption, renders a verified private snapshot, and rejects input or manifest changes before publication. It also corrects this document's formerly contradictory release identity. See `RENDER_BINDING_FIX.md` for the patch boundary and regression coverage.

## Reference lineage

| Reference | ZIP SHA-256 | Contribution |
|---|---|---|
| `GKM_AV_TOOLCHAIN_V1_0.zip` | `1c9cb3def22632cdeeec14a9f5402f4c91e3230016affd97926e1deb478c95fb` | Source/materialization accounting, bounded equality, artifact integrity, and modality-specific review coverage. |
| `chatgpt_av_analysis_toolkit_v1.0.0.zip` | `0223fb40bdf46b62e1af69bbbf8908d192ee2fa192fb484b7aee8ced2acd5386` | Subtitle-oriented episode workflow, visual navigation, cue features, and evidence-class documentation. |
| `AV_Evidence_Toolkit_1.0.0_trial_patch.zip` | `7372e2b3931029f35fef3eeb6b8ed78a8925ef687bc2c43d6beb322dd36652f0` | Combined evidence architecture and mono/stereo float-WAV layout patch used as the direct code base for RC1. |

The retained GBC toolkit license appears under `LICENSES/GBC_TOOLKIT_MIT.txt`.

## 1.1.0-rc1 changes

- Fixed FFmpeg 7.1 clip mapping when decoded video frames omit duration fields.
- Replaced third-party signature-default serialization with an explicit JSON-stable pYIN parameter contract.
- Added immutable `frame-index` runs and verified `--frame-index-run` reuse.
- Added bounded input seeking with exact original-PTS verification and recorded full-decode fallback.
- Added practical, forensic, and verified-FLAC audio-storage profiles; bundles default to practical native-stream preservation.
- Added `admit-external-clip` for preexisting LosslessCut/stream-copy derivatives using encoded-packet SHA-256 subsequence mapping.
- Extended review validation to accept verified external audio/motion mappings.
- Restored agent-facing media-capability explanations, a copy-paste bootstrap prompt, and a GBC E11 example.
- Rebuilt release/version/report governance around one `1.1.0-rc1` identity.

## Deliberate limits

The toolkit does not supply hidden model audio perception, automatic speaker separation, emotion recognition, generalized HDR normalization, cloud upload, or automatic literary/canonical promotion. It prepares, measures, maps, and validates evidence declarations.

## 1.1.0-rc2 changes

Direct input: AV_Evidence_Toolkit_1.1.0-rc1.zip, SHA-256
`9d30c44ee2beac8086dd4621d817918de24d49e8470c5b189fd78b6a3b2dd959`.
Codex supplied independent findings in REVIEW(1).md; linked auxiliary files were
not attached, so failing-case analogues are newly constructed here.

- Exact stored-case release checks and canonical member/hash-set binding.
- Shared interval-union and quantized rate-one mapping contract.
- AAC decoded-playability/priming intersection while retaining packet provenance.
- Complete single-audio WAV byte copies and known-layout native f64 repair.
- Portable review inventory/derivative/parent-frame closure; immutable manifests.
- Localized estimated-tail qualification in review results and CLI status.
- Native release-build verification script and explicit outstanding Windows gate.
- Streaming test logs so a stalled subprocess is visible during validation.

## 1.2.0-rc1: deterministic audio instruments

Adds audio_tracks.py/audio_render.py and two CLI commands; native decoding,
retiming/clip admission and review validation modules are unchanged. New optional
`instruments` extra requires NumPy without librosa. Adds multi-resolution tracks,
STFT/mel PNG+NPZ, EBU history, stereo and limited musical candidates. This is a
local candidate, not upstream 1.2 stable. Prior validation remains in the input
ZIP, not under this revision's current reports. See AUDIO_TRACKS.md.
