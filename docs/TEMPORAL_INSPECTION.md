# Adaptive temporal inspection

The inspection packet answers a bounded question by preparing a sequence of
original source frames. It preserves source identity and actual timestamps;
it never records generated frames as inspected motion. No native video model,
episode interpretation, media upload or paid inference is involved.

## Profiles

| Receipt profile | Requested sampling | Use |
|---|---|---|
| `TEMPORAL_LOW` | 2 fps | Locate a broad change and identify a critical window. |
| `TEMPORAL_MEDIUM` | 8 fps | Inspect a supplied narrower window. |
| `TEMPORAL_HIGH` | 12 fps | Resolve remaining sequence questions in that window. |
| `FRAME_COMPLETE_WINDOW` | Every original source PTS frame | Prepare a short critical window when sparse sampling is insufficient. |

`LOW`, `MEDIUM` and `HIGH` are API aliases; receipts always use canonical names.
The fps values are request densities, not a conversion of the source video.
The inherited extractor selects the first actual frame at or after each request.
VFR spacing, source-frame indices, integer PTS, time base, nonzero origin and
duplicate selections remain visible. No interpolated frame is invented. A
frame-complete window contains source frames within its half-open interval;
it cannot establish what happened physically between source frames.

## Preparation API and receipt

```python
from avevidence.temporal_inspection import extract_temporal_inspection

extract_temporal_inspection(
    "authorized-source.mkv", "runs/temporal-01",
    question="Which gesture occurs first in the critical exchange?",
    question_type="CONTACT_ORDER",
    start=8.0, end=10.0,
    critical_interval=[8.2, 8.8],
    profile="TEMPORAL_LOW",
    stream_index=0,
    frame_index_run="runs/source-frame-index",
    width=960,
)
```

Question types are `GENERAL_MOTION`, `CONTACT_ORDER`, `EXACT_CONTACT` and
`AV_SYNC`. A verified frame-index run is reused when supplied. Otherwise the
existing frame-index builder creates a source-bound index inside the packet,
subject to the index-generation ceiling. A supplied critical interval must lie
inside the preparation interval; it is not inferred from a narrative thesis.

The function returns the normal `ave.run.v1` manifest. Its result file is
`temporal_report.json`, with schema `ave.temporal-inspection.v1`. The report has:

- source SHA-256, selected video stream, source clock, origin and time base;
- question, question type, requested and critical intervals, profile and fps;
- `frame_rows`: each artifact path/hash, source-frame index, integer source PTS,
  original/source seconds, requested time, timing delta and duplicate marker;
- `source_frames_in_interval` and `expected_critical_source_frame_indices`:
  complete index-derived coverage inventories, including unsampled frames;
- source-index and frame-sequence manifest hashes, extraction parameters,
  decode strategy, geometry, ceilings and observed resource use;
- status `GENERATED_NOT_REVIEWED` and assessment status `OPEN`.

`all_source_frames_extracted` is preparation coverage, never review adequacy.
`load_temporal_inspection(run)` checks the packet manifest, frame artifacts,
original media hash and source-index dependency before returning the report.
An external cached index remains an explicit dependency; changing its bytes
invalidates later validation. The packet can contain its own generated index.

## Review and safe adoption

```python
from avevidence.temporal_inspection import validate_temporal_review

assessment = validate_temporal_review("runs/temporal-01", "review.json")
```

The declaration schema is `ave.temporal-review.v1`. It must bind the exact
`temporal_run_manifest_sha256`, `temporal_report_sha256`, `source_sha256` and
`stream_index`, and name the reviewer. These fields are also required for a
useful declaration:

```json
{
  "actual_review_declared": true,
  "inspection_mode": "source_frame_sequence",
  "question_answered": true,
  "inspected_interval_seconds": [8.2, 8.8],
  "inspected_source_frame_indices": [198, 199, 200],
  "observation": "Replace with the actual attributed observation and its limits."
}
```

The example omits required binding fields deliberately; it is not a valid review
receipt to copy. Never populate review declarations merely to satisfy a gate.

The inspected frame-index list must be unique and in original source order.
At least two reviewed source frames in the critical interval are needed for a
temporal observation; an adequately inspected isolated still remains static
visual evidence rather than motion evidence.

The assessment schema is `ave.temporal-review-assessment.v1`. It records either
`ADEQUATE_DECLARED` or `OPEN`, the exact report/manifest bindings, reviewer,
adequate interval, inspected frame indices, reasons and original declaration.
It verifies a declaration and artifact coverage, not whether the reviewer
actually perceived the frames or whether an interpretation is true.

For `CONTACT_ORDER`, `EXACT_CONTACT` and `AV_SYNC`, adequacy requires declared
inspection of **every original source frame in the critical window**, including
the frames missing from a sparse extraction. One still, a contact sheet or a
boolean claim that extraction was frame-complete is insufficient. A generated
packet can never adopt `FRAME_COMPLETE_CONFIRMED` on its own. Temporal claims
in a scene packet need this separate bound review assessment and matching scope.

`AV_SYNC` additionally requires `inspection_mode: synchronized_av` and an
`audio_review` object identifying actual audio inspection, the same source hash,
an existing audio stream, source clock, inspected audio interval, audio-event
source time, reviewed visual-event source-frame index and declared tolerance.
Its assessment retains the event delta and whether it lies within that tolerance.
A negative synchrony observation can still be adequately reviewed; a numerical
event pair alone does not establish perceived synchrony. Images cannot substitute
for hearing, and neither event timing nor generated frames proves emotion.

## Adaptive planning

```python
from avevidence.temporal_inspection import plan_escalation

next_step = plan_escalation(
    report, review_assessment=assessment,
    critical_interval=[8.2, 8.8],
    remaining_frame_budget=120,
    remaining_seconds_budget=30,
)
```

This is a pure planner. It starts from the report's current profile, then returns
the next profile and the supplied critical interval. A first low-density
inspection needs a narrower critical interval before it can escalate. Once the
interval is narrowed, density progresses through medium, high and all source
frames. A valid matching adequate review stops extraction with `STOP_ADEQUATE`.
Budget/window limits produce `OPEN_BUDGET`. Planned `next_limits` restrict the
next extraction's frame ceiling and worker deadline to the remaining budget;
pass those limits to the extraction API when executing the plan. Missing narrowing produces
`OPEN_NO_NARROWER_WINDOW`. At the frame-complete ceiling, inconclusive or missing
review remains `OPEN`; more generated frames cannot replace judgment.

Only pass an assessment produced by `validate_temporal_review` or its separately
verified saved receipt. The planner checks report identity but does not load
files or establish receipt authority. Remaining frame/time budgets are supplied
by the caller; planning does not authorize execution or imply a wall-time estimate.

## Resource and format limits

Defaults are a 30-second query window, three-second frame-complete window,
360 requested frames, 250 million output pixels, 8,388,608 source pixels,
one GiB actual output, 1,800 seconds for uncached index generation, 200,000
index rows and a 120-second extraction worker deadline. Callers may lower these
ceilings or explicitly set bounded alternatives; unknown limits are refused.
Frame counts above 10,000, complete windows above 60 seconds and worker deadlines
above 3,600 seconds are always refused.

Requested duration/density is checked before probing. Cached-index selection,
source geometry and predicted output pixels/bytes are checked before image
decoding. Actual frame/output counts are also checked. VFR index counts remain
actual rows, not inferred from average frame rate. Uncached indexing still scans
the full admitted source; longer sources need an existing verified index.

The existing extractor may fall back to full decode when bounded seeking fails.
This is recorded rather than described as a guaranteed bounded decode. The
extraction/index worker runs in a separate interpreter with command deadlines.
An overall worker timeout terminates its process group/tree, including decoding
children, and publishes no successful packet. Standard parent manifest hashing
and environment finalization follow the worker and are not an additional media
decode. Failed staging evidence remains visibly failed, as in other toolkit runs.

Inherited refusals remain: HDR, wide gamut, high bit depth, rotation/display
matrices, non-square pixels and midstream geometry changes need an explicitly
verified preparation route. This feature does not silently tone-map, rotate,
rescale geometry assumptions or introduce a native video model.

Generated 8-bit FFmpeg tests cover all four densities, source-clock/PTS behavior,
VFR and nonzero origin, hash binding, source/index/frame mutation, resource and
timeout refusals, review coverage, AV-sync abstention and adaptive stopping.
They validate preparation and accounting contracts; synthetic declarations in
tests do not establish actual human or model viewing.
