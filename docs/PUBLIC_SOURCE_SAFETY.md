# Public source safety — portable 1.5.0a2 import

This record describes the public-source classification and documentation
sanitization performed before the first repository import. The authoritative
private/portable release and immutable validation records were preserved outside
Git; public copies do not replace their evidence authority. These changes alter
documentation and examples, not runtime behavior or analytical admission gates.
See `MIGRATION_FROM_PORTABLE_1.5.0a2.md` for the complete source inventory,
packaging changes and before/after validation.

## Material excluded from the public import

- `wheelhouse/`: the toolkit wheel and eight downloaded native dependency wheels.
- `dependency-sources/`: the downloaded Parselmouth source archive.
- `reports/`: generated source-regression, fresh-install and fixture receipts,
  including workstation paths and archive-specific bindings. Their existing
  results can be summarized with explicit historical scope in the migration
  record; unchanged private originals retain their original hashes.
- `examples/reuse-misuzu.json`: an actual-media configuration containing personal
  source paths, a source filename/hash, and source-derived attribution/context.
- `SHA256SUMS.txt`, `TRANSFER_MANIFEST.json`, `DELIVERY_PROFILE.json` and
  `DEPENDENCIES.json`: archive-specific inventories for the portable delivery,
  rather than the different public source tree. Dependency installation and
  optional offline release construction are documented separately.

No primary media, model weights, credentials, user environment, cache or large
analytical run is intended to be Git input. The repository is software source;
series-specific governance and conclusions remain outside it.

## Generalized first-party documentation

- `README.md`: points to the synthetic reuse example and migration validation,
  with no claim that omitted historical reports validate the new Git tree.
- `docs/PERFORMANCE_REVIEW.md`: replaces the actual series-study subsection and
  source-specific timing residual with generic integration/tolerance guidance.
- `docs/PERFORMANCE_REUSE.md`: removes real character/configuration examples and
  supplies an original, locally generated non-speech demonstration. The recipe
  creates ignored media; it establishes neither listening nor acted performance.
- `examples/EXAMPLE_GIRLS_BAND_CRY_WORKFLOW.md`: keeps the inherited filename for
  release-builder compatibility, while its contents now use fictional filenames,
  illustrative intervals and hypothetical claim types. No episode conclusion is
  supplied.
- `examples/reuse-synthetic.json`: new configuration for the original non-speech
  recipe, with explicit nonvocal status and window identities.

## Source correspondence

Hashes below are SHA-256 over exact file bytes. Original means the supplied
portable release; current means the sanitized public candidate. These are
intentional differences, not archive byte identity. Relative names alone are
used here to avoid republishing workstation paths.

| Relative file | Original SHA-256 | Sanitized candidate SHA-256 |
|---|---|---|
| `README.md` | `ed948eebc25d15d0c13b914ddf3503f5d70510bc488c407f373c0758c443fc3f` | `bbedd9ef5b293a67c43f41ff8c442fc6103f11fec6b747147bc922050f9ba7a2` |
| `docs/PERFORMANCE_REVIEW.md` | `eabb7b9a9621f44f5874dee0d413cf9435d2750580738a3ef00483d721870cf0` | `433b4f71af5647253a2b62ce2f18d5015472137240e6b80c0e5c3e24e57531c6` |
| `docs/PERFORMANCE_REUSE.md` | `f68c1f61365dbdee518ed418d9e20ac9b3d7f30d20b6ab03cf63b4765cb7cb86` | `e219e07341c974cf7b1e8a63ba3937c0499a4aa3263238b51220003e520a4ac0` |
| `examples/EXAMPLE_GIRLS_BAND_CRY_WORKFLOW.md` | `8da03c4608bdf607bb2766fd1977f5745cd438a27aba7611be379b7637758c44` | `50ac17e940e6f66c457fe4d057872fad6f82678126ddb42d07eec4b09300c716` |

The new `examples/reuse-synthetic.json` has no original same-name file. Its
SHA-256 is `50c4bad1cd21bd3012f333939a3e0cb41bac9f13fcb180be30b2b16b4b6c18ba`.

## Licensing and notices

The supplied source already contains `LICENSE` and package metadata declaring
MIT, plus the inherited `LICENSES/GBC_TOOLKIT_MIT.txt`. Preserve those notices;
this migration does not select or invent another license. Preserve the copied
dependency license texts and dependency notice, including the GPLv3 notice for
`praat-parselmouth`. Removing downloaded wheels/source from Git does not remove
those acknowledgments or decide downstream license obligations. Distribution
wording must distinguish source-only Git from optional offline release bundles.

## Point-in-time public-safety scan

The candidate was scanned on 2026-10-07 UTC after this documentation sanitization,
before feature development. The scan checked all readable candidate files,
excluding Git internals and Python bytecode caches. It reported:

- personal workstation/source paths: 0;
- common API-key/token shapes and literal credential assignments: 0;
- data-URI/long-base64 payloads: 0;
- credential-bearing URLs: 0;
- unreadable binary payloads: 0;
- primary-media, weight, wheel/archive, database or executable file extensions: 0.

The localhost URL in server code is a generic runtime endpoint, not a private
source URL. Placeholder media paths and source IDs remain permissible examples.
The synthetic JSON parses, and its documented Python recipe parses without
executing it or creating media. Pattern scanning is evidence within its stated
scope, not proof that arbitrary future additions are safe. Re-scan the staged
candidate before any public push; do not treat this record as an admission gate
for auditory or interpretive accuracy.
