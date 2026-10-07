# Retiming fix: 1.1.0rc2+retiming.1

Historical note: this document describes the inherited retiming correction.
The current candidate is `1.2.0rc1+renderbind.1`; its validation is in
[PATCH_VALIDATION](../reports/PATCH_VALIDATION.md). References below to the
retiming patch's own validation describe that earlier delivery.

This is a local patch of the supplied 1.1.0-rc2 package. It does not claim to be
an upstream RC3 or a stable 1.1.0 release.

RC2 could accept a two-second video whose packet timestamps had been doubled.
It normalized derivative durations to parent durations and split the result into
many short rate-one segments. This concealed material drift from the review
validator despite a recorded 1.9-second offset spread and 0.002002-second budget.

Admission now checks observed onset offsets over each connected parent interval
before normalizing durations. A derivative gap or change in extent confidence
cannot reset that budget. Video packet durations must also agree within the
existing two-endpoint allowance. Audio packet padding remains governed by decoded
sample extents and presentation bounds, preserving the AAC interoperability fixes.

Unsupported timing retains verified packet identity but yields
`PACKET_IDENTITY_VERIFIED_REVIEW_MAPPING_INDETERMINATE`, `review_ready=false`,
no review mappings, and CLI exit 3. Observed timing diagnostics remain in the run.
The shared review validator also rejects legacy fragmented mappings whose onset
offsets drift beyond the clock budget across a connected parent interval.
Actual positive parent gaps remain separate; their offsets may differ.

The regression tests include generated retimed video/audio, changed single-frame
duration, rejection of an old-style RC2 mapping, accumulating small drift, and a
genuine parent-gap control. Existing AAC whole/interior, B-frame/coarse-clock,
gap, portability, WAV and estimated-tail cases remain in the suite.

## Validation and distribution scope

Current results are recorded in the patch's `reports/` directory. The original
RC2 Linux/real-media reports are deliberately excluded because their runtime
hashes do not identify this patch. They remain preserved with the original ZIP.
This patch makes no new Linux, real LosslessCut, listening or character-analysis
claim. Computational measurements are not direct listening.

The existing upstream release builder's full validation contract is unchanged.
It still requires matching Linux and real-media reports for an unqualified build;
those gates are not implied by this local fix. To rebuild this candidate archive:

```text
python scripts/build_release.py /outside/new-patch.zip --candidate
```

Native Windows tests, wheel checks, archive checks and the standalone reported
failure reproduction are recorded independently for the delivered patch bytes.
Use `python scripts/self_test.py --report-dir /outside/new-report` to run the
current suite; the original full-release verification entrypoint intentionally
requires all upstream release gates.
