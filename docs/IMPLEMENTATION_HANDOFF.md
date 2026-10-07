> **Local patch notice:** This delivery is 1.2.0-rc1-renderbind.1. The inherited workflow below describes the 1.2.0-rc1 base. Use the current README, docs/RENDER_BINDING_FIX.md and reports/PATCH_VALIDATION.md for patch identity and validation; predecessor results are historical.

# Audio Instruments 1.2.0-rc1 - implementation handoff

## Current identity and scope

This package extends AV_Evidence_Toolkit_1.1.0-rc2-retiming.1.zip locally.
README.md is the current entrypoint. AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md governs the
new additive behavior. This is not AVPW Hybrid Build Spec v0.3.x and does not
supersede it; the portable tools can supply its deterministic instrument plane.
No GitHub repository was edited and no canonical analytical document was promoted.

## Completed software

- avevidence/audio_tracks.py: native source-time acoustic tracks, multiresolution
  FFT evidence, log-mel overview, source-safe stereo, EBU history, onset/tempo
  candidates, elementary STFT chroma and result lineage.
- avevidence/audio_render.py: bounded Pillow plots and verified no-decode rerender.
- cli.py: audio-track and render-audio; old commands preserved.
- pyproject.toml: optional instruments extra (NumPy), no GPU/ML dependency.
- tests/test_audio_tracks.py: new numerical and temporal regressions.
- scripts/audio_smoke_test.py: synthetic 24-second stereo trial for either host.

Existing audio.py, common.py, mapping.py, external.py, review validators and frame
extraction code were not changed. The old decoder remains the canonical PCM path.
New tracks are not automatically admitted into the legacy cue-only timeline API.

## Validation boundaries

Use reports/AUDIO_REVISION_VALIDATION.md and the actual source-bound reports.
Synthetic success does not qualify voice quality, beat perception, dialogue
attribution, subjective music description, or GBC narrative analysis. Native
Windows execution and the actual Girls Band Cry E11 performance need new tests.
Old retiming-patch Windows PASS reports apply to those older bytes only and are
retained in the input ZIP rather than bundled as current test results here.

## Intended real-media trial

The user selected Girls Band Cry Episode 11's performance as BENCHMARK-001, with
an established analytical baseline. Obtain the authorized media and identify the
actual source streams and a 20-30 second sentinel interval. No exact source path,
hash or timestamps for that media are established by this package.

Freeze independent observations before consulting the old analysis. Keep that
historical baseline as a benchmark reference, not unquestioned current authority.
The portable instrumentation alone is the B condition, not an audio-language or
native AV observer. A voiced-sounding pitch peak in a full mix is not Nina's F0.
GBC media and baseline are not embedded. Do not fetch or invent them automatically.

## Immediate next steps

1. Run the current self_test.py and audio_smoke_test.py on the Windows L40S host.
   Use an isolated Python environment; this package does not need CUDA or drivers.
2. Verify source identities/clocks on the actual sentinel. Inspect plots and
   numerical tracks; retain original audio for listening and model comparisons.
3. Check whether onset, spectral and stereo changes genuinely help focus inspection.
   Assess analytical gain independently of engineering completion.
4. Only then add optional CQT/HPSS, voice-quality methods, VAD or external adapter
   contracts; do not smuggle inferred emotion/instrument identity into DSP outputs.
5. A future join operation should bind source hash, selected stream and source
   clock and link event-support intervals to frames without making sync claims.

## Known implementation limits

- Whole input decode precedes bounded feature selection; use a verified audio
  clip run for repeated queries. f64 scratch can be much larger than compressed input.
- Spectral overviews are max-pooled. Use exact row/window/sample coordinates for
  measurements; images are not exact timing or energy integrals.
- Spectral onset and tempo detectors are deliberately basic; aliases and false
  detections on real music remain likely. No metrical/downbeat qualification.
- Full-mix measures are not isolated voice analysis. New voice-quality descriptors,
  source separation and true speech/music activity classifiers are not implemented.
- EBU running true peak is taken from rounded metadata, not a high-precision
  block-local true-peak track. Short-segment LRA needs skepticism.
- Reports retain paths and executable environment details; inspect before sharing.
- Exact legacy rationale beyond the supplied ZIP and conversation may need original
  sources. This handoff does not claim lossless preservation of the whole discussion.

## Instructions to the New Chat

Resume from this delivered source package, not the previous chat's general model
recommendations. Read README.md, the current audio spec and validation report.
Run the Windows regression/synthetic smoke before treating portability as tested.
Do not reinstall drivers or alter unrelated L40S workloads. Obtain the authorized
GBC source and freeze its binding before choosing timings or making AV claims.
Preserve the decoder, retiming defenses, source-clock maps, unknown-layout policy,
no-fabricated-review rule and distinction between instrument evidence and listening.
Report measured failures and propose narrow fixes; do not rewrite AVPW architecture.
