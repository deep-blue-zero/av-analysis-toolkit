# Music identity implementation and completion audit

Target: `1.6.0a2` / `1.6.0-alpha2-music-identity`, additive to the
post-PR-1 `1.6.0a1` source. Base commit:
`5ef302c26f1658832b817e68b15a9a800ca6e9e9`.

This implements the music-identification handoff in the AV Evidence Toolkit.
Analytical title repositories, historical media runs, observer responses and the
Git migration baseline are outside this change. Real-media recognition and paid
auditory inference were not executed or qualified by this work.

## Requirement trace

| Handoff requirement | Implementation and verification |
| --- | --- |
| Mandatory preflight | `MUSIC_IDENTITY_PREFLIGHT.json`: fetched base, branch, mandatory reads, paths, dependency and network policy. |
| I–II: identity levels/statuses | `music_identity/__init__.py`, `contracts.py`: six separate levels and seven statuses; all levels retained, no automatic promotion. |
| III: first-class evidence | `music-identity.schema.json`, `contracts.py`, `workflow.py`: source hash, absolute stream, original clock, interval, query/candidate/family provenance, per-level adjudication and traffic. |
| IV: staged resolver | `workflow.py`: separately attributed metadata, corpus, fingerprint, optional lookup, normalization, grouping, references and adjudication. |
| V: local metadata | `metadata.py`: selected-stream/format allowlist, null missing fields, conflicting declarations retained. Metadata tests cover valid, absent, malformed and conflicting tags. |
| VI: local corpus | `local.py`: read-only SQLite snapshot and existing reuse workflow/ledger; native immutable IDs/classifications retained. Optional persistent reuse cache; already indexed source windows retain occurrence identity. |
| VII: Chromaprint | `fingerprint.py`, CLI doctor: optional externally installed `fpcalc`, executable/version/hash/configuration, bounded local witness and fingerprint. Parsing/missing/malformed/duration/duplicate-field tests; actual installed `fpcalc` execution remains unvalidated. |
| VIII: AcoustID | `backends.py`: explicit provider authorization and env credential, fingerprint/duration only, bounded fixed HTTPS request, no redirects/media upload/secret persistence. Mock zero/multiple/error/credential-echo tests. |
| IX: MusicBrainz | `backends.py`: separately authorized, cached, rate-limited recording metadata; releases/groups, artists, ISRCs and work relations distinct. Mock cache/cover/release/missing/conflict/error tests. |
| X: backend abstraction | `MusicIdentityBackend` and local/derived-lookup classes; reserved remote-media class has no implementation or fallback. |
| XI: candidate families | `families.py`: explicit recording-to-work relation preferred; title-only grouping stays weak; unrelated works separate; medley/mashup/partial scope retained. No score aggregation. |
| XII: parody outcome | Separate recording, performance, arrangement and composition decisions; generated Beat It-family test supports composition after scoped review while recording remains unresolved. |
| XIII: references | `local.py`: supplied local references only, existing reuse and ordered chroma implementations, bounded temporal samples. Exact/gain/FLAC/transform/different-realization fixtures; existing musical-relations semantics remain controlling. |
| XIV: auditory model | `workflow.py`: imports only an existing verified, source/stream/interval-bound observation and cited response pointer; names remain `AUDITORY_IDENTITY_CANDIDATE`. No observer call or catalogue authority. |
| XV: scene packets | `MID`, five identity propositions, stream/clock/interval/artifact hash validation. No authority for delivery, emotion, intent, motion or narrative meaning. |
| XVI: reconciliation | Per-level support guards and existing claim-delta integration. Synthetic source/official credits survive a negative waveform overlap; no result and unsupported model names remain unresolved. |
| XVII: escalation | `music plan`: explicit bounded longer/neighboring requests only, no submission or separation; future derived-witness provenance requirements recorded. |
| XVIII: portability | `portability.py`: private execution manifest versus logical source/artifact aliases; fail-closed public projection, verified export, source/evidence-hash rebinding. Historical runs untouched. |
| XIX: CLI | `music identify`, `adjudicate`, `export`, `rebind`, `plan`; default local, provider-specific network flags and bounded intervals/request budgets. |
| XX: immutable schemas/receipts | Four new schemas, run transactions, hashed artifacts, separate adjudication snapshots and query traffic/authorization/response hashes. |
| XXI: modeled smoke cases | Generated No Reason Needed/negative-overlap, Beat It cover-family and Thai/Bilibili-style unresolved-to-enriched scene tests; no user media in fixtures. |
| XXII: synthetic tests | `test_music_identity.py` and `test_music_identity_integration.py`: metadata, providers, grouping, correspondence, reference verification, scene enrichment, privacy and tampering. |
| XXIII: zero-cost CI | Keys blank, paid/network gates disabled, mocked services. New feature makes no paid calls; ordinary CI has no live music-ID requirement or media upload. |
| XXIV: dependencies | Optional `music` extra reuses NumPy/SciPy; HTTP client uses stdlib; `fpcalc` remains external. No binaries vendored; licensing in `THIRD_PARTY_NOTICES.md`. |
| XXV–XXVI: docs/limitations | User guide, prominent README/CLI, limitations, changelog, holistic workflow docs; incomplete databases, covers/transforms/short contaminated cues, scores and negative results stated explicitly. |
| XXVII: version | Next additive prerelease `1.6.0a2`; active specification binds the new release while previous revision and baseline history remain readable. |
| XXVIII: validation/review | Focused/full regression receipt, public-source scan, wheel/sdist source correspondence and fresh installed CLI checks. Diff review categories and resolutions below; PR remains unmerged pending authorization. |

## Deliverables

All fifteen requested deliverable classes are represented: implementation, CLI,
schemas, provider interface, metadata, fingerprint adapter, mocked AcoustID,
MusicBrainz normalization, families, scene integration, portability, documentation,
`MUSIC_IDENTITY_CHANGED_FILES.json`, `MUSIC_IDENTITY_REGRESSION_RECEIPT.json` and
`MUSIC_IDENTITY_VALIDATION_PLAN.md`.

The last document proposes the three existing real-media cases and their expected
identity-layer results. It does not authorize or execute those trials. Ordinary
regression uses generated signals, generated credits and mocked service responses.

## Codex diff review and resolutions

Review scope is this feature against the fetched `origin/main` base. Codex
performed a separate diff-review pass; this does not claim an independent human
or independently qualified auditory reviewer. The requested six review categories
were checked: layer conflation, exact-recording promotion, negatives, privacy,
source/stream binding and family over-clustering.

Substantive findings corrected before publication:

- **Recording promotion:** support now requires a credited reference with matching
  source hash/stream and full query coverage by native-rate, information-sufficient
  `EXACT`/`NEAR_EXACT` correspondence. Nonexact native matches additionally require
  an attributed all-channel inspection. Proxy/partial/chroma matches cannot close
  the recording proposition.
- **Grouping authority:** only explicit catalogue recording/work links form a
  strong work family; copied work tags and title heuristics remain candidates.
  Medleys, mashups and partial/excerpt relations cannot be promoted to a whole-work
  family. Supported families require a separate scoped composition adjudication;
  lookup candidates remain unchanged.
- **Scene binding:** MID must cite the actual verified identity JSON, selected
  source/stream/clock and covered interval. Another file in the same run cannot
  borrow its authority. Reconciliation/delta schemas preserve identity level and
  status, and competence is limited to the requested identity proposition.
- **Review attribution:** source/official credit roles need distinct artifact
  hashes. Imported model titles/artists must occur in the cited immutable response
  value, with bounded types, source coverage and stage/backend attribution retained.
- **Immutable snapshots and Windows behavior:** evidence IDs map to portable hashed
  filenames; copied review evidence is checked against its admitted hash. SQLite
  backup connections close explicitly. Existing chroma receives its required
  native segment identity rather than a duplicate DSP path.
- **Provider failures/privacy:** malformed/excessive candidates retain an unresolved
  query receipt and response hash without arbitrary private response details.
  Raw and JSON-escaped credential echoes, duplicate fields and excessive JSON
  depth are rejected without persistence. Failed MusicBrainz
  attempts also participate in rate limiting; mocks cover transport and JSON
  failures. Request flags distinguish authorization, test doubles and actual traffic.
- **Release/package binding:** the active specification still named alpha1 in the
  first full run. Its current extension now names alpha2. Fresh wheel and sdist
  checks execute the installed `music identify`, assert local-only traffic and
  unresolved initial decisions, and prohibit first-party source-tree fallback.

No remaining substantive issue was identified in these review categories. Limits
are validation scope: no live catalogue query, real-song recognition, installed
Chromaprint execution, remote auditory call or universal recognition qualification.

## Validation records

Exact final counts, execution timestamps, platform, implementation/test/schema
hashes and the preserved earlier release-binding failure are in
`MUSIC_IDENTITY_REGRESSION_RECEIPT.json`. It is a sanitized public record, omitting
host paths, credentials, command journals and raw media. Full local historical
receipts remain external and are not rewritten.

Source-bound distributions include `BUILD_PROVENANCE.json` and `SHA256SUMS` beside
the wheel and source archive. Their receipt binds the clean Git commit and reviewed
source bytes, and reports artifact integrity plus both fresh-install checks.

Publication uses a feature PR. This audit does not grant permission to merge it.
