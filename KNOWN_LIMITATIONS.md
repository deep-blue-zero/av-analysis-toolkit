# Current limits

This is the 1.6.0a3 auditory-qualification iteration, stacked on music identity. It prepares and validates
source-bound evidence, dependencies and review records. Its software contracts
do not certify a literary interpretation, an acting judgment or model perception.
The [holistic workflow](docs/HOLISTIC_CROSS_MODAL_ANALYSIS.md) describes the new
event-level contracts; [claim-directed conformance](docs/CLAIM_DIRECTED_CONFORMANCE.md)
retains the earlier task-by-task limits.

- Music lookup generates candidates. Short/contaminated excerpts, covers,
  live/obscure/unreleased music, medleys/mashups, transformations, partial quotes
  and AI reconstructions can defeat matching. Catalogue coverage is incomplete;
  no result never establishes originality. Composition support does not identify
  a master, singer, generated voice or arrangement. Scores are provider-native,
  not probabilities. Real-media identification and installed `fpcalc` execution
  remain unvalidated by generated/mocked tests. [Music identity](docs/MUSIC_IDENTITY.md)
  documents separate levels, external permission and portable metadata/replay limits.

- All actual natural-audio task qualifications remain unearned. The framework can
  recompute scope-specific qualification from held-out, independently reviewed
  benchmark records; hashes do not authenticate human declarations or establish
  the truth of model descriptions. Generated/mocked CI fixtures do not qualify a
  real model. Timing qualification scores positive localization error (50 ms for
  events; 20 ms for words); it does not guarantee every individual boundary.
- The authorized live synthetic integration submitted PCM16 and accounted for
  $0.02514 across 26 requests. Probe v2 failed at 18/24; the first observation
  invented a timeline beyond its clip and was rejected. One explicitly planned
  duration-contract revision returned a valid abstention. This validates that
  executed submission/contract path, without natural acting/music proficiency.
  Historical outputs remain unchanged. See the public integration receipt.
- `EXPERIMENTAL` requires explicit selection and all authorization, supported
  witness, privacy and budget guards. It collects attributed hypotheses. `QUALIFIED`
  requires recomputed exact task/route/media qualification. Neither lane grants
  automatic claim support, and a synthetic influence pass alone cannot do so.
- Stage 2 is a separate contextual inspection of the same audio and source
  interval. Its agreement with supplied text is dependent agreement. Neither stage
  establishes the coordinating model's own hearing. A scoped human review can
  assess one observation without qualifying the backend globally or rewriting it.
- Ordinary audio limits remain 30 seconds per clip and 60 seconds total. One
  coherent `PERFORMANCE_MUSIC` forensic section can extend to 120 seconds and 128 MiB only
  with explicit opt-in and a declared budget. Preparation never authorizes remote
  submission or automatically increases limits, spending or retry counts. The
  provider PCM16 derivative has a separate 24 MiB limit and preserves rate/channels.
- Scene reconciliation checks declared authority, competence, scope and
  dependencies. It requires attributed adjudication to close supported claims;
  it does not create that judgment or establish literary truth. A claim delta
  records the analyst's decision and surviving uncertainty, not automatic
  improvement in interpretation.
- Temporal preparation uses ordered original frames at 2, 8 or 12 fps, with
  bounded escalation to every frame in a short critical window. Extraction alone
  remains `GENERATED_NOT_REVIEWED` / `OPEN`. Exact contact/order requires the
  corresponding complete scoped review; audiovisual timing also requires actual
  audio inspection. No native continuous-video model backend is introduced.
- Temporal indexing can scan the whole admitted source when no verified cached
  index exists. Extraction may fall back to full decode; receipts retain this
  behavior. High bit depth, HDR, rotation, non-square pixels and changing geometry
  still need an explicitly verified preparation route.
- Source separation admits declared derived witnesses and comparisons. This
  release does not install or execute Demucs/UVR or establish artifact-free output.
- Qwen Japanese alignment remains optional. No new multi-aligner comparison or
  manually verified onset benchmark was executed for this iteration. Subtitle
  display boundaries alone are not phonetic alignment.
- CPP/openSMILE standard backends, multiple-tracker agreement, general internal
  pause/articulation extraction, semantic gaze/gesture tracking and automatic
  game UI/choice detection remain unvalidated or unimplemented extensions.
- Game asset/event bindings and shot-cut candidates require declared/reviewed
  identity. Camera focality and subtitle overlap do not identify audible singers.
  Mixed-track pitch and intensity measurements do not isolate speakers or prove
  emotion. Baselines require reviewed isolated non-singing assets and enough
  independent performances; they do not supply generic emotional labels.
- Neutral text reduces supplied-context leakage. It does not anonymize video or
  prove that a pretrained model has never encountered a work. A/B/C/D packets
  need independent reasoning runs; without an adjudicated reference,
  interpretation quality remains unknown.
- Full replay needs separately retained source media and optional model weights.
  Private paths are provenance; cached-query portability is distinct from replay.

Platform and decoder validation applies only to its exact executed source and
environment. The Git baseline passed 277 generated regressions locally with
FFmpeg 8.1.1; that result does not certify new 1.6 source or every decoder version.
Baseline Windows CI with FFmpeg 9.0.2 exposed three audio-coverage failures beyond
the separately addressed filter-option and temporary-path issues. An isolated
9.0.2 rerun reproduced them: rounded FLAC packet extents create apparent 1 ms gaps,
AAC priming normalization leaves an apparent 0.333 ms tail gap, and an interior
AAC copy has an 11.667 ms uncovered decoded prefix. Admission conservatively
retains `PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED`; packet identity alone must
not authorize the entire claimed interval. Do not widen tolerances to hide these
cases. Native 1.6 Linux/Windows CI and decoder compatibility must follow their
actual source-bound receipts. Python 3.13 is outside the current offline targets.

The bare Git checkout contains no dependency wheels or decoder binaries. Optional
portable builds select one ABI/platform profile; compatible wheel tags are not
proof of native execution. See [packaging and CI](docs/PACKAGING_AND_CI.md) and
[baseline migration](docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md). Historical scripts,
guides and release reports do not certify the current source.

FFmpeg, Python, optional model weights and actual perceptual interfaces are
supplied by the destination. The implemented core needs no paid subscription,
hosted call or VM. Standard regressions use generated inputs and injected
transports, never real anime/game media or paid APIs.

Explicit `--method` always takes precedence, including in isolated/blind mode.
Episode isolation constrains semantic evidence independently. The embedded method
is a fallback only; see [method precedence](docs/METHOD_PRECEDENCE_PATCH.md).
