# Migration and artifact compatibility

The verified software baseline is `v1.5.0a2-git-baseline`, commit
`d44d6a62e4760ea0f8ed7a1b1ad0181c790cb234`. Migration and packaging normalization
precede the cross-modal feature history. The baseline is retained rather than
relabelled as the next prerelease.

The migration record distinguishes original archive bytes from source/functional
equivalence. Git excludes third-party wheels, source dependency archives, model
weights, private media and generated evidence. Those omissions make Git different
from a portable archive while preserving the canonical first-party software.

## Existing paths

Existing episode bundles, `deep-read`, explicit analytical-method precedence,
blind/isolated analysis, acoustic measurements, observer receipts, source hashes,
human review, mock observations and portable private-runtime installation remain
supported paths. Version `1.6.0a1` adds scene/event reasoning records; it does
not reinterpret an old bundle as having completed that new workflow.

Legacy claim records and new scene packets have distinct declared schemas. Keep
legacy readers and their evidence gates. New records may reference old valid
artifacts through stable IDs/hashes, but missing new context or task declarations
cannot be invented during loading. A migration or extension must state its
meaning and preserve the original record.

Stage-1 and Stage-2 auditory artifacts coexist. Raw provider responses, journals,
usage, source/extraction identities, supplied context and historical capability
receipts are retained. A later correction or scoped human review is an additional
record, never an overwrite. Unvalidated or failed observations may be kept for
diagnostic comparison without entering a qualified analytical route.

Temporal extraction retains actual timestamps and source frame indices. Existing
motion windows remain available; new sparse/extracted sequences do not silently
upgrade old motion claims or declarations of actual inspection.

Media acquisition plans retain the packet's selected absolute audio/video stream
indices on each applicable route. Newly generated plans explicitly leave missing
selection unresolved; an executor must obtain a selection rather than assume the
default track. Historical plans without these extension fields remain readable,
but their missing selection cannot be treated as execution authority.

Auditory claim attachment separates each timed model observation into its own
evidence record, retaining the complete original response and artifact hash.
An observation spanning beyond the selected atomic claim stays unadmitted;
attachment does not trim its interval or import another passage's description.
Legacy evidence without an observation selector retains its original semantics.

## Qualification and cost

Existing failed auditory probes remain failed. Task-scoped usefulness, a human
check of one passage and global qualification are different claims. No backend
is declared accurate merely because its response is fluent or because API
transport succeeded. A provider must satisfy the relevant admission and
capability requirements for its specific use.

Normal tests and CI use generated fixtures and mock/local pathways with zero
paid API calls. Hosted execution preserves explicit media authorization,
credential-safe configuration, budget reservation/usage receipts and opt-in
gates. A scene's unresolved auditory question does not itself authorize a paid
request.

## Packaging and executed scope

The flat package layout and CLI entry point are preserved. Dependencies are
installed from `pyproject.toml`; optional portable artifacts use external matching
wheelhouses and retain private installation without network/root/pip. The
original Windows and Linux native profiles have separate text locks.

Baseline local regression was 277 passed, 0 failed, 0 skipped in the supplied,
migrated and fresh-metadata environments. Local Windows wheel/sdist and offline
native smoke validation were executed; Linux archive checks alone did not prove
native Linux execution. Subsequent CI receipts must identify the actually
executed platform and dependency versions.

The first baseline CI run
([37559153891](https://github.com/deep-blue-zero/av-analysis-toolkit/actions/runs/37559153891))
is retained as a failed historical run:

| Executed target | Passed tests | Failures | Errors | Skipped |
|---|---:|---:|---:|---:|
| Linux CPython 3.11 | 276 | 1 | 0 | 0 |
| Linux CPython 3.12 | 276 | 1 | 0 | 0 |
| Windows CPython 3.12 | 259 | 4 | 14 | 0 |

Each job ran 277 tests; distribution steps were skipped after regression failed.
Linux exposed a decoder-version-dependent channel-layout test assumption.
Windows exposed FFmpeg 9.0.2 removal of the old filter-script option, temporary-path
aliases in a relocation assertion and three review-coverage failures. Compatibility corrections
belong to subsequent commits with their own receipts; they do not turn this
baseline run into a pass.

The feature release must publish its exact regression counts, changed-file
manifest, migration notes and known limitations. Old evidence remains bound to
the source/version that produced it. See
[migration record](MIGRATION_FROM_PORTABLE_1.5.0a2.md) and
[packaging](PACKAGING_AND_CI.md) for the source and build details.

## Current executed regression and FFmpeg scope

Local `1.6.0a1` regression: **459 passed, 0 failures, 0 errors, 0 skipped**, with
runtime and test bytes unchanged throughout the run. See
[the source-bound receipt](REGRESSION_1.6.0a1.json) and
[changed-file manifest](FEATURE_CHANGED_FILES_1.6.0a1.json).

Windows CI pins the locally validated FFmpeg **8.1.1**. A separate generated-media
diagnostic on **9.0.2** reproduced three inherited external-AAC coverage failures:
two conservative packet-duration/priming mapping gaps, and an interior cut with
11.667 ms of genuinely missing decoded coverage. These remain explicit partial
coverage; tolerances and retiming guards were not widened. This iteration does
not qualify FFmpeg 9 external-AAC materialization as equivalent to 8.1.1.
The newer file-backed video-filter syntax is separately supported and tested.

Builds use a new external first-party source snapshot, so stale ignored
`build/lib` files cannot enter a wheel. Exact module-byte comparison rejected
an initial cached candidate; the corrected isolated candidate and clean committed
build both passed all four distribution/source/install checks. Windows offline
installation and generated public-scene commands were also executed. Linux
offline ZIP construction checks do not certify the offline installer on Linux;
GitHub Actions separately exercises native Linux online installations.

## 1.6.0a3 optional extensions

Task qualification uses scoring revision `auditory-qualification-v3` within the
unchanged v1 wire format. Revision-v1/v2 qualification records remain readable as
archival JSON, with their original status and bytes retained. They are refused
as current admission proofs: rerun the retained original benchmark configuration
into a NEW output run. The current scorer counts every reviewed PRESENT negative
as a false positive, including malformed output, and invalid/mistimed positive
cases as false negatives. It prevents aggregate accuracy from hiding failures of one
control family. Existing observer schemas, raw responses, failed influence
probes, Stage-1/Stage-2 history and mock/human declarations are unchanged.

Legacy receipts and mock/human review behavior remain readable. Probe v1 retains
its original scoring; v2 uses balanced reversed pairs and strict choice JSON.
Actual new observers default to `QUALIFIED`, which requires an exact recomputed
task qualification. A prior synthetic probe alone no longer admits routine live
collection. Explicit `EXPERIMENTAL` retains upload, credential, budget, format
and source guards. Injected test-double compatibility never becomes perception.
New scene roles/routes are optional; legacy packets retain all-premise behavior.
`ELIGIBLE_FOR_SUPPORT` additionally requires an atomic assessment and benchmark
proof; analyst adjudication is still necessary. No historical artifact is rewritten.
The public task catalog retains its original six `profiles` entries and exposes
the four finer request profiles through the optional `competency_profiles` map.
