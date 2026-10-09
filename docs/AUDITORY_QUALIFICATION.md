# Auditory admission, qualification and reconciliation (1.6.0a3)

Collection and evidentiary support are separate decisions. A failed synthetic
probe must not prevent an explicitly authorized experiment. A successful API
call must not make a fluent answer true. No natural task has been qualified by
this release's generated tests or synthetic hosted integration.

## Six independent dimensions

| Dimension | What establishes it | What it does not establish |
| --- | --- | --- |
| Source/execution authorization | Verified witness, source hash, absolute stream, clock, upload authorization, credential and request/run/queue budget | Perceptual competence |
| Transport | Accepted audio request, matching returned model, preserved response and accounted usage | Audio influence or accuracy |
| Response contract | Strict local JSON parsing, exact fields, bounded observations and independent clip clocks | Correct descriptions |
| Input influence | Versioned blinded contrast trials, family and reversed-pair results | Acting, language, music or natural-audio skill |
| Task competence | Recomputed reference benchmark, exact backend/route/revisions/configuration/media category and explicit scope review | Unrelated tasks or global reliability |
| Individual adequacy | Bound atomic observation, applicable qualification, proposition-specific review, unresolved contradictions checked | Claim support without explicit analytical adjudication |

`admission_dimensions` travels with observation receipts. Invalid format,
incorrect perceptual judgments and failed transport are reported separately.
Transport may succeed while the response contract fails. Historical capability
receipts, including failed probes, retain their original status and bytes.

## Collection lanes

`QUALIFIED` is the default for actual observers. It requires a verified
qualification run with status `QUALIFIED_FOR_SCOPE`, the exact backend identity,
competency, media category and configuration revision. A synthetic influence
certificate alone does not admit this lane. A provider-reported model revision
must match the revision in the actual qualifying trial receipts.

`EXPERIMENTAL` must be selected explicitly in a request or CLI option. Hosted
execution still requires `--allow-remote-media openai`, an environment-variable
credential, a verified provider witness and available spending ceilings. It
collects hypotheses, discrepancies and review priorities while reporting
`UNQUALIFIED`. No bypass flag, automatic retry or model fallback exists.

Mock observations and the inherited implicit injected-transport test route
remain available for zero-cost software tests. They are always inadequate as
perceptual evidence. The compatibility route cannot admit actual live execution.

```text
ave audio-witness source.mkv forensic --audio-stream 1 --start 12 --end 22
ave observer provider-witness forensic submission
ave observer estimate --backend openai-audio --request request.json --witness-run submission
ave observer observe --backend openai-audio --request request.json --witness-run submission --output experiment --observation-lane EXPERIMENTAL --allow-remote-media openai --budget-usd 0.05 --request-budget-usd 0.025 --queue-ledger usage.jsonl --queue-budget-usd 0.25
```

Request example (replace the digest with the actual source SHA-256):

```json
{
  "request_id": "bounded-sound-review",
  "source_sha256": "SOURCE_SHA256",
  "stage": 1,
  "task_profile": "SOUNDSCAPE",
  "media_category": "mixed_anime_audio",
  "question": "Describe audible sound changes within this bounded clip.",
  "observation_lane": "EXPERIMENTAL"
}
```

For qualified collection, supply `--qualification QUALIFICATION_RUN` and an
explicit matching `--media-category`. The qualification cannot grant upload or
spending permission. Output directories are immutable and must be new.

## Provider audio and provenance

`FORENSIC_AUDIO_WITNESS` retains the existing native decoded sample route.
`PROVIDER_SUBMISSION_WITNESS` is a separate PCM16 WAV derivative for the current
OpenAI Chat Completions adapter. It preserves rate, mono/stereo channels, sample
frames, duration and original-source clock segments. It performs no resampling,
normalization, channel mixing or concatenation. Out-of-range/nonfinite samples,
more than two channels, incompatible framing, repeated quantization and explicit
size/duration ceiling violations are refused.

The derivative includes the unchanged parent witness JSON, transformation
receipt, input/output hashes, input/output format, maximum sample error and a
decoded-sample verification. Quantization is not byte identity. The observer
rechecks framing and bytes immediately before submission. Base64 exists only in
the request's memory; it is not retained in receipts. Maximum derivative bounds
are 120 seconds and 24 MiB; ordinary microclips remain at most 30 seconds each,
60 seconds total. Only explicitly opted-in `PERFORMANCE_MUSIC` coherent sections
can use the existing 120-second section policy and its narrower request ceiling.

Official API references: [audio inputs](https://developers.openai.com/api/docs/guides/audio-chat-completions),
[GPT Audio 1.5](https://developers.openai.com/api/docs/models/gpt-audio-1.5).
The adapter requests text output with `store: false`; this is not a promise of
zero provider retention. Structured-output enforcement is not assumed. Local
validation remains mandatory, including when the provider returns accepted text.

## Stage 1 and Stage 2

Stage 1 accepts a bounded neutral task question and opaque clip labels. Context
fields, character names, canonical text, expected emotion and a Stage-1 parent
are rejected. Source titles, file paths, claim IDs and earlier conclusions are
not forwarded. Known clip duration is supplied as neutral temporal metadata.
The operator still must avoid inserting an expected answer into free-form prose;
the tool is not a semantic detector of every leading question.

Stage 2 binds the same ordered source/stream/interval/sample mappings and task to
a valid immutable Stage-1 parent. It may receive dialogue/lyrics, context,
lexical discrepancies, speaker mapping and reinspection questions with explicit
context evidence IDs. It appends a response, never repairs Stage 1 in place.
Agreement with supplied wording is contextual consistency, not independent
transcription evidence. Independent delivery observations require their own
assessment even after a lexical correction.

## Probe v2

The programmatic `make_probe` retains its v1 default for old clients; CLI
`observer fixtures` defaults to v2 and accepts explicit `--protocol v1`.
V2 generates varied tones/silence locally, balances correct positions within
each family and supplies both orders of each matched pair using opaque files.
No answer, family, seed or adjudication path reaches the provider.

The strict contract is exactly `{"choice": 0}` or `{"choice": 1}` with a 128-token
ceiling. Raw text is unchanged. Wrong choices, malformed output, transport
failure and unattempted trials remain distinct; the overall failure rate includes
all planned trials, including invalid and unattempted ones.

The complete trial score requires accuracy at least 0.8 and one-sided binary
chance p at most 0.01. Each family requires accuracy at least 0.8 and p at most
0.05. Reversed calls are not treated as independent observations: both orders
must be correct, at least six pairs per family are required, and a conservative
binary-null pair test must meet p at most 0.05. At least 24 trials are therefore
needed to earn v2 influence admission. Statistical scope is these generated
contrasts; it does not qualify natural auditory tasks or calibrated confidence.

```text
ave observer fixtures fixtures --protocol v2 --trials 24
ave observer probe --backend openai-audio --fixtures fixtures --output plan --dry-run
```

Normal tests never submit the fixtures. The optional integration script requires
an explicit `--execute` flag, remote authorization and configured credentials,
accepts synthetic generated audio only, caps cumulative initial spending at
$0.25 and retains a shared ledger. No automatic charged retry exists. Unknown
usage retains the reservation and blocks further requests.

## Earning task qualification

The original six task-profile names remain supported. Finer request profiles
are `SPEECH_DELIVERY`, `AUDITORY_EVENT_TIMING`, `LEXICAL_TRANSCRIPTION` and
`EXACT_WORD_TIMING`. `SPEECH_PERFORMANCE` maps only to delivery competence for
qualification; `AV_SYNC` maps to audible-event timing, not visual timing.
Nonverbal, music structure, performance music and soundscape remain separate.
No task automatically inherits another task's qualification.

```text
ave observer benchmark benchmark-config.json qualification-run
ave observer qualification qualification-run
ave observer capability-profile history-with-qualification-runs.json profile-run
```

Benchmark configuration contains `dataset`, exact `backend_identity`,
`configuration_revision`, hash-bound `trials` and optional `scope_approval`.
Each trial references immutable observation and independent output-review JSON.
The dataset declares its ID/revision, task, media category, source kind,
source-first reference methodology, scope, limitations and examples. Each
example binds source hash, absolute stream, interval, development/held-out split,
positive/negative/ambiguous control, expected PRESENT/ABSENT/ABSTAIN, reference
reviewer votes and reference provenance. Duplicate intervals cannot inflate n.
Human disagreements are retained and require ambiguous/abstention references.

Output review binds example ID and original observation hash, records evaluator,
independence/blinding declarations, predicted label, reason and measured timing
error where applicable. Raw contract validity is recomputed. Positive timing cases
require a measured error no greater
than 50 ms for audible events or 20 ms for exact word timing. Missing or larger
errors fail that example; a correct PRESENT label alone cannot qualify timing.
Malformed outputs still need verified collection provenance to count as actual
observer trials. Invalid/unverified transport cannot hide behind malformed JSON.
Contract failures count in the overall error rate rather than disappearing
from the denominator. Every incorrect positive, including invalid output or
missing/out-of-tolerance localization with a PRESENT label, also counts as a
false negative. Literal false positives count valid PRESENT predictions on
negative controls; invalid output remains separately reported. Each positive,
negative and ambiguous control family reports its own failures and failure rate,
so failures of absence detection or abstention cannot be diluted by other cases.
Reference and
output evaluators must differ for scoped natural qualification.

Qualification requires at least 20 held-out examples, at least six positive and
six negative controls and two ambiguous examples, no missing examples, overall
failure rate at most 0.2, false-positive rate at most 0.1 and false-negative rate
at most 0.2, with failure rate at most 0.2 in each control family. Only actual,
context-minimized observer receipts on a declared
natural-audio reference set can advance beyond provisional. An explicit scope
approval must bind the exact dataset digest, task, category and configuration.

States are NOT_TESTED, PROVISIONAL, VALIDATED_ON_BENCHMARK, QUALIFIED_FOR_SCOPE,
FAILED and UNRESOLVED. A small or generated/mock reference set stays provisional.
A passed sufficient natural benchmark without scope approval is merely
VALIDATED_ON_BENCHMARK. A one-clip owner review is never global qualification.
Reference authenticity and independent review are attributed human declarations;
hashes verify bytes, not the truth of those declarations.

Benchmark outputs preserve original response/review bytes plus a scored snapshot.
Every containing AV input run is verified, including outer runs around nested
observation runs. Their manifests are publication dependencies. Output inside
any such run is refused before staging, and membership is rechecked before
publication; standalone input artifacts remain supported.
Loading a qualification verifies the run and originals and recomputes the whole
record. Editing a status field or supplying a self-declared certificate fails.
The current scorer is `auditory-qualification-v2`; the qualification wire schema
remains v1. Earlier scoring records remain immutable and format-readable, but
cannot serve as current admission certificates. Recompute the retained original
benchmark configuration into a NEW output run to earn current eligibility;
do not modify a historical score, revision, response, approval or manifest in place.

## Scoped support and legitimate routes

An AO reference needs valid actual-observer collection receipts, verified
source/stream/interval/transform lineage, applicable qualification, an atomic
record pointer, and a separate `ave.auditory-observation-assessment.v1` review.
That review binds observation hash/index, exact proposition, source interval,
statement, reviewer, scope, adequacy, unresolved contradictions, reason and a
hash-bound qualification artifact. `ELIGIBLE_FOR_SUPPORT` describes the witness;
it is distinct from an explicitly adjudicated `CLAIM_SUPPORTED` conclusion.
The assessment and scene observation statement must equal the description in
the selected immutable model observation. Put analyst paraphrases and further
interpretation in a separate claim or inference, keeping the reported atomic
statement unchanged.
Valid recomputed proofs in NOT_TESTED, PROVISIONAL, VALIDATED_ON_BENCHMARK or
FAILED states leave this witness inadequate; they do not abort an independently
adequate route where audio is supplementary. Missing qualification, wrong scope,
tampered proof bytes or an invalid atomic binding never grant support. Routine
QUALIFIED collection still requires QUALIFIED_FOR_SCOPE before submission.

An experimental response may later receive a separate qualification and scoped
review without rewriting its original UNQUALIFIED receipt. Stage-2 supplied
wording remains contextual rather than independent lexical support. Delivery
eligibility does not establish emotional acceptance; an emotional reading needs
articulated inference and its actual premises. Audible timing does not establish
visual contact. Music guesses remain candidates; use MID for recording,
composition, arrangement, performance and phrase identity.

Reconciliation and claim-delta runs freeze each separately admitted qualification
run under `qualification-proofs/`, including its original manifest, benchmark
snapshot, original responses/reviews and command record. The
`qualification-bindings.json` sidecar binds scene evidence IDs (or the before/after
delta sides) to the retained manifest hashes. Original proof files are also
recorded as publication dependencies. A retained proof can be recomputed after
the external original moves; changing its copied bytes fails run verification.
Publishing inside an input qualification run is refused before staging begins.
This archival verification establishes recorded integrity, not perceptual truth.

An explicit same-proposition conflict is resolved only when exactly one rival
has adequate, affirmative effective support and every other rival is explicitly
contradicted. Pending rivals, unresolved prerequisites and multiple affirmative
rivals leave every conflicting observation inadequate for support, irrespective
of evidence channel or observation order. This includes human, text, static
visual and qualified model-audio rivals. Only affected nodes and materially
dependent conclusions are rechecked; unrelated observations retain their scope.

Observations may declare `evidence_roles` for every reference. Roles are REQUIRED,
SUPPORTING, CONTEXTUAL, CONTRADICTORY and NON_DISCRIMINATING. Dependencies may
declare their role; omitted roles preserve legacy required-premise semantics.
Supplementary edges do not propagate necessary-premise failure. Necessary lexical
or claim dependencies do. Optional claim premises require explicit proof routes.

Claims may declare `support_routes`. Each route declares `id`, narrower `scope`,
`essential`, `interchangeable` groups, `supplementary`, `corroborating` and
`insufficient_alone` observations. Every listed premise is classified. Required
global premises cannot be omitted. A route survives only with adequate,
affirmatively adjudicated essential evidence. An insufficient-alone witness must
be combined with another essential witness. A text/image route may survive failed
optional audio. AV synchronization requires temporal visual review, audible-event
timing and their shared original-source clock; visual-only support stays OPEN.
Claims still require separate attributed adjudication and articulated inference.

Corrections append state overlays. A mistaken quoted word can become CONTRADICTED
while unrelated louder/prolonged delivery survives its own assessment; a command
interpretation materially depending on that word becomes RECHECK. Raw responses,
declared premises and prior packets are preserved. No premise is silently deleted
to save an interpretation. Claim-to-claim dependencies propagate in the same DAG.

Independence checks group shared raw artifacts, upstream references, context,
assumptions, model/clip/context provenance and graph dependencies. Results distinguish
DEPENDENT_CONSISTENCY and CONTEXT_INFLUENCED_OBSERVATION. Independent corroboration
requires separate attributed preparation declarations without detected dependency;
the declarations are not authenticated by hashes and are never vote weights.

Scene reconciliation retains surviving route scopes, alternatives, OPEN/RECHECK
and one attributed dramatic-event formulation. Claim deltas preserve the frozen
initial claim, atomic auditory reports and qualification states, dependencies,
contradictions, revised formulation, analytical reason and what stayed established.
The reasoning analyst supplies the holistic interpretation; the tool never invents
literary truth or promotes model output to the external corpus's fact category.

See [real-media acceptance scenarios](AUDITORY_ACCEPTANCE_PLAN.md),
[schemas](AUDITORY_SCHEMAS.md) and the release validation receipt for executed scope.
