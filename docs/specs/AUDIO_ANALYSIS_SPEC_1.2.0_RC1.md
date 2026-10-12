> Current release: **1.6.0-alpha3-auditory-qualification**; see
> `docs/AUDITORY_QUALIFICATION.md` for layered admission and scoped proof rules.
> Inherited extension: **1.5.0-alpha2-method-precedence**. This file describes the
> inherited Audio Instruments contract. Reuse commands and their separate
> correspondence schema are in `docs/PERFORMANCE_REUSE.md`; local alignment and
> listening worksheets retain their existing guides. Current reuse validation
> is in `reports/PERFORMANCE_REUSE_VALIDATION.md`.
> The new analytical evidence contracts and their explicit scope are documented
> in `docs/CLAIM_DIRECTED_ANALYSIS.md` and `docs/CLAIM_DIRECTED_CONFORMANCE.md`.

# AV Evidence Toolkit 1.2.0-rc1-renderbind.1 - Audio Instruments contract

Local render-binding patch: `render-audio` consumes a private snapshot verified
against a manifest bound before rendering; original dependency identities are
checked again before publication. Numerical analysis algorithms are unchanged.

Identity: local implementation candidate, not upstream stable release.
Scope: deterministic temporal, spectral, spatial and elementary musical evidence.
Authority: this is the current extension contract; README.md is the package entrypoint.
Existing evidence/source/review invariants in docs/METHODS.md and the retained RC2
and retiming contracts continue unchanged. Historical specs do not select current
release identity. No AVPW worker, repository integration or canonical analysis is
implemented here.

## Required behavior

1. Keep canonical native-rate, native-channel float64 decoding and source-clock
   mapping. Do not normalize, resample, downmix or interpolate missing source time.
   Float64 is a computational reference, not recovered fidelity from a lossy codec.
2. Accept direct media and verified extract-audio/clip-audio run inputs. Preserve
   parent source/stream identities and mapping uncertainty, including delayed starts.
3. Analyze each selected contiguous PCM interval separately. Store half-open native
   sample supports and source start/end/center times. No frame, flux, chroma-change,
   loudness integration or tempo calculation may span a source gap.
4. Record every complete noncentered analysis frame. Default hop is 10 ms; actual
   integer sample sizes must be recorded. Incomplete spectral tails are reported,
   not padded. Waveform and summary statistics include final partial bins.
5. Export temporal RMS/peak/crest/DC/ZCR, magnitude centroid/bandwidth/85% rolloff,
   power flatness/entropy, normalized spectral flux and unnormalized onset strength.
   Units and definitions must be explicit. Digital zero dB and undefined ratios
   use null, not arbitrary floors or JSON NaN/Infinity.
6. Export max-pooled linear STFT and log-mel overviews in numerical NPZ and PNG
   forms. Pooling parameters/support must be recorded. Images do not replace the
   underlying waveform or full-resolution frame measurements.
7. Known stereo enables ch0/ch1 mid/side, balance, Pearson correlation and side
   energy fraction. Unknown two-channel layout requires explicit operator opt-in;
   a channel count alone must not imply a spatial interpretation. No layout guess
   may establish EBU channel weighting.
8. FFmpeg EBU R128 measurement is optional/required/off by explicit mode. Preserve
   raw metadata and logs. Bind each measurement to observed decoder/filter sample
   counts; report the END of each meter block. Momentary/short-term windows require
   400 ms/3 s history; integrated/LRA state resets per selected contiguous segment.
   Cumulative true peak must not be mislabeled per-frame true peak. LRA from a
   short excerpt carries no confidence claim. Unsupported filters are explicit.
9. Music profile adds a documented STFT pitch-class fold, chroma changes, spectral
   onset candidates and up to three tempo-period hypotheses. It does not identify
   instruments, notes, downbeats, chords, voices, emotions or expressive intention.
10. Every run is an immutable, verified, source-bound artifact set. Store settings,
    implementation/library/FFmpeg identities and hashes. Keep synthetic tests
    separate from real media and perceptual review. Retain existing archive checks.
11. Default resource caps are 300 s analyzed source span and 250,000 total spectral
    frame rows. Decode currently scans the full selected source into scratch f64;
    a disk preflight guard is an estimate, not a quota. Document that limitation.
12. Old CLI behavior remains compatible. A successful instrument run is not a
    perceptual success gate or proof of improved narrative analysis.

## Initial implementation boundary

Implemented: audio-track, render-audio, independent channels, multiple STFT scales,
log-mel overview, source-timed plots, EBU history, stereo, elementary music,
provenance, validation and portable synthetic smoke script.
Deferred: CQT/HPSS, robust beat/downbeat tracking, voice-quality/formant/CPP/HNR
analysis, speech/music activity classifiers, neural VAD, separation and audio/AV
language models. No automatic join of new tracks into legacy cue-only timeline.
No claim of actual GBC E11 testing without user-authorized media.

## Qualification

Use synthetic numerical reference tests, discontinuous/delayed media tests,
existing retiming/portability regressions, wheel smoke, archive verification and
clean extraction. Native Windows and real performance tests remain separate gates.
Build with `scripts/build_release.py --candidate` until ALL stable-release report
requirements pass; this revision does not weaken that inherited release gate.
# Previous extension: 1.6.0-alpha1-holistic-cross-modal

The original instrument specification below remains historical component scope.
Current event contracts, contextual auditory passes and temporal escalation are
documented in `docs/HOLISTIC_CROSS_MODAL_ANALYSIS.md`,
`docs/AUDITORY_TASK_PROFILES.md` and `docs/TEMPORAL_INSPECTION.md`.

# Current extension: 1.6.0-alpha2-music-identity

Source-bound music identity adds local metadata/reuse, optional external
Chromaprint, explicitly authorized AcoustID/MusicBrainz, conservative work families,
separate identity-level adjudication and MID scene evidence. Its contracts and
privacy are documented in `docs/MUSIC_IDENTITY.md`. Historical specifications,
receipts and the migration baseline are preserved. Generated/mocked tests cannot
qualify real-song recognition or auditory perception; real-media trials are a
separately authorized plan. The inherited stable-release gates remain controlling.
