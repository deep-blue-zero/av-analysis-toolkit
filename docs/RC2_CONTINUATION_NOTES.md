# RC2 continuation and reviewer handoff

Purpose: harden RC1 against the six contract defects described in the supplied
Codex REVIEW(1).md. This package does not modify or reprocess character analyses.

Canonical entrypoint: README.md. Active specification: HARDENING_SPEC_1.1.0_RC2.md.
Historical RC1 specification is under docs/history and is not current authority.
Current exact-byte execution evidence is in reports; delivery-wide hashes and
report-frozen rebuild observations accompany the ZIP externally.

Important invariants: no automatic perceptual claims; source and derived clocks
are not interchangeable; packet identity is distinct from sample identity; gaps
are explicit; estimates cannot become unqualified exact extent; original manifests
are not rewritten while claiming their old hashes; no overwritten run directories.

The user-supplied review linked to native-Windows and codec reproduction scripts
that were not included with REVIEW(1).md. New tests reconstruct the described
failure modes independently. Do not claim they are the exact Codex scripts.

Remaining external gates: native Windows RC2 regression AND release construction,
actual user-generated LosslessCut clips with selected streams/parent intervals,
and broader FFmpeg-version coverage. Prior RC1 native Windows pass does not certify
RC2 bytes. Linux synthetic packet copies are not LosslessCut UI tests.

## Instructions to the New Chat

Start from README.md, the RC2 specification and current validation/conformance
reports. Verify the delivered ZIP and internal hashes, then run doctor and the
regression suite. Use scripts/verify_release_windows.ps1 on a native Windows host
for the missing platform gate. Admit a real user-supplied LosslessCut derivative
against its parent, inspect identity and coverage separately, then test the review
contract with clearly labeled synthetic declarations when validating accounting.
Do not claim listening from that exercise. Never change source files and continue
citing old regression bindings: rerun tests, regenerate source binding and reports,
and publish a new release identity. Do not redo canonical Girls Band Cry readings
or mutate their checkpoints as part of software validation.
