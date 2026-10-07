# Separate provenance, integrated interpretation

The principal unit of targeted audiovisual analysis is a bounded dramatic event.
A scene/event packet gathers separately attributable evidence, atomic
observations, claims, dependencies, conflicts, alternatives and adjudication.
Its purpose is to determine how the complete evidentiary state changes an
interpretation, while preserving the origin and limits of every component.

The reasoning agent writes the integrated reading. Toolkit preparation and
validation do not themselves establish that an agent heard the acting, perceived
continuous video or completed a literary analysis.

## Evidence channels

| Class | Meaning | Limit to preserve |
|---|---|---|
| TXT | Dialogue, source-language text, lyrics or another textual witness | Subtitle times alone do not establish phonetic boundaries. |
| VIS | Static visual evidence | A still does not establish trajectory, contact or order. |
| TVIS | Ordered temporal visual evidence | Sampling density limits the temporal claim. |
| AM | Deterministic acoustic measurement | Mixed-track RMS/F0 does not establish emotion or speaker isolation. |
| AO_STAGE1 | Context-minimized auditory model observations | Model wording and emotion impressions require separate assessment. |
| AO_STAGE2 | Contextual auditory reinspection | Supplied context and the first pass create dependence. |
| HL | Scoped human listening observations | One reviewed clip does not qualify an entire backend. |
| VO | Attributed temporal visual observer report | It is a report from its actual observer/interface. |
| AVO | Attributed audiovisual observer report, if externally available | This iteration does not introduce a native-video model backend. |
| INF / INT | Inference and interpretation | They remain distinguishable from the evidence they depend on. |

Evidence references retain stable IDs and hashes, source identity, selected
stream, interval and transformation provenance. Scene packets reference raw
artifacts rather than unnecessarily copying their contents. Project-level
analytical statuses, including `F-AUD` or another adjudicated auditory fact category, belong to
the external analytical corpus; raw provider output is never promoted to a
source fact by renaming it.

## Executable event workflow

Use the installed `ave` command. Generate the fictional example outside the
checkout to exercise the workflow without primary media or hosted calls:

```text
python examples/build_synthetic_scene.py ../scene-example
ave scene validate ../scene-example/before-scene.json
ave scene prepare ../scene-example/before-scene.json ../scene-prepared
ave scene reconcile ../scene-example/after-scene.json ../scene-reconciled
ave scene plan ../scene-example/after-scene.json ../scene-plan --max-requests 12 --max-frames 120 --max-audio-seconds 60
ave scene delta ../scene-example/before-scene.json ../scene-example/after-scene.json ../scene-delta
```

Every output directory must be new. The example supplies fictional text,
directions and scripted mock reports; it demonstrates record handling rather
than a heard or viewed performance. `scene validate` prints a structural and
binding result and takes no output directory.

`scene prepare` creates `scene-packet.json`, unchanged original
`input-packet.json`, `evidence-bindings.json` and guidance. The prepared packet
materializes resolved physical references while retaining the same logical
packet identity. `scene reconcile` writes `reconciliation.json`; `scene plan`
writes `query-plan.json`; `scene delta` writes `claim-delta.json`, frozen before
and after packets and separate reconciliations. Standard run receipts record
the inputs and result. All five commands accept `--relocations MAP.json`, an
explicit digest-to-path object. Relocation must match the declared hash; filename
guessing or changing a hash to admit replacement content is invalid.

The [scene packet schema](../schemas/scene-packet.schema.json) declares the
contract. Its source uses `original_pts_minus_source_origin`, selected absolute
stream indices and a SHA-256 digest. A narrative boundary, question, evidence,
atomic observations, claims, directed dependencies, conflicts, adjudication and
open questions are required. Intervals are half-open and bounded to 600 seconds;
points must lie inside the scene. Referenced artifacts carry path/hash pairs and
source/clock locators. Referencing bytes establishes identity, not authenticity
or perceptual inspection. Source media can remain external and unreplayed.

The [reconciliation](../schemas/scene-reconciliation.schema.json),
[query plan](../schemas/scene-query-plan.schema.json) and
[claim delta](../schemas/scene-claim-delta.schema.json) schemas describe the
separate results. `deep-read` accepts `--scene-packet PACKET.json` to add
preparation and planning for an event matched to that source; it does not turn
the packet into a completed interpretation.

## Auditory stages and task scope

Stage 1 uses a bounded source audio witness and a neutral question. It normally
receives no expected emotion, character thesis or future-series knowledge. Keep
the complete response and validation outcome, including malformed or failed
responses, as evidence of what the route actually produced.

Stage 2 is optional. It reinspects the same bounded audio while receiving logged
canonical/best-available wording, source-established speaker mapping, narrowly
relevant context and a specific discrepancy or comparison question. Record the
exact supplied context and the first-pass reference. Store Stage 2 separately;
it cannot replace, silently edit or independently corroborate Stage 1.

The auditory tasks differ:

- `SPEECH_PERFORMANCE`: pace, pitch movement, articulation, phrase endings,
  hesitation, breath, tension and overlap.
- `NONVERBAL_VOCAL`: crying, gasping, laughter, screams, sighs and ambiguous
  vocal sounds.
- `MUSIC_STRUCTURE`: entry/exit, section boundaries, continuity, recurrence,
  density and dynamic development; instrumentation only when supportable.
- `PERFORMANCE_MUSIC`: sung realization, ensemble/instrument relationships,
  section development, audience relation and musical/dramatic form.
- `SOUNDSCAPE`: ambience, impacts, mechanical sound, effects, noise, silence
  and foreground/background relations.
- `AV_SYNC`: targeted relationships between audible events and source-bound
  visual events. Audio-only reports cannot independently confirm visual timing.

A speech question obscured by effects may fail while a soundscape question is
still useful. Capability declarations are task-specific and follow actual tests.
Lexical transcription can remain non-authoritative even when delivery observations
are useful. Historical failed probes remain failed; new scoped validation adds
new records rather than rewriting their history.

`ave observer profiles` lists the six profiles. `ave observer capability-profile
CONFIG OUTPUT` exports task capability history. `ave observer
scoped-human-review OBSERVATION_RUN CONFIG OUTPUT` records a separate listening
judgment. All current task capabilities remain `UNQUALIFIED` / `NOT_TESTED`.
A broad model-accuracy judgment does not establish each proposition mentioned
by the model. A proposition-specific listening observation needs its own actual
inspection declaration, reviewer, source/stream, interval and observation.

Ordinary auditory requests use at most 30 seconds per clip, 60 seconds total and
four clips. One continuous `PERFORMANCE_MUSIC` section may extend to 120 seconds
only with explicit coherent-section opt-in and a bounded request budget.
`ave performance-sections CONFIG OUTPUT` preserves the complete performance
object and declared formal sections. Longer objects need meaningful musical
boundaries before observation. See [auditory task profiles](AUDITORY_TASK_PROFILES.md)
for exact context, request, qualification and budget fields.

## Dependencies and local error handling

Split compound model statements into atomic observations. A report of wording,
an abrupt onset and a strained phrase ending are different premises. A textual
correction can contradict the wording while leaving the acoustic observations
unaffected unless those are separately challenged. An interpretation that relied
on the misheard command must still be reconsidered.

Track observation-to-observation, observation-to-claim and claim-to-claim
dependencies. Relationship semantics distinguish support, constraint,
contradiction, dependence, change-triggered reinspection and evidence that does
not discriminate between alternatives: `SUPPORTS`, `CONSTRAINS`, `CONTRADICTS`,
`DEPENDS_ON`, `REQUIRES_RECHECK_IF_CHANGED` and `DOES_NOT_DISCRIMINATE`.
Propagate a changed premise through its
dependents; preserve unrelated observations and claims. A packet retains both
the raw record and the later adjudication.

Edges point from premise to dependent. `SUPPORTS`, `CONSTRAINS`, `DEPENDS_ON` and
`REQUIRES_RECHECK_IF_CHANGED` propagate changes; `CONTRADICTS` and
`DOES_NOT_DISCRIMINATE` retain their declared relation without automatically
rewriting node status. A reasoned adjudication records a state decision.
Reaffirming an unchanged state alone does not invalidate its descendants.

## Reconciliation

Reconcile propositions according to modality competence, source authority,
independence and the consequences of the disagreement. Do not count votes.
Canonical text may govern wording but cannot establish breathiness. Ordered
frames may establish a movement sequence that a still cannot. A measured
soundtrack level cannot establish emotional intensity. Camera focus alone does
not establish who is singing.

Agreement between a caption-informed model and those captions is dependent
agreement. Shared prompts, common source material and supplied Stage-2 context
must remain visible. Reconciliation is stored separately from the observations;
an alternative survives until evidence actually discriminates against it.

Affirmative support requires competent, adequate evidence and explicit attributed
adjudication. Direct facts require adequate premises of the relevant proposition
covering the claim's interval; emotion and interpretation also require articulated
reasoning. Sparse stills, provisional
model reports and unreviewed generated sequences cannot close motion or contact
claims. A changed or inadequate premise leaves affected dependents requiring
review; an affirmative decision cannot silently bypass that requirement.

## Adaptive temporal evidence

Use temporal extraction to answer a bounded question. Reconnaissance starts at
`TEMPORAL_LOW` (about 2 fps); ambiguous windows can move to `TEMPORAL_MEDIUM`
(8 fps), `TEMPORAL_HIGH` (12 fps), then
`FRAME_COMPLETE_WINDOW` over a short critical interval. Each frame retains
source identity/hash, source timestamp, source frame index and extraction
parameters. Full-frame extraction is an escalation, not an episode-wide default.

Keep static confirmation, temporal sampling, inspected frame sequences,
frame-complete inspection, observer reports and `OPEN` separate. Extracting a
sequence alone does not prove that anyone inspected it. Sparse consistency is
insufficient for exact contact, first-mover order, sub-second choreography or
gesture-to-beat synchronization. Stop when the evidence is adequate; otherwise
escalate the smallest relevant interval. Resource ceilings preserve `OPEN`.

```text
ave temporal-inspect generated-media.mp4 ../temporal-low --start 1 --end 3 --question "Which event occurs first?" --question-type CONTACT_ORDER --profile TEMPORAL_LOW
ave temporal-review ../temporal-low review-declaration.json ../temporal-review
ave temporal-escalate ../temporal-low ../temporal-next --critical-start 1.4 --critical-end 1.8 --remaining-frame-budget 120 --remaining-seconds-budget 30
```

The extraction example requires separately generated local media. Supply a
truthful actual-review declaration only after inspection. An optional
`--review-assessment` on escalation accepts a prior assessment result.
The temporal commands preserve original frame/PTS identity and bound decoding,
indexing, memory, output and worker time. Defaults include a 30-second sampled
window and a 3-second frame-complete window. Exact contact/order and AV timing
require all critical source frames plus the corresponding scoped declaration;
AV timing also requires source-bound audio event evidence. See
[temporal inspection](TEMPORAL_INSPECTION.md) for declaration fields, output names
and stop/escalation states.

## One event and a claim delta

A performance event can combine lyrics, sung realization, ensemble buildup,
gesture/gaze, camera transitions, intercuts and audience sound. An action event
can combine dialogue, movement, impacts, music change, render-state transitions
and reactions. The final prose explains how those jointly construct the event.
Evidence IDs remain accessible beneath the prose. Separate modality sections are
useful when the disagreement itself is the analytical issue.

Compare the claim before and after targeted completion. Record the new evidence,
conflicts, surviving premises, changed premises, revised formulation, confidence
change and remaining questions. Dispositions are `PRESERVE`, `STRENGTHEN`,
`REVISE`, `DOWNGRADE`, `REJECT` or `OPEN`. Additional description alone is not a
demonstrated analytical improvement.

`scene delta BEFORE AFTER OUTPUT --decisions DECISIONS.json` accepts an
attributed analyst's `reviewer` and decisions keyed by baseline claim ID. Each
decision includes `disposition`, `reason`, `what_survived`, `what_changed` and
`remaining_open_questions`. Without a decision, detected changes remain `OPEN`;
unchanged claims/premises may be `PRESERVE`. `STRENGTHEN` requires relevant new
evidence and adequately adjudicated support; `REVISE` requires a changed
formulation. The detector does not invent an improved reading or raise confidence.

See [backward compatibility](BACKWARD_COMPATIBILITY.md),
[packaging and CI](PACKAGING_AND_CI.md) and the
[next validation plan](../NEXT_VALIDATION_PLAN.md). Preparation, extraction and
planning commands do not authorize hosted execution.
