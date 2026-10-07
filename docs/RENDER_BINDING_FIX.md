# Re-render input binding patch

Version: `1.2.0rc1+renderbind.1` / `1.2.0-rc1-renderbind.1`.

The 1.2.0-rc1 re-render command checked its input run, rendered it, then captured
dependency hashes. An input changed during rendering could therefore receive a
new accepted hash, even though a rendered image consumed different bytes.

The patched command binds the parent manifest before verification, copies the
declared artifacts into a temporary private snapshot, and checks every snapshot
member against the original manifest. Images consume only that verified snapshot.
Before publication it verifies the snapshot and original membership and checks
the original dependencies against their retained hashes. It never replaces those
hashes with new post-render identities. Temporary copies are removed before the
output manifest is finalized. This adds temporary disk use proportional to the
input run, including existing plots; it does not copy or decode original media.

Regressions cover a measurement changed during rendering, a measurement plus
consistently rewritten parent manifest, and a transient original-file mutation
that is isolated by the snapshot. The first two refuse final publication; the
third generates images identical to the unchanged control. Existing no-redecode,
preexisting-tamper, archive and retiming checks remain.

These controls handle concurrent input changes; they do not promise protection
against an adversary controlling the process or filesystem at every instant.

The origins document now identifies the current local patch and labels earlier
release sections as history. Audio formulas, the canonical decoder, source clocks,
retiming controls and review accounting are unchanged. Current validation is in
`../reports/PATCH_VALIDATION.md`; inherited reports belong to predecessor bytes.
