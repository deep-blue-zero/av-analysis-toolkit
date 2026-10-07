# Inventory and review records

The toolkit checks evidence bookkeeping. It does **not** certify that a human or model listened, watched, understood a scene, or made a true observation. A valid receipt is still an externally trusted declaration. Its hash only binds the supplied bytes.

Every run remains `GENERATED_NOT_REVIEWED`. Review validation can report `FULL_DECLARED_COVERAGE` or `PARTIAL_DECLARED_COVERAGE`; both retain `perception_truth: NOT_ESTABLISHED_BY_VALIDATION`. “Full” means the required modalities and intervals or points declared in the inventory have corresponding structurally valid review declarations. It is not a claim about an entire franchise, missing material, or the truth of perception.

## Source admission

```console
python -m avevidence inventory inventory-config.json runs/inventory
python -m avevidence verify runs/inventory
python -m avevidence verify runs/inventory --verify-sources
```

Use a new output directory for each run. Config-relative media paths resolve against the config file's directory. A missing file, wrong expected hash, duplicate materialization ID, ambiguous preference, omitted required stream, or unknown config field causes an error. No missing source is silently marked admitted.

The config is a JSON object with a nonempty `sources` list:

```json
{
  "schema": "ave.inventory.config.v1",
  "required_logical_source_ids": ["example-source"],
  "sources": [
    {
      "logical_source_id": "example-source",
      "materialization_id": "example-capture",
      "path": "media/example.mp4",
      "selected_streams": {"video": 0, "audio": 1},
      "required_modalities": ["audio", "motion"],
      "preferred": true
    }
  ]
}
```

This illustrative path must be replaced with an existing file. Stream indices are **absolute ffprobe indices**, not the first or second stream of a particular kind. Explicit selection is required even when there is only one matching stream. An attached-picture stream does not satisfy the video requirement.

| Field | Meaning |
| --- | --- |
| `logical_source_id` | Stable identity for the source work or recording being counted. |
| `materialization_id` | Unique identity for these particular bytes/capture. Multiple materializations can belong to one logical source. |
| `path` | Existing local media file. The run stores its resolved path, SHA-256, size and actual probe. |
| `selected_streams` | Object containing explicit `audio` and/or `video` integer indices. Required modalities must have corresponding selections. |
| `required_modalities` | Distinct list drawn from `audio`, `motion`, `visual_stills`. No modality implies another. |
| `preferred` | Exactly one preferred materialization per logical source. A single materialization defaults to preferred; multiple materializations require an explicit choice. |
| `required_intervals_seconds` | Optional object containing `audio` and/or `motion` lists of `[start,end]` pairs. Defaults to the preferred file's entire duration for required audio/motion modalities. |
| `required_points_seconds` | Required nonempty list of exact source times when `visual_stills` is required. Point review does not mean continuous viewing. |
| `expected_sha256` | Optional prior digest which must match the newly read file. |
| `evidence_weight_group` | Optional declared relationship between sources. Identical hashes and materializations of the same logical source are grouped conservatively even without this field. |
| `title`, `notes` | Optional descriptive strings. |

The optional top-level `required_logical_source_ids` must match the configured source set exactly. If omitted, completeness is only relative to the rows provided. The program cannot detect works the config author omitted.

Different copies of the same bytes, multiple captures of one logical source, and sources explicitly placed in the same evidence group do not multiply independent evidence weight. The report's computed groups allow at most one evidence unit per connected group; different groups are not thereby certified statistically independent.

Intervals use the admitted source clock: original PTS minus the admitted format origin, in seconds. They are start-inclusive and end-exclusive. A full container duration can include a delayed stream, an early stream ending, a gap or padding. If the intended review target excludes such regions, define that requirement explicitly and retain the reason in project documentation. Do not silently shorten requirements merely to obtain a full result.

For stills, use `source_seconds` from the verified frame output, not the originally requested timestamp when the selected frame differs. For example:

```json
{
  "logical_source_id": "example-source",
  "materialization_id": "example-capture",
  "path": "media/example.mp4",
  "selected_streams": {"video": 0},
  "required_modalities": ["visual_stills"],
  "required_points_seconds": [1.25, 3.75]
}
```

This is one row to place in the config's `sources` list. Reviewing these two points cannot establish what happened between them.

## Generate declarations only after actual presentation

```console
python -m avevidence review-templates review-drafts
python -m avevidence review-check reviews.json runs/inventory capabilities.json runs/review-check
```

The template command creates conspicuous `REPLACE_*` values. It does not create reviews or valid capability evidence. Placeholders are deliberately rejected until completed. A capability check is an external tool/human action, separate from media generation. If a model's environment cannot deliver audio or motion, it cannot obtain that capability by decoding files, reading transcripts, or editing the JSON.

The records input is either a JSON array or a `.jsonl` file with one review object per nonblank line. The inventory argument can be the inventory run directory, its `run.json`, or its listed `inventory.json` result. A bare inventory object without a verified run is not sufficient. Select the inventory child explicitly when working with a bundle.

The CLI exits `2` for invalid input, `3` for structurally valid but partial declared coverage, and `0` for full required **declared** coverage. Use the explicit report fields rather than treating a successful process exit as proof of perception.

## Capability and presentation declarations

The capabilities file has this shape. The placeholders below are intentionally invalid:

```json
{
  "schema": "ave.capabilities.v1",
  "capabilities": [
    {
      "capability_id": "REPLACE_CAPABILITY_ID",
      "reviewer": {"id": "REPLACE_REVIEWER_ID", "type": "human", "name": "REPLACE_REVIEWER_NAME"},
      "modality": "audio",
      "mechanism": "audio_playback",
      "verified_at": "REPLACE_TIMEZONE_DATE",
      "receipt": {"kind": "human_attestation", "path": "REPLACE_RECEIPT_PATH", "sha256": "REPLACE_SHA256"}
    }
  ],
  "presentations": [
    {
      "presentation_id": "REPLACE_PRESENTATION_ID",
      "capability_id": "REPLACE_CAPABILITY_ID",
      "reviewer_id": "REPLACE_REVIEWER_ID",
      "reviewer_type": "human",
      "modality": "audio",
      "mechanism": "audio_playback",
      "presented_at": "REPLACE_TIMEZONE_DATE",
      "logical_source_id": "REPLACE_LOGICAL_SOURCE_ID",
      "materialization_id": "REPLACE_MATERIALIZATION_ID",
      "source_sha256": "REPLACE_SHA256",
      "stream_index": 1,
      "intervals_seconds": [[0, 10]],
      "evidence": {"kind": "source"},
      "receipt": {"kind": "human_attestation", "path": "REPLACE_RECEIPT_PATH", "sha256": "REPLACE_SHA256"}
    }
  ]
}
```

A capability declaration names a reviewer, modality and mechanism. A presentation separately binds that capability to exact materialized bytes, a selected stream, an evidence object and actual declared presented times. Reviews must refer to both IDs. A receipt file must exist, be nonempty, and match the stated SHA-256. Receipt paths resolve against the capabilities file. Merely supplying a receipt hash or a “verified” flag is insufficient.

| Modality | Required mechanism | Coverage kind |
| --- | --- | --- |
| `audio` | `audio_playback` | Intervals in a selected audio stream. |
| `motion` | `video_playback` | Intervals in a selected video stream. |
| `visual_stills` | `image_view` | Exact source points represented by images. |

Reviewer type is exactly `human` or `model`. Human receipt kinds may be `human_attestation` or `tool_receipt`; model capability and presentation declarations require `tool_receipt`. The validator checks the receipt bytes and bindings, not whether the receipt honestly describes what the tool/user did. Keep a useful original tool response or dated human presentation note; do not manufacture one from the desired coverage result.

One audiovisual player can provide both modalities, but audio and motion still require separate capability/presentation/review declarations. An image-view capability cannot authorize either. Names and IDs must agree throughout the chain. Capability verification must precede or equal presentation, and review must follow or equal presentation. Dates require an explicit timezone, for example `2026-09-10T12:30:00Z`; dates more than five minutes in the future are rejected.

## Review object

```json
{
  "schema": "ave.review.v1",
  "review_id": "REPLACE_REVIEW_ID",
  "reviewer": {"id": "REPLACE_REVIEWER_ID", "type": "human", "name": "REPLACE_REVIEWER_NAME"},
  "reviewed_at": "REPLACE_TIMEZONE_DATE",
  "logical_source_id": "REPLACE_LOGICAL_SOURCE_ID",
  "materialization_id": "REPLACE_MATERIALIZATION_ID",
  "source_sha256": "REPLACE_SHA256",
  "stream_index": 1,
  "modality": "audio",
  "capability_id": "REPLACE_CAPABILITY_ID",
  "presentation_id": "REPLACE_PRESENTATION_ID",
  "intervals_seconds": [[0, 10]],
  "evidence": {"kind": "source"},
  "observation": "REPLACE_WITH_YOUR_ACTUAL_OBSERVATION"
}
```

Use unique review, capability and presentation IDs within their respective collections. Hashes are completed lowercase SHA-256 strings. JSON booleans and numeric strings are not valid stream indices or times. Times must be finite JSON numbers; intervals must be nonempty and bounded by the materialization's duration. Unknown fields are rejected to expose spelling mistakes and unsupported claims.

The review's source/materialization/hash/stream must match the admitted selection and its presentation declaration. A review may cover a subset of what was presented, but cannot claim more. Its evidence identity must match the presentation. The observation text is a declared observation, not a machine-certified conclusion.

## Derivatives: preserve exact parent mapping

For an extracted audio file or AV clip, replace the source evidence object with:

```json
{
  "kind": "derivative",
  "run_manifest_path": "runs/clip/run.json",
  "run_manifest_sha256": "REPLACE_SHA256",
  "artifact_path": "clip.mkv",
  "artifact_sha256": "REPLACE_SHA256",
  "derivative_stream_index": 1,
  "derivative_intervals_seconds": [[0, 3], [5, 8]]
}
```

Use this object in both the presentation and review, adjusting the derivative intervals for the actual declared subset if needed. Paths resolve against the containing records/capabilities file. `artifact_path` is relative to the referenced run. The stream index is the absolute index **in the derivative** and can differ from the parent index recorded in the surrounding review.

The validator verifies the run manifest hash, every artifact's hash/size/membership, the selected derivative artifact, the media-producing operation, and its exact source/stream/modality mapping. Only `extract_audio`, `clip_audio`, `clip-av` and compatible `admit_external_clip` runs can provide the corresponding playback mappings. A feature, ASR or still-image run cannot acquire listening credit by adding an interval field.

Audio-only runs use `metadata.review_mapping`; AV clips use `metadata.review_mappings` with separate entries for audio and motion. Each mapping contains parent source/hash/stream, artifact hash/path, derivative stream, modality and explicit segments with:

```json
{
  "parent_start_seconds": 100,
  "parent_end_seconds": 103,
  "derivative_start_seconds": 0,
  "derivative_end_seconds": 3
}
```

The surrounding review's `intervals_seconds` must equal the mapped source-time union of the declared derivative intervals. A compact PCM file may place source regions `[0,3]` and `[6,10]` next to each other. Playing the full derivative then covers those two source regions; it does not cover `[3,6]`. Padding outside mapped media does not count. Overlapping derivative mappings and undocumented rate changes are rejected.

For still-frame or contact-sheet evidence use the same derivative identity fields, but replace derivative stream/interval fields with `frame_ids`, for example `["F000001", "F000002"]`. The surrounding record uses `modality: visual_stills` and `points_seconds`. The validator resolves the exact frame rows and hashes; contact sheets additionally require the hash-bound parent frame run and its mappings. Duplicate request frames can refer to the same decoded source point, which is counted once. Neither contact-sheet density nor frame count creates audio or motion intervals.

## Portability and verification limits

`verify` checks relative artifact paths, SHA-256, sizes, exact file membership, source-reference structure and nested child runs. Symlinks, path traversal, duplicate members and unexpected files are rejected. Original media paths are not required by default; `--verify-sources` explicitly checks them too. This lets another machine verify a bundle without possessing the original host's absolute paths.

Strict source verification can resolve a missing staging/old-host path to an exact SHA-256-and-size-matched internal artifact listed by an ancestor bundle. This supports generated dependencies after publishing or moving a bundle. It does not skip unavailable originals: a source with neither its original file nor an exact bound internal copy fails. An existing recorded file whose bytes have changed also fails.

For derivative review, the actual referenced derivative run remains necessary. Contact-sheet review also requires its parent frame run. Put these runs in a verified bundle. If recorded manifest paths no longer exist, the validator can resolve an exact hash-matched child run from an ancestor bundle's artifact inventory; it never substitutes a same-name file. A standalone contact-sheet integrity check can validate its declared parent reference without original media, but review validation requires the parent point mappings.

RC2 archives all receipt bytes plus complete inventory/derivative run closure,
including transitive frame-run dependencies. `reviews.json` and `capabilities.json`
use rebased local references. Original declarations remain in explicitly named
`.original.json` files. Revalidation uses the portable inventory path in the report.
Immutable derivative manifests retain their bytes and hashes; ancestor hash lookup
resolves internal parent-run references. Source bytes remain external unless
explicitly included. Portable verification does not make a receipt trustworthy.

Coverage unions overlapping intervals and reports every gap. Alternate materialization reviews remain in the report but do not fill the preferred materialization's timeline. The current schema does not infer alignment between different captures. Full status requires every configured logical source and required modality to have full declared coverage of its explicit targets. The source inventory, required scope, capability receipts, presentation events and reviewer judgments remain distinct audit responsibilities.

The tests use synthetic declarations, bytes and receipts solely to exercise structural checks. They are not character analysis or evidence that anyone actually perceived media.

See [RC2 timing, estimated-tail and portable-closure contract](RC2_MAPPING_AND_PORTABILITY.md) for the new qualified statuses and clock budgets.
