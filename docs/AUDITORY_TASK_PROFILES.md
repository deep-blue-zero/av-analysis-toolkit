# Auditory task profiles and scoped review

Auditory outputs are attributed witnesses for a specified source interval. The
coordinating model can combine them with separately cited text, stills and
temporal evidence. An audio observer does not establish a visual action or
become the coordinating model's own hearing.

The six profiles choose neutral questions and preserve the existing strict JSON
response format. Existing requests that omit `task_profile` use
`SPEECH_PERFORMANCE` and the original neutral speech prompt.

| Profile | Audible questions | Limits on interpretation |
| --- | --- | --- |
| `SPEECH_PERFORMANCE` | Pitch movement, pace, loudness, hesitation, articulation, tension | Acoustic description and emotional reading stay distinct. |
| `NONVERBAL_VOCAL` | Breaths, cries, screams, laughter, gasps, groans, sighs | Ambiguous effects remain alternatives; identity is not inferred. |
| `MUSIC_STRUCTURE` | Entries, exits, continuity, sections, recurrence, density, dynamics | Instruments are named only when supportable; dramatic meaning needs other evidence. |
| `PERFORMANCE_MUSIC` | Sung realization, phrasing, vocal development, singer/instrument relationship, ensemble, intensity, audience sound | A bounded formal section retains context; singer identity is not inferred. |
| `SOUNDSCAPE` | Ambience, mechanical sounds, impacts, effects, noise, near-silence, foreground/background | Obscured speech can coexist with useful soundscape observations. Visible causes require other evidence. |
| `AV_SYNC` | Audible onsets, impacts, vocal effort, changes and silence, with timing uncertainty | Audio alone cannot establish contact, movement order or audiovisual synchronization. |

## Qualification remains separate

`capability_profile(backend_identity, historical_probes=..., scoped_reviews=...,
qualification_runs=...)` creates `ave.auditory-task-capabilities.v1`. Without
benchmark records it emits `UNQUALIFIED` / `NOT_TESTED`. Supplied qualification
runs are recomputed and listed by task and media scope; mixed scope outcomes are
reported as `UNRESOLVED`, never chosen by input order. Legacy transcription/timing
summary labels remain conservative; the new `competencies` field lists exact
earned scopes. Actual natural-audio qualifications remain unearned.

Version 1.6.0a3 adds `SPEECH_DELIVERY`, `AUDITORY_EVENT_TIMING`,
`LEXICAL_TRANSCRIPTION` and `EXACT_WORD_TIMING` request profiles. The original
`SPEECH_PERFORMANCE` qualifies delivery only; `AV_SYNC` tests audible event timing
only. Neither inherits lexical, visual or synchronization competence.
The public `observer profiles` command preserves the six-entry `profiles` map;
the four additional request profiles appear in the additive `competency_profiles`
map. Neither catalog declares qualification.

The JSON schema is [auditory-task-profiles.schema.json](../schemas/auditory-task-profiles.schema.json).
This profile is a description of current qualification, not a hosted admission
receipt. Altering its task status cannot qualify a backend. A passed synthetic
input-influence probe does not validate acting, music, emotion, lexical accuracy
or synchronization proficiency.

Hosted execution requires explicit remote-media authorization, an environment
credential reference, verified provider-compatible witness, matching returned
model and available request/run/queue budgets. Routine `QUALIFIED` execution also
requires a recomputed task/media/route qualification. Explicit `EXPERIMENTAL`
execution can collect an unqualified response despite a broad probe failure;
transport, contract, input influence and task status remain separate. See
[the qualification contract](AUDITORY_QUALIFICATION.md) for CLI and benchmark fields. Actual historical failed probes
remain failed. There are no automatic retries, model fallbacks, spending
increases or live calls in the standard test suite.

## Two separate inspections

Stage 1 is context-minimized: no subtitles, character names, scene interpretation,
speaker mapping or expected emotion fields are accepted. It emits `AO_STAGE1`.
The neutral question must avoid embedding the answer in its wording.

Stage 2 emits `AO_STAGE2` and points to a verified `stage1_run` plus the hash of
its original `observation.json`. It uses exactly the same ordered clip bytes,
source hashes, audio streams, requested and actual source intervals, mappings,
and task profile. Identical silent WAV bytes at two source times are not
interchangeable. Invalid Stage 1 output remains recorded and cannot admit Stage
2; a structurally valid abstention can.

Explicit Stage 2 context extensions are:

- `canonical_text`: source-established text, still separate from an observer's lexical guess.
- `speaker_mapping`: a bounded dictionary from opaque labels to source-established speakers.
- `scene_context`: bounded scene context supplied to this contextual inspection.
- `stage1_discrepancies`: a bounded list of differences or errors to retain.
- `reinspection_question`: the question for this separate inspection.
- `context_evidence_ids`: unique references that the scene/dependency layer must resolve.

The entire context budget is 16 KiB and the request file budget 64 KiB. Stage 2
records the supplied context without silently repairing Stage 1's raw response.
Its judgments are dependent on that context; agreement with the text is not
independent corroboration of the text.

## Bounded coherent musical sections

Ordinary requests retain a 30-second maximum per clip, a 60-second maximum total
for comparisons, at most four independently clocked clips, and a 64 MiB WAV
ceiling per clip. No automatic planner extends these defaults.

An operator can explicitly opt into one coherent musical section with these
additional request fields:

```json
{
  "task_profile": "PERFORMANCE_MUSIC",
  "audio_mode": "coherent_section",
  "allow_long_section": true,
  "section_budget_usd": 0.08
}
```

These are extensions to the ordinary source-bound request, which still needs a
request ID, question, source hash and a native audio witness. A single witness
can be supplied separately or through `witness_run`, a path resolved relative to
the request JSON. Comparison `clips` cannot be supplied in coherent-section
mode. The maximum is one continuous clip of 120 seconds and 128 MiB; the
operator must refine a longer interval into meaningful sections. Native sample
identity, source timing and transformations are checked as before.

`section_budget_usd` narrows the configured request budget. The effective ceiling
is the smaller of that value and the operator's request budget; run and queue
ceilings remain in force. An estimate includes the entire section duration and
maximum output tokens. If it exceeds any ceiling, submission is refused.
Opt-in does not authorize remote media, supply credentials, pass qualification,
or raise a budget. The conservative estimate is not an invoice or a provider
billing guarantee; reported usage is retained separately, and unresolved usage
blocks further spending.

Use `estimate_observation(...)` for an offline preparation estimate. It requires
no credentials or submission authorization and does not call a provider. The
standard default estimator still rejects a 40-second clip unless the coherent
section policy is explicitly supplied.

## Keep the complete performance source

`performance_sections(config, output)` prepares an immutable
`ave.performance-sections.v1` run without calling a provider, copying the source
media or cutting arbitrary API-sized payloads. Its configuration uses
`ave.performance-sections.input.v1` and declares:

- `source_path`, `source_sha256`, explicit selected audio stream and optional video stream;
- the complete `performance_interval_seconds`;
- ordered, nonoverlapping sections, each with `id`, `label`, `interval_seconds`, `boundary_origin` and `question`;
- optional section `lyric_ids` / `intercut_ids`, resolved against hash-bound evidence references.

Each lyric or intercut reference has an ID, an `artifact` containing path and
SHA-256, a source interval and its actual timing authority. Relative artifact
paths resolve beside the configuration. Missing IDs, changed hashes, wrong
reference groups and out-of-performance intervals are refused. Gaps between
sections remain visible as unassigned intervals.

Sections up to 120 seconds have status `COHERENT_SECTION_OPT_IN_REQUIRED`;
longer sections have `FORMAL_REFINEMENT_REQUIRED`. Neither status submits audio.
Subtitle display boundaries remain `subtitle_boundary`, not phonetic alignment.
The declaration of a musical/formal boundary is an operator-supplied premise,
not an automated judgment that the formal analysis is correct.

## Scoped human listening

`scoped_human_review(observation_run, config, output)` adds a separate immutable
review record bound to the original observation hash, backend, task and declared
source/stream intervals. The required configuration fields are `reviewer_type`
(`owner` or `human`), `reviewer`, `scope`, `judgment`, `exceptions`, `date`
(`YYYY-MM-DD`) and `inspected_intervals` (ordinary audio locators).

Judgments are `broadly_accurate`, `partly_accurate`, `inaccurate`, `uncertain` or
`abstain`. Declared inspected intervals must lie inside this observation's clips.
The review records `global_backend_qualification: false` and
`changes_original_response: false`. Broad accuracy for one clip cannot turn
failed probes into passes, qualify all tasks, rewrite a provider response or
automatically support every dependent claim.

All current regression coverage uses generated signals and injected transports.
These tests validate contracts, provenance and guards; they do not establish
perceptual proficiency on anime, games or music.
