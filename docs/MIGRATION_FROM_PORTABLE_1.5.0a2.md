# Migration from portable 1.5.0a2

Date: 2026-10-06. Canonical software repository:
https://github.com/deep-blue-zero/av-analysis-toolkit.

The source release is `AV-Evidence-Toolkit-1.5.0a2-cloud.zip`, SHA-256
`e44b33fc58891f4f1d62529f1ad6c93fdc76e890de290cb46e9ce97af1cd6825`.
Its 164 members were inventoried and every extracted member matched the archive
before candidate source construction. `MIGRATION_SOURCE_INVENTORY.json` records
individual hashes and classification. The source release reports 277 passing
synthetic regressions, zero failures/errors/skips. Fresh before/after results and
the final gate are recorded in `MIGRATION_VALIDATION.json` after execution.

141 first-party source, documentation, test, script and license files were copied
with their original relative paths. Four documentation/example files were
generalized before public import; their original and new hashes are recorded in
`PUBLIC_SOURCE_SAFETY.md`. All runtime and test bytes are unchanged. The sound flat
`avevidence/` layout is retained. No analytical features are added to this import.
The existing MIT source license and inherited contributor notices are preserved.

Excluded members are classified in the inventory: nine dependency/toolkit
wheels, the Parselmouth source tarball, archive-specific delivery manifests and
generated historical receipts. The real-media reuse example is excluded because
it discloses a user's source path and source-derived diagnostic. Original
archives and receipts remain immutable in the external evidence workspace.
The inventory uses release-relative names and hashes, without private paths.

Dependencies become externally installable from `pyproject.toml`; FFmpeg and
ffprobe remain system dependencies. Optional alignment requires separately
obtained model weights. No user media, credentials, binary dependencies, weights,
virtual environments or runtime evidence are part of Git. Parselmouth 0.4.7 is
GPLv3; its notices are retained. This record is not a legal determination.

Archive byte identity is different from source/function equivalence. The Git
tree is **not** byte-identical to the portable ZIP. The runtime and tests in the
initial import are byte-identical to their inventoried release counterparts.
Packaging normalization is a separate commit and records all changed source
files. Known non-equivalence: the bare checkout does not contain a wheelhouse,
transfer manifest or delivery profile for the private offline installer. A
generated portable release supplies those; the checkout installs through pip.

Portable targets in the supplied edition are Linux x64 CPython 3.11/3.12
(glibc 2.17+) and Windows x64 CPython 3.12. The inherited package metadata says
Python >=3.10. Native execution claims apply only to recorded local/CI results;
wheel tags alone are not platform validation. No paid model call is required
for migration, normal tests, packaging or CI.

The baseline marker is `v1.5.0a2-git-baseline`. It marks the validated migration
and packaging commits before the feature branch. The feature revision must not
change or reuse that release identity.
