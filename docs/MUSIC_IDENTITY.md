# Source-bound music identity

Version 1.6.0a2 adds a local-first music identity workflow. It produces attributed
candidates, preserves recording correspondences and groups catalogue work
relations. A separate review adjudicates the exact identity proposition. It does
not recognize every song, certify a singer, or interpret a scene automatically.

## Levels and statuses

| Level | Question |
|---|---|
| RECORDING | Is this derived from this particular recorded master? |
| PERFORMANCE | Is this this particular performed or generated realization? |
| ARRANGEMENT | Does this use a particular treatment, orchestration or parody arrangement? |
| COMPOSITION | Which musical work is realized or referenced? |
| PHRASE | Does this quote a bounded musical phrase? |
| UNKNOWN | Is the identity level itself unresolved? |

Each level has a separate adjudication: `CONFIRMED`, `SUPPORTED`, `CANDIDATE`,
`AMBIGUOUS`, `CONTRADICTED`, `UNRESOLVED`, or `NOT_TESTED`. Lookup candidates always
remain `CANDIDATE`; adjudication never rewrites a provider response. Scores are
provider-native with `score_is_probability: false`; related covers' scores are
never added. Missing metadata is null or an empty list.

An empty lookup, failed executable, short excerpt, or failed recording overlap
**does not mean original, unreleased, a different composition or a different
performance**. It leaves the affected question unresolved.

## Stages, privacy and dependencies

Stages remain separately attributable: selected-source admission, embedded tags,
optional local reuse, optional Chromaprint, authorized AcoustID, authorized
MusicBrainz normalization, family grouping, optional local reference comparison,
and separate review. Each query has a source scope and receipt.

`MusicIdentityBackend` supplies the interface. `embedded-metadata` and
`local-reuse` are `LOCAL_ONLY`; `acoustid` is `DERIVED_FINGERPRINT_LOOKUP`.
`REMOTE_MEDIA_LOOKUP` is reserved; this release has no WAV-upload or commercial
backend and makes no paid observer call. Future backends need separate guards.
Existing hosted auditory permission does not authorize fingerprint lookup.

```text
python -m pip install ".[music,dev]"
ave doctor
ave music identify source.mkv identity-run --audio-stream 1 --start 00:12:10 --end 00:12:40
ave music identify source.mkv local-run --audio-stream 1 --start 12 --end 42 --local-corpus corpus.sqlite --reuse-cache-dir corpus-cache
ave music identify source.mkv fingerprint-run --audio-stream 1 --start 12 --end 42 --fingerprint
```

Default execution uses FFprobe tags without network traffic. An omitted end
selects at most 30 seconds, bounded by the source. Explicit intervals are at most
120 seconds. Multiple audio streams require an absolute stream selection.
Commentary is not silently chosen. Metadata-only use needs no numerical extra;
the optional `music` extra reuses NumPy/SciPy and HTTP uses the standard library.

Install [Chromaprint](https://github.com/acoustid/chromaprint) separately if wanted.
The toolkit does not download, compile or bundle `fpcalc`. `--fpcalc` names an
installed executable. Doctor reports its path/version; the private manifest
retains executable hash, actual command/configuration, duration, native witness
hash, fingerprint and fingerprint hash. Missing/malformed fingerprints preserve
local metadata and an unresolved fingerprint stage, without a lookup.

## Embedded metadata and local corpus

Only title, artist, album, album artist, track, disc, composer, ISRC and recognized
MusicBrainz identifiers from format/selected-stream tags are admitted.
`METADATA_DECLARED` candidates preserve conflicting/stale values; neither tags
nor filenames authenticate identity.

Local queries snapshot the supplied reuse SQLite ledger read-only, then use the
existing descriptor index, detector and immutable correspondence IDs. They retain
`EXACT`, `NEAR_EXACT`, `PROBABLE_DERIVATIVE`, `POSSIBLE_DERIVATIVE` and unresolved
nonmatches. The original ledger is never ingested into or rewritten. Recording
correspondence alone cannot identify a named composition or credited master.
Already indexed identical source/sample windows retain their occurrence IDs as
local identity candidates without fabricating a new correspondence. For repeated
collection queries, `--reuse-cache-dir` preserves the existing detector's decoded
audio/features/index; otherwise temporary caches are used. Mutable caches must
remain outside immutable output runs.

## Authorized AcoustID lookup

```text
ave music identify source.mkv lookup-run --audio-stream 1 --start 12 --end 42 --provider acoustid --allow-external-lookup acoustid
ave music identify source.mkv normalized-run --audio-stream 1 --start 12 --end 42 --provider acoustid --allow-external-lookup acoustid --normalize-musicbrainz --allow-external-lookup musicbrainz --catalogue-cache catalogue-cache --max-external-requests 5
```

A registered application client key comes through `ACOUSTID_API_KEY`, or the
environment variable named by `--credential-env`. Never put a key in a config,
CLI literal, source or receipt. Lookup sends only fingerprint, witness duration,
client identifier and requested metadata fields. Receipts retain submission
class, explicit authorization, source/hash/stream/interval, timestamp and
candidates, with `media_uploaded: false`, fingerprint/duration submission true.
No key or media payload is persisted. Redirects/retries are refused and responses
are bounded. A failed request remains unresolved, with safe error provenance.

The [AcoustID API](https://acoustid.org/webservice) is free for noncommercial use
and publishes usage limits. This command performs one lookup per selected
interval. Chromaprint targets near-identical recordings, not general melody
recognition. Arbitrary short clips' duration/content can differ substantially
from catalogue entries; a high score still supplies a candidate.

## MusicBrainz normalization and families

[MusicBrainz normalization](https://musicbrainz.org/doc/MusicBrainz_API) needs its
own authorization. It uses recording UUIDs, an identifying User-Agent, bounded
request ceiling, allowlisted cache and at least one second between live requests.
Recording, release, release group, artist credit, work, ISRC and cover/medley
attributes remain separate. Receipts retain provider IDs, response hash,
retrieval timestamp and missing fields. Several releases of one recording do
not become independent matches. Cached metadata remains an attributed snapshot.

Strong family proposals join explicit recording-to-work catalogue relations,
including normalized AcoustID recording candidates. Embedded work tags alone
and fuzzy titles remain weak declared candidates. Same-title unrelated works stay
separate. Multiple work relations create partial medley/mashup families; they
cannot support one whole-interval composition automatically.

An explicit composition decision may name `family_ids` to mark a strong,
nonpartial family `SUPPORTED`, retaining its work/member/provider provenance and
adjudication evidence IDs. Weak title groups and partial families cannot be
promoted this way. Grouping itself always produces candidates.

Several `Beat It` covers can form one proposed work family. Scoped musical review
can support composition while the Michael Jackson master remains unresolved.
Generated performance or parody arrangement needs its own actual basis and
separate review. Catalogue votes cannot establish either.

## Local references and auditory guesses

```text
ave music identify source.mkv reference-run --audio-stream 1 --start 12 --end 42 --reference legitimate-local-reference.wav --reference-stream 0 --reference-start 10 --reference-end 40
```

The toolkit obtains no remote reference recording. It calls existing reuse,
source-clock chroma and `ordered_chroma_candidate`; it adds no duplicate DSP.
`--transforms` opts into existing bounded pitch/time hypotheses. Chroma uses
channel 0, with at most 600 ordered frames per side and retained sampled indices.
The original mixed track remains primary and perceptual inspection stays separate.

Use existing `ave musical-relations` to review `SAME_RECORDED_PERFORMANCE`,
`EDITED_RECORDING_REUSE`, `SAME_COMPOSITION`,
`SAME_COMPOSITION_DIFFERENT_REALIZATION`, `ARRANGEMENT_TRANSFORMATION`,
`MUSICAL_PHRASE_RECURRENCE` or `UNRESOLVED`. Different realization still needs
verified distinct-performance evidence; recording nonmatch cannot provide it.

`--auditory-candidates guesses.json` imports names extracted from a verified
existing observation run. Input has `observation_run` and a bounded list of
title/artist/level/`record_pointer` entries. Source, stream and clip coverage must
agree. Raw response stays in its original run, referenced by hash and pointer,
with backend/stage/task attribution. No observer call occurs. Names remain
`AUDITORY_IDENTITY_CANDIDATE`; contextual Stage 2 stays dependent. No task is
globally qualified by candidate import or a synthetic test.

## Separate adjudication and MID scene evidence

```text
ave music adjudicate identity-run scoped-review.json adjudicated-run
```

`ave.music-identity.review.v1` has reviewer, exact source scope, evidence and
decisions. Evidence has ID, class, hash-bound artifact, source, level, candidate
IDs, statement and actual-inspection declaration. Classes are
`SOURCE_VISIBLE_CREDIT`, `OFFICIAL_CREDIT`, `SCOPED_IDENTITY_REVIEW`, and
`REFERENCE_IDENTITY`. A decision has level/status/candidate IDs/evidence IDs/reason.
Optional `declared_candidates` supplies label/level/title/artist for a reviewed
identity subject; evidence/decisions can use that label. The resulting subject
remains a candidate and its adjudication is a separate record.

Composition support can use inspected source-visible and official credits from
distinct hashed artifacts, or
a scoped actual-audio review with explicit musical comparison basis. Arrangement
and phrase judgments need their own comparison basis. Performance identity needs
its own inspected performance basis. Recording support needs a credited reference
and a full-query native-verified `EXACT`/`NEAR_EXACT` correspondence to its
hash/selected stream. Metadata, grouping, model names and negatives cannot satisfy
these gates alone. Software verifies declarations, not their perceptual truth.

Recording support also requires native-rate verification with sufficient signal
information. Near-exact single-channel correspondence needs an explicit
`all_channels_inspected_declared` review before supporting whole-track identity;
resampled search proxies and incomplete fragments cannot establish it.

Scene channel `MID` is competent only for `recording_identity`,
`performance_identity`, `composition_identity`, `arrangement_identity` and
`musical_reference` (phrase-level). It checks a verified identity run and
source/hash/stream/clock/coverage, then only the matching adjudication level. It
cannot establish emotion, acting delivery, motion, narrative meaning or creator
intent; these still need their independent premises and articulated interpretation.

Inspected visible/official `No Reason Needed` credits can support composition
despite recording-overlap nonmatch. Those address different propositions and are
not a contradiction. Adding music identity to a Bilibili text/image reading can
strengthen the musical premise while preserving unrelated claims and raw records.
Changed premises still follow the existing recheck/new-adjudication rule.

## Refinement and portable replay

```text
ave music plan identity-run refinement-plan --padding-seconds 5
ave music export adjudicated-run portable-identity
ave music rebind portable-identity source.mkv rebound-run --evidence-bindings evidence-alias-map.json
```

Planning proposes bounded longer/neighboring intervals. It never submits,
silently expands a query, assumes cleaner audio, or separates the soundtrack.
Extra availability needs a hash-verified source. Future separated witnesses must
retain parent hash, stream, interval, tool/version/parameters, derived hash and
artifact inspection, with the mixed soundtrack primary.

Normal runs keep private execution paths and native witnesses. Portable export
retains logical source/artifact aliases, hashes, selected stream, interval and
original clock, plus a path-free portable receipt. It omits generic environment/
command journals, private source names, executable path, fingerprint value,
credentials, media and arbitrary review bytes. Suspicious private metadata fails
closed; original receipts are unchanged. Rebinding requires explicit source and
reviewed-evidence files with matching hashes. Missing reviewed artifacts cannot
be replayed as supported. Export transports metadata, not a complete media bundle.

## Limits and testing

Short/dialogue-contaminated clips, obscure/unreleased/live music, covers,
arrangements, medleys, mashups, extreme transformations, partial quotes and AI
reconstructions can defeat matching. Catalogue coverage/relations are incomplete.
Credits can also be misleading. Keep alternatives, levels and unresolved scope
explicit; no result is not originality and scores are not probabilities.

Ordinary CI uses generated audio/credits and mocked catalogues: no live lookup,
paid call, upload or implicit network integration test. The
[real-media validation plan](MUSIC_IDENTITY_VALIDATION_PLAN.md) requires separate
authorization and is not evidence that those trials ran. Synthetic regressions
cannot qualify real-song recognition, singing/performance or installed `fpcalc`.
