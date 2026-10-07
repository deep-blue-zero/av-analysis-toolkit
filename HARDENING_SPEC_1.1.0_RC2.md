# Local patch: 1.1.0-rc2-retiming.1

The historical RC2 contract below is retained for context. This local patch adds
pre-normalization packet-onset validation and shared consumer validation of
contiguous source offsets. See docs/RETIMING_FIX.md for its current validation scope.

# AV Evidence Toolkit 1.1.0-rc2: contract-repair specification

Status: implementation contract, authored before RC2 code changes.
Basis: user-supplied REVIEW(1).md reviewing exact RC1 source ZIP SHA-256
9d30c44ee2beac8086dd4621d817918de24d49e8470c5b189fd78b6a3b2dd959.
The linked Codex repro scripts and subsidiary reports were not supplied. Reproduce
its described cases independently; do not claim execution of those missing scripts.

## Scope and invariants

Repair the six agreed areas without changing literary analyses, contacting Drive,
or committing a repository. RC1 is immutable input. Output identity: package
1.1.0rc2, archive 1.1.0-rc2. Remain an RC until native Windows execution and an
actual user-produced LosslessCut clip are externally validated. Do not inherit
RC1 Windows or real-media test claims as tests of RC2. Generated media, synthetic
review declarations and valid receipts do not establish actual perception.

## R1. Release construction and exact-source binding

Enumerate stored relative names: stale lowercase wheel_smoke.json must not match
current WHEEL_SMOKE.json. Refuse casefold-colliding release members. One canonical
selector defines all included non-report paths. Record the sorted path/hash list
and aggregate SHA-256 of UTF-8 `path + NUL + file_sha256 + LF` records. Exclude
reports and generated SHA256SUMS.txt to avoid self-reference. Builder recomputes
list/count/digest and checks runtime/test hash bindings, not just pass/version
flags. Explicit candidate builds may bootstrap evidence; verified delivery builds
require passing current reports. Rebuild the report-frozen delivery twice and
from clean extraction. Publish final hashes/observations externally, avoiding
circular reports. Supply a native-Windows verification entrypoint, but do not
claim Windows execution from Linux.

## R2. Interval-set coverage

Admission verifies the union of supported segments, not their enclosing span.
Record coverage, gaps, packet identity and review readiness independently. No
VERIFIED_ALL_SELECTED_MODALITIES/zero CLI exit for an uncovered requested part.
Producer and consumer share rate-one mapping and coverage arithmetic.

## R3. AAC priming, discard and quantized clocks

Keep negative packet timestamps and skip/discard side data as encoded provenance.
Derive reviewable audio from decoder sample extents and applicable presentation
bounds; never silently clamp pre-roll. Intersect parent and clip playable intervals
through the packet mapping. Quantization allowance is derived from selected stream
time bases, recorded and checked by the shared contract. An arbitrary supplied
large tolerance must not validate retiming or gaps. Packet identity can remain
verified when review readiness is indeterminate. Test whole-file M4A-to-MKA remux
and interior AAC copy through synthetic presentation/review validation.

## R4. Native WAV practical profile

Byte-copy complete single-audio WAVs when the whole source is the artifact.
Otherwise preserve known masks on supported generated f64 WAV paths and check
samples/layout. Never infer stereo from channel count. Test forensic-to-practical
and default bundle composition; unrepairable overlapping audio clocks still refuse.

## R5. Portable review dependency closure

Copy verified derivative runs, artifacts and transitive parent-frame dependencies.
Rebase declaration manifest paths, not immutable derivative manifests. Resolve
transitive references by exact hash in the included closure. Include inventory
run and archive original declarations separately. Test relocation with originals
removed. Source-byte re-verification remains a separate opt-in requiring media.

## R6. Estimated final-frame extents

Label estimated tail interval/basis/confidence. Do not silently count estimates as
exact review coverage. Reviews touching a tail are qualified; earlier intervals
can remain unqualified. Propagate qualification into result/status and test it.

## Acceptance and limitations

Add regression analogues for each described defect and show correct supported
output or explicit non-success classification. Run old/new suite, clean extraction,
archive CRC/hash verification, two-build and clean-rebuild identity, fresh wheel
smoke, local real GBC audio smoke, synthetic AAC/H.264 stream-copy interoperability,
portable relocation and stale-report mutation rejection. Native Windows and actual
LosslessCut UI-produced clips remain NOT_RUN when unavailable. No code change to
upstream analyses, frozen checkpoints or external repositories.

## Technical references

FFmpeg packet skip/discard sample metadata: https://ffmpeg.org/doxygen/trunk/packet_8h.html
FFprobe inspection options: https://ffmpeg.org/ffprobe.html
Tests and media observations, not these references alone, verify toolkit behavior.
