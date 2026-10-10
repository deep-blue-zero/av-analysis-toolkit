# Auditory reliability schema extensions

Existing `ave.auditory-observation.v1`, comparison, scene-packet, reconciliation
and claim-delta records remain readable. New optional fields state their new
semantics; loading a legacy record never invents qualification, review or roles.

- `admission_dimensions`: separate source/authorization, transport and usage,
  local response contract, input influence, task competence and individual review.
- `observation_lane`, `media_category`, `task_qualification`, source-input receipt
  and optional preserved qualification-proof run: collection provenance, never
  automatic claim support.
- Witness kind: forensic versus provider-submission. `transformation.json` binds
  native input to PCM16 output, format, clocks and decoded verification.
- `ave.hosted-auditory-capability.v2`: balanced/reversed probe, raw responses,
  family/pair scores, separate outcome kinds, invalid-inclusive failure rate.
- `ave.auditory-benchmark-dataset.v1` and benchmark reviews: source-first reference
  votes, controls, blinding, held-out split and independently evaluated output.
- `ave.auditory-task-qualification.v1`: exact backend/revisions/configuration,
  dataset/category, recomputed metrics/criteria, scope approval and limitations.
- `ave.auditory-observation-assessment.v1`: explicit review of one atomic timed
  report and proposition, with a hash-bound qualification and unresolved conflicts.
  Runtime binds its statement to the indexed raw model description and the
  scene observation; analyst paraphrases belong in separate claims/inferences.
- `ave.scene-qualification-bindings.v1`: sidecar mapping scene or before/after
  evidence IDs to immutable retained qualification runs and manifest hashes.
  Reconciliation/delta manifests include every original proof file as a source
  and every retained byte as an artifact; historical qualification remains
  recomputable after relocation, and copied-proof tampering fails verification.
- Optional observation `evidence_roles`, dependency `role`, and claim
  `support_routes`: validated proof obligations, not Boolean adequacy overrides.
- Optional `independence_declaration` plus derived model/clip/context tags:
  provenance-sensitive agreement categories rather than majority voting.
- Reconciliation `event_synthesis` and per-claim route scopes; delta atomic
  auditory assessments, qualification states, dependencies and integrated event
  formulation preserve the earlier reading and explain material changes.

JSON schemas describe bounded record shape. Executable validators additionally
verify immutable runs, hashes, selectors, source/stream clocks, exact task scope,
parent identity, qualifier recomputation, necessary evidence and adjudication.
Reference and analyst declarations are attributed evidence, not cryptographic
proof of perceptual accuracy. Historical records retain their original meaning.
