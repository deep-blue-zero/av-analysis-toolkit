# Vocal performance reuse — 1.4.0-alpha1

This extension finds recording correspondence before performance aggregation. It
retains every source window and match, separates media edits from declared vocal
units, and emits measurement-specific baseline contributions. Everything runs
locally. There are no inference API calls, media uploads, trained-model downloads,
or per-minute charges. NumPy, SciPy and FFmpeg are sufficient; Parquet input also
uses the optional PyArrow package.

## Start with existing evidence

Use the same environment as the performance tools. A fresh environment needs
`pip install ".[reuse]"`; use `.[reuse-parquet]` for Parquet baseline audits.

```powershell
python -m avevidence reuse scan examples/reuse-synthetic.json runs/reuse-synthetic --cache-dir cache/reuse --database reuse.sqlite
python -m avevidence reuse compare examples/reuse-synthetic.json "synthetic reference" "synthetic repeated occurrence" runs/reuse-comparison --cache-dir cache/reuse --database reuse.sqlite
python -m avevidence reuse graph reuse.sqlite runs/reuse-graph --speaker "synthetic signal"
python -m avevidence reuse audit reuse.sqlite runs/reuse-audit
```

Generate the example's local input with the recipe below before running these
commands. It contains non-speech signals, so its correspondence results are not
evidence of a voice, emotion or independent acted performance. For a real
character baseline, supply your own source-bound, appropriately reviewed config
and pass `--baseline` with that project's separate baseline file.

Each output is a **new directory**, consistent with the rest of `ave`. Open its
`reuse.html`. The report links `reuse.json`, `graph.json`, `audit.json`, and
`baseline-contributions.json`. A scan includes a self-contained `ledger.sqlite`
snapshot and a small configurable set of audition WAVs. `ave verify RUN
--verify-sources` verifies the published artifacts and their input sources.

Pass a directory of verified `performance` runs instead of a JSON config to
import their subtitle/utterance windows. Text, emotion, speaker embeddings and
lexical similarity are **not** matching features. Imported captions remain
unverified vocal candidates and do not become listening evidence.

```powershell
python -m avevidence reuse scan runs/episodes runs/corpus-reuse --cache-dir cache/reuse --database reuse.sqlite
python -m avevidence performance-search evidence.sqlite "人間" --reuse-database reuse.sqlite
```

Search results then include matched source windows, canonical witnesses, partial
links and feature eligibility. A partial overlap never makes the whole returned
cue a duplicate. To reproduce a decision state, use `--revision N` on graph/audit,
or `--reuse-revision N` on performance-search. Frozen reports include both the
revision and implementation hashes; preserve the published run for reproducible
analysis across software revisions.

## Configuration without a transcript

```json
{
  "schema": "ave.reuse.config.v1",
  "occurrences": [
    {
      "id": "known reaction",
      "source": "voice.wav",
      "audio_stream": 0,
      "start_s": 0,
      "end_s": 1.2,
      "speaker": "character label",
      "unit_kind": "performance",
      "role": "reference",
      "quality": "isolated_voice_asset",
      "vocal_status": "verified_voice",
      "vocal_basis": "Replace with the actual asset provenance or review basis",
      "context": "battle reaction"
    },
    {
      "id": "recording excerpt",
      "source": "recording.mp4",
      "audio_stream": 1,
      "start_s": 120,
      "end_s": 150,
      "unit_kind": "composite",
      "role": "target",
      "quality": "mixed_scene",
      "vocal_status": "unknown"
    }
  ]
}
```

Paths resolve relative to the config. Select absolute stream indices with `ave
probe`; optional `source_sha256` binds a config to exact media bytes. The clock is
the toolkit's original PTS minus source origin. Bounds are half-open. Timestamp
gaps must be separate windows; the scanner refuses to concatenate across gaps.
`--timestamp-tolerance-ms` explicitly permits the existing decoder's bounded
clock adjustment, up to 10 ms; it never inserts missing media.

`unit_kind` is `performance`, `fragment`, `window` or `composite`. A declared
performance/fragment can seed a provisional vocal-unit identity. Windows and
composites have media-edit identities and no new actor-performance ID. This is a
declaration of scope, not a detector assertion about a studio recording session.

`role` is `reference`, `target` or `both` (default). All are indexed. New targets
can retrieve older references, and new references can retrieve older targets.
`quality` is `isolated_voice_asset`, `clean_scene`, `clean_derived`, `mixed_scene`,
`edited` or `unknown`. `vocal_status` is `unknown`, `candidate`, `verified_voice`
or `verified_nonvocal`. Verified declarations require a nonempty basis. **Do not
copy the example's verified status without that evidence.** `measurement_exclusions`
can explicitly exclude `pitch`, `level`, `timbre` or `timing`.

Occurrences identify source bytes, materialization, stream and sample range.
By default the resolved path identifies a materialization; optional `media_id`
provides a stable materialization ID across a file move. Identical files at
different paths remain visible as separate occurrences and can share one
performance identity. Repeating the exact same materialization/window in one
config is rejected. Source samples and source-clock identities are immutable.

## Detection and its limits

1. Native interleaved float64 PCM is decoded through the existing strict decoder
   and cached with source, stream, clock-policy, decoder-version and code hashes.
   A per-channel 16 kHz search representation is explicitly a derived proxy.
2. Overlapping spectral-shape fingerprints enter an indexed SQLite candidate
   lookup. Candidate retrieval is not proof of reuse. Cached windows and detailed
   comparisons are reused; new scans compare only their selected windows against
   indexed candidates. An absent/stale search index is rebuilt from bound sources.
3. Mean-centered waveform correlation finds full and sliding partial matches.
   Native-rate comparisons refine timing and verify exact sample equality when
   rates agree. Channels are preserved; cross-channel comparison can locate
   swapped-channel copies. A proxy or one matching channel cannot establish
   all-channel digital equality.
4. `--self-scan` searches repetitions within selected windows. Per-channel energy
   envelope autocorrelation also reports periodicity candidates. Music alone can
   produce these peaks, so they do not establish copied vocal material.
5. `--transforms` enables spectral temporal matching over a finite grid: duration
   ratios 0.95, 1/1.05, 1, 1.05 and 1/0.95; pitch shifts -1, 0 and +1 semitone;
   and corresponding speed-change hypotheses. It also searches partial windows.
   Log-frequency features and bounded local timing support tolerate some gain,
   EQ, compression, recompression, accompaniment and modest reverberation.

The first four layers need no learned model. The transform search is optional
because it costs more. It is a **bounded hypothesis search**, not a universal
filter inverter: shifts outside the grid, heavy overlapping speech, extreme
effects, very short/steady sounds and some codec edits remain unresolved.
Transform estimates describe B relative to A; they do not identify which version
was the original recording. The software records observed codecs separately and
does not invent an AAC reencoding history or assert that residual sound is BGM.

Defaults: 300 seconds per configured window, 20 candidate occurrences per query,
240 ms minimum waveform fragment (configurable down to 120 ms), and 2,000 reported
matches per pair. Candidate/match truncation is explicit in `reuse.json`.
Long recordings should be segmented into supplied cues or bounded overlapping
windows. Full-source native decoding currently precedes window selection; its
disk cache can be substantially larger than compressed media. Subsequent scans
reuse it. Detailed matching is bounded by window size, not claimed to have
exhaustive corpus recall. An explicit `compare` bypasses candidate retrieval.

`EXACT` means original f64 sample equality across all ordered channels at the same
rate for the reported span. `NEAR_EXACT` requires high waveform correspondence
including energetic subwindows. Lower-strength correspondence is
`PROBABLE_DERIVATIVE` or `POSSIBLE_DERIVATIVE`. Scores, durations, support,
transform hypotheses and alignment tolerances are retained. Numeric correlation
is **not** a probability; `calibrated_probability` is null.

Silence and low-information stationary tones cannot automatically establish
vocal identity. Failure to find a match is `UNRESOLVED`, not `DISTINCT`. Only an
explicit, attributed decision establishes a distinct-performance determination.

## Identity, decisions and correction

`ave.reuse.correspondence.v1` maps A/B source ranges, native sample coordinates,
channels and clock bounds. A/B indicate comparison coordinates; historical
derivation direction remains unknown. This is a separate schema from the legacy
rate-one review mapping. Those admission/retiming safeguards are unchanged.

Full-coverage accepted correspondences may group declared vocal units when their
attribution is supported or an explicit identity decision supplies that basis.
Partial matches remain range links. A composite or unclassified window matching
A and B cannot join A and B, including simultaneous voices on separate channels.
Repeated references to the same source ranges do not multiply their coverage.
Source-fragment links preserve order and unmatched remainders. A better witness
can replace the default or a feature-specific witness without changing an
existing performance ID. Merges retain aliases; reversing a decision can split
groups again without deleting the original observations.

`fragment_occurrences` records the located appearances inside larger configured
windows. Overlapping observations of the same reference appearance are coalesced;
they do not become extra independent-performance votes. Media-edit records expose
these components in playback order. Configured-window counts and detected-fragment
appearance counts are reported separately.

Automatic performance grouping additionally requires supported vocal attribution:
compatible verified isolated-voice references, or full digitally identical mixes
whose relevant vocal attribution is verified. Near-identical mixed soundtrack
alone remains audio correspondence. Transformed matches remain candidates for an
identity decision even when their spectral correspondence is strong.

```powershell
python -m avevidence reuse decide reuse.sqlite MATCH_ID --decision same_performance --reason "Actual supporting evidence" --reviewer "Reviewer"
python -m avevidence reuse decide reuse.sqlite MATCH_ID --decision distinct --reason "Evidence for different takes" --reviewer "Reviewer"
python -m avevidence reuse decide reuse.sqlite MATCH_ID --decision reset --reason "Restore the recorded automatic decision" --reviewer "Reviewer"
python -m avevidence reuse promote reuse.sqlite PERFORMANCE_ID OCCURRENCE_ID --feature pitch --reason "Better suitable witness" --reviewer "Reviewer"
python -m avevidence reuse annotate reuse.sqlite OCCURRENCE_ID changes.json --reason "Attribution correction" --reviewer "Reviewer"
```

Other decisions are `audio_only` and `unresolved`. Every decision, promotion and
annotation appends an event. `changes.json` may revise attribution, quality,
context, unit kind or measurement exclusions; source identities and spans cannot
be changed. Annotation does not silently change correspondence decisions.

Measurements and decisions are separate: a new observation of the same bound
correspondence retains an earlier human decision, and its observation history is
preserved. The initial automatic decision is stored with its policy hash instead
of silently changing when detection software is updated. The mutable ledger is
outside immutable evidence runs. Frozen reports retain their own ledger snapshot.

## Baseline use

`audit.json` keeps raw occurrence counts, grouped identities, repeated coverage,
unresolved remainders and separate eligibility for pitch, level, timbre and timing.
`baseline-contributions.json` contains one selected witness and weight 1 per
eligible group **for each measurement**. Use those records for downstream
aggregation; do not aggregate every occurrence or every detected match.

A gain-adjusted witness can retain pitch eligibility while another witness is
chosen for level. A pitch/time-transformed witness retains its identity without
automatically entering an original-pitch/timing baseline. Automatic selection
falls back to another eligible witness; explicit per-feature promotion retains
the requested selection and reports any exclusion. Canonical quality alone does
not certify representativeness, actor isolation or unprocessed studio audio.

Baseline input is an array of rows containing `occurrence_id`, or an object with
an `occurrences` array; CSV/TSV and optional Parquet use the same column. The audit
flags repeated whole groups, partial reuse and unindexed rows. It never deletes
the input baseline or rewrites its measurements. The exported contribution list
is the controlled aggregation input; missing measurements must be retrieved from
the selected witness rather than copied from an altered occurrence.

The machine-readable field `independent_resolved_unit_count` means deduplicated declared
units under accepted links and the recorded search scope. It does **not** prove
that every unlinked recording came from an independent physical act. Unique
declared vocal seconds use the chosen full witness's clock and include its
declared window; unresolved partial coverage is reported separately. They are not
a measured voiced-activity duration. Preserve occurrence frequency for questions
about game repetition, editing and audience experience. Deduplication cannot
make a hand-selected collection of unusual reactions an ordinary-character baseline.

## Validation

`tests/test_reuse.py` covers exact/gain/polarity/codec/trim copies, background
addition, composites, partial overlap chains, loops, rate/channel changes, bounded
pitch/time/speed transformations, EQ/reverberation, shared-music false merges,
silence, low-information tones, different synthetic takes, cache corruption,
incremental retrieval, clock gaps, stable promotion, revision recovery and
search integration. Synthetic fixtures establish detector behavior, not human
acting quality or universal error rates.

`examples/reuse-synthetic.json` is an original synthetic configuration. The input
is generated locally by the recipe below and is not stored in Git. Private
real-media configurations and their immutable results were excluded from the
public import. See `docs/MIGRATION_FROM_PORTABLE_1.5.0a2.md` for the baseline
checks and limits; synthetic success does not validate real acting judgments.

## Generate the synthetic example input

Run this from the repository root with Python. It writes a new four-second mono
PCM16 WAV under the ignored `media/` directory: one original varying signal,
one second of silence, its exact repeat, and another second of silence. The two
configured windows are nonvocal and have no acted-performance identity.

```python
from pathlib import Path
import math
import struct
import wave

rate = 48000
signal = []
for sample in range(rate):
    t = sample / rate
    envelope = math.sin(math.pi * t) ** 2
    value = envelope * (
        0.50 * math.sin(2 * math.pi * (330 * t + 180 * t * t))
        + 0.12 * math.sin(2 * math.pi * 770 * t)
    )
    signal.append(round(32767 * value))
silence = [0] * rate
values = signal + silence + signal + silence
target = Path("media/reuse-synthetic.wav")
target.parent.mkdir(parents=True, exist_ok=True)
with target.open("xb") as file:
    with wave.open(file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<" + "h" * len(values), *values))
```
