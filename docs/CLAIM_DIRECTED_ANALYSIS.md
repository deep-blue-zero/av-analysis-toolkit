# Claim-directed audiovisual analysis

Release: 1.5.0-alpha2-method-precedence. No paid API, remote media upload,
new heavy model installation, or virtual machine is needed for the core workflow.

Start with a bounded source reading. Ask which claim would actually change if
a pause, movement, delivery or musical relationship were different. Make that
question a separate source-bound claim. Run the smallest evidence operation
that can distinguish its alternatives. Measurements never infer emotion.

## Commands

```sh
python -m avevidence preflight episode.mkv runs/preflight --audio-stream 1 --video-stream 0 --transcript japanese.ass
python -m avevidence subtitles japanese.ass runs/text --source-media episode.mkv --normalize
python -m avevidence performance episode.mkv runs/performance --cache-dir cache --subtitle-run runs/text --audio-stream 1 --coverage-mode decoded --coverage-end 1424 --timestamp-tolerance-ms 3
python -m avevidence contour-query runs/performance runs/gap --start 663.06 --end 665.73 --boundary-origin subtitle_boundary
python -m avevidence compare runs/performance runs/contrast --a-start 659.93 --a-end 663.06 --b-start 665.73 --b-end 666.64 --boundary-origin subtitle_boundary
python -m avevidence evidence-plan claims.json runs/plan --capabilities runs/preflight/capabilities.json
python -m avevidence audit claims.json runs/uncertain --flags BGM_CONTAMINATION,LOW_F0_CONFIDENCE
python -m avevidence audio-witness episode.mkv runs/listen --start 659.93 --end 666.64 --audio-stream 1 --channel preserve --timestamp-tolerance-ms 3
python -m avevidence motion-window episode.mkv runs/motion --start 659.93 --end 666.64 --audio-stream 1 --video-stream 0
python -m avevidence deep-read episode.mkv runs/reading --transcript japanese.ass --method method.md --claims claims.json --contour-run runs/performance --episode-isolated --execute-local-queries
```

Examples are commands, not endorsements of those exact thresholds/endpoints for
other files. Stream indices are absolute. Every published output must be new.
`performance` now also accepts audio with no transcript and produces contours
without fabricated utterances. Strict duration handling remains the default.
The explicit coverage end is a bounded decode limit, not a claim about the
source's exact endpoint. Coarse container timestamps can trim a few tail samples
when cropping at a previously decoded endpoint. For a complete-stream trial,
choose a bounded limit beyond the expected end and retain the decoder's actual
coverage; compare native sample identities before claiming full equivalence.

## Method selection and evidence isolation

`--method` selects the governing analytical method. An explicit UTF-8 file always
takes precedence over `FALLBACK_GENERIC_METHOD`, including with
`--episode-isolated` or `blind-export`. The original file bytes, including BOMs
and line endings, are copied unchanged into the existing `method.txt` location.
A missing, unreadable or invalid UTF-8 explicit file fails before preparation;
it never silently selects the fallback.

`--episode-isolated` independently limits semantic evidence to the supplied
episode/clip. It prohibits later-episode and external-series evidence; it does
not replace, sanitize or pseudonymize the selected method. Method examples and
hypotheses are analytical guidance, not observations of the supplied source.
An explicit method may itself contain identifying context; exact preservation
does not guarantee that such a method is neutral for a blind benchmark.

```sh
python -m avevidence deep-read episode.mkv runs/reading --method governance/analytical-methods/GENERIC_ANIME_EPISODE_ANALYTICAL_METHOD.md --episode-isolated
python -m avevidence blind-export episode-local-utterances.json runs/blind --method governance/analytical-methods/GENERIC_ANIME_EPISODE_ANALYTICAL_METHOD.md
```

No `--method` selects the unchanged embedded fallback text. It is an abbreviated
default, not a substitute for a supplied repository method. Each run now writes
`method-receipt.json` with the explicit/fallback selection, source path/filename,
source and copied SHA-256, copy location, and independent semantic boundary.
Those fields also appear in run metadata and, for deep-read, `analysis-plan.json`.
Blind-export keeps the full provenance receipt outside `analyst-context` so
private source filenames are not automatically introduced into the blind input.

For external audio use `preflight --external-audio audio.wav` and explicitly
declare `--external-audio-offset`. This is an operator mapping with unknown
residual, not automatic AV synchronization. Without it the relation is flagged.
Audio plus separately supplied stills uses `--screenshots-manifest`; unbound
still clocks remain flagged. No visual extractor runs on an audio-only source.

## Claims and reports

The `ave.claims.v1` input has `claims` and `evidence` lists. Each claim includes
an ID, one statement, question kind, required modalities, source locators,
support/counterevidence IDs, inference, interpretation, alternatives and
confidence. Locators carry a full source SHA-256, source-relative clock,
intervals and absolute stream where applicable. Still witnesses use points.
Evidence includes its layer, modality, statement, hash-bound artifact and
quality flags. Attributed perceptual observations also require a reviewer and
`inspection_status: REVIEW_DECLARED`.

Generated evidence-ready claims are PROVISIONAL. SUPPORTED requires a recorded
adjudicator, reason, requisite witnesses and no relevant unresolved quality
gates. These are structural controls, not proof that the reviewer perceived the
media or that the interpretation is true. Inferences and interpretations do not
count as direct evidence. The report always exposes OPEN layers and alternatives.

Question kinds make flags actionable. A subtitle-bounded interval can support
mixed-energy measurement, but cannot establish phonetic response latency.
Motion needs a declared continuous-motion observation. A delivery claim needs
an auditory witness. Candidate pitch does not establish character F0 without
reviewed speaker isolation. Cut candidates retain unverified-event flags.

## Audio boundaries and quality

The public contour query verifies the retained run and sample mapping before
querying. Centers in [start,end) reproduce the original audit statistics.
`--selection contained_rms_windows` excludes RMS windows extending outside
the requested interval. Pitch uses Praat's longer support and is not made
equally precise by this selection. A 10 ms hop is not 10 ms subtitle accuracy.
Low energy is neither digital silence nor absence of audible speech.

F0 saturation, low voiced fraction, weak periodicity and octave jump candidates
are heuristic flags. They are retained, not silently repaired. Music, speaker
overlap and separation interference require contextual witnesses: the toolkit
does not pretend it can identify every contaminant from periodicity alone.

`audio-witness` preserves native rate and channels by default. It offers
`preserve`, `mono_mix`, `left`, `right`, and `channel:N`, with a cancellation
diagnostic before a chosen downmix. WAV format does not establish clean voice.
Alignment alone makes its own separate 16 kHz input where required; canonical
text and native-rate measurements remain intact.

`segment-features` accepts a witness run and source-bound configuration.
HNR and cycle perturbation are gated on reviewed isolated non-singing mono
speech. Jitter/shimmer additionally require reviewed steady voicing. Explicit
kana reading and verified boundaries are needed for approximate mora rate.
CPP has no validated backend in this release and is explicitly unavailable.

## Observer and human escalation

`observer fixtures` creates randomized nonlexical tone-direction/presence
probes. Supply only `observer-input` to an observer; `adjudication` contains
the answers. The whole run is not a blind observer input. `score_probe` checks
accuracy and a one-sided chance test; passing establishes only the tested
contrasts, not reliable emotion, accent or singer judgments.

`ObserverBackend` is a Python adapter protocol. Real adapters require a matching
backend/model/adapter identity and semantic probe receipt before `observe_clip`
can run. Stage 1 rejects supplied transcript, character names and expected
emotions. Stage 2 logs the new context. Raw responses and malformed-response
failures are preserved without silent repair. No hosted adapter is configured;
provider, cost, storage and media-transfer choice must precede one.

The CLI `observer observe` uses a mock for contract tests only. Existing human
review forms remain the production path. Use `performance-review-import`, then
`observer human-import` to retain their validated source-bound declarations.

## Recurrence, baselines, derived witnesses and games

`musical-relations` records lyrical, melodic, phrase, composition and arrangement
relations independently. Several relations may apply to the same pair. Musical
records never union recorded-performance identities. A reuse nonmatch does not
establish a different take. Use the existing `reuse compare`, decisions, graph,
self-scan, fragment appearance and feature-specific source promotion commands.
`ordered_chroma_candidate` is a bounded temporal descriptor, requiring negative
controls and musical review before support; pooled agreement is not a take ID.

`vocal-baseline` uses an actual reuse-ledger revision and admits reviewed isolated
non-singing voice assets only. It counts each recorded performance once and
reports median/MAD/percentiles, leaving insufficient samples OPEN. Analyst
emotion labels are not used for clustering. Unknown partial/edited windows do
not become new acted performances merely because they are different media.

`admit-separation` accepts explicitly generated Demucs/UVR/equivalent outputs,
with model/version/hash/parameters and the exact raw clip hash. Native rate and
sample count must agree. Raw and separated contours/differences are retained.
This does not execute a separation model or validate alignment/cleanliness by
itself. The raw mix stays primary; changed F0 and unknown separation validity
remain review flags.

`game-bind` binds dialogue IDs, speaker, scene/event IDs, isolated assets, UI,
choice/expression/advance states and gameplay locators when these are supplied.
It does not automatically infer semantic UI states from pixels. Prefer known
assets over unnecessary diarization, but keep gameplay as contextual evidence.

## Benchmark and portability

`blind-export` emits neutral episode-local text and the exact supplied method,
or the embedded fallback when no method is supplied.
Only `analyst-context` belongs in the analyst input; keep the crosswalk private.
It reduces supplied-context leakage without claiming the pretrained model has
never encountered the work. Text substitution is not anonymous video.

`modality-ablation` exports independent A/B/C/D evidence packets without prior
verdicts. Each still needs a separate reasoning pass. `evaluate-ablation` scores
supported gain only against attributed source-grounded human adjudications.
Wall time, cost, speaker/timing errors and abstentions are separate from prose
quality. No human reference means unknown quality, not automatic success.

Portable report reading, retained-measurement queries and full media replay are
different capabilities. Original H:/C: paths are provenance. For execution,
resolve an explicit source-hash binding within allowed local roots; do not search
for a matching basename. Full replay needs the original source and optional
model weights. Keep cache retention/removal as dated lifecycle receipts and
do not rewrite an older run's state claims.
