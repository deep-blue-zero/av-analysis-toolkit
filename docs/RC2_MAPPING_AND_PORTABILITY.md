# RC2 mapping and portability contract

This document describes 1.1.0-rc2. It is an implementation contract, not a claim
that any model listened to or watched media.

## Three clocks, not one

Encoded packet coverage, decoded playable coverage, and declared-review coverage
are distinct. AAC packets can have negative PTS, leading skip samples, or trailing
padding. `admit-external-clip` retains packet hashes, timestamp spans and side data;
negative encoded timestamps are not themselves invalid. Audio review mappings are
intersected with decoder sample extents, the nonnegative canonical source clock,
and declared presentation bounds when the container supplies them. A leading
priming-only packet can therefore remain part of the identity proof but contribute
no reviewable parent audio. An admitted derivative may have positive unmapped
pre-roll. It is not safe to assume derivative zero equals parent claim start.

Mappings are anchored by matching packet onsets. Within a contiguous parent group,
parent durations define a rate-one normalized interval; observed onset-offset
variation is bounded by the clock budget. Parent packet gaps are never merged just
because they are smaller than the tolerance. Video mappings additionally intersect
parent packet unions with the derivative's decoded display extents. The latter
join only within one derivative time-base tick, recording every maximum
adjustment; this handles coarse-container frame-duration rounding without merging
positive parent packet gaps. Decoder/sample and encoded-packet
intervals are different metadata layers, not interchangeable proofs.

`timing_basis` records the selected parent/derivative time bases and a fixed
microsecond serialization resolution. Endpoint allowance is one tick of each clock
plus two serialization quanta. The shared mapping validator recomputes it; the
review consumer checks the parent stream and re-probes the actual derivative
stream. A manually enlarged allowance fails. Legacy mappings without this basis
retain the old strict microsecond duration allowance rather than accepting an
unverifiable user-specified tolerance.

Endpoint interpolation represents bounded quantization uncertainty, not verified
time-stretching. Duration disagreements larger than the two-endpoint clock budget
are refused. Coverage unions and padding/gap checks remain strict (1e-7-second
floating arithmetic epsilon); this budget is not an allowance to fill missing time.

## Admission results and command exit

- `VERIFIED_ALL_SELECTED_MODALITIES`: each selected modality has unique encoded
  packet identity, a review-compatible mapping, and complete requested union
  coverage without estimated extents. Exit 0.
- `PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED`: encoded identity is retained but
  the requested interval is not covered continuously. Gap intervals are reported.
  Exit 3.
- `PACKET_IDENTITY_VERIFIED_REVIEW_MAPPING_INDETERMINATE`: identity can be verified
  but timing/playability cannot meet the review contract. Reason is recorded;
  unsupported mappings are not emitted as review-ready. Exit 3.
- `VERIFIED_WITH_ESTIMATED_FINAL_EXTENT`: useful mapping, qualified timing. Exit 3.
- Partial/no packet matches retain their partial/indeterminate results and exit 3.
- Invalid input or an operation that cannot publish a valid run exits 2.

Review readiness is structural, never a declaration of actual perception, isolated
voice identity, decoded-sample equality, or complete audiovisual equivalence.
Cross-modal offset diagnostics remain separate.

## Estimated video tails

Missing final-frame duration may use the preceding PTS difference. `_windows`
records the exact estimated interval and basis. `clip-av` translates it into
`estimated_parent_intervals_seconds`. A review touching that interval receives
`extent_qualifications` and cannot have `full_required_exact_extent_coverage=true`.
A full declaration becomes `FULL_DECLARED_COVERAGE_WITH_ESTIMATED_EXTENT` and
`review-check` exits 3 rather than an unqualified success. Earlier intervals can
be unaffected. Legacy estimates without localized tail intervals are conservatively
qualified over their whole mapping. This is an extent qualification, not a claim
of frame-accurate perception or exactness of all other aspects of the recording.

## Portable reviews

A review-check output now includes:

```
reviews.json                  # references rebased into this output
capabilities.json             # references/receipts rebased
reviews.original.json         # original declarations, provenance only
capabilities.original.json    # original declarations, provenance only
receipts/                     # hash-verified copies
dependencies/<manifest-hash>/  # complete immutable run directories
review_validation.json        # includes portable inventory path
run.json                      # complete recursive artifact inventory
```

The closure includes the admitted inventory, selected derivative runs and
transitive parent-frame runs required by contact sheets. Derivative manifests
are not edited: their identities stay fixed. Hash-based lookup through the
portable run's artifact inventory resolves their historical internal references.
Revalidate with the inventory path in
`review_validation.json -> portable_declarations.inventory`, not an old host path.

The original source media is not copied implicitly. `verify` and `review-check`
can use the archived metadata/derivatives. `verify --verify-sources` still requires
original source bytes or an explicitly included exact hash/size-matched copy.
A receipt remains an externally trusted declaration even after copying and hashing.

Portable output can be larger because it includes complete referenced runs, not
just the one image or clip displayed. Existing output directories remain immutable;
regenerate a review-check output rather than editing its manifests in place.
