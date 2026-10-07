# Local Japanese performance workflow

Version 1.3.0-alpha1 extends the portable AV Evidence Toolkit. It accepts the earlier
voice-performance-pipeline's `utterances` JSON as well as this toolkit's source-bound
subtitle runs and embedded subtitle packets. Source files and earlier releases are
not modified. There are no remote inference calls, media uploads, account requirements,
or per-minute API charges. Model and package downloads happen only during setup.

## What is included

| Component | Output | Use |
|---|---|---|
| Local Qwen3 ForcedAligner 0.6B | Candidate Japanese unit boundaries, mapped to source time | Locate words in supplied dialogue |
| Praat autocorrelation | F0 candidates and correlation at 10 ms spacing | Inspect pitch contours within a comparable passage |
| Native-rate windowed RMS | Digital level at 10 ms spacing, 40 ms windows | Locate changes in level and compare consistently recorded passages |
| Immutable local caches | Hashed audio, contours and alignments | Reuse expensive work and resume interrupted batches |
| SQLite search | Japanese text, source, supplied speaker label, timing status, pitch/level filters | Retrieve a passage with its source hash and times |
| Offline HTML | Searchable cue table and pitch/level plots | Inspect a run without a server |

The aligner is the original [Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
used through [qwen-asr](https://github.com/QwenLM/Qwen3-ASR). Japanese is supported;
the downloaded model's timestamp step is 80 ms. A 10 ms contour grid does not make
those word boundaries accurate to 10 ms. Some short units get zero-duration model
boundaries and are explicitly flagged. No calibrated alignment confidence is invented.

## Setup

The prepared Windows environment is `.venv` in this delivery's working directory.
It has CUDA-enabled PyTorch and was exercised on the local RTX 3080 Ti (12 GB).
It shadows the necessary packages locally but inherits some base Python packages;
it is not a standalone portable runtime. Models and the virtual environment are
excluded from the source ZIP and wheel.

For a new installation, use a fresh Python 3.12 environment, install FFmpeg/ffprobe,
then install the toolkit. These are the Windows GPU versions used in the local run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install ".[alignment]"
.\.venv\Scripts\python.exe scripts/download_aligner.py models/Qwen3-ForcedAligner-0.6B
```

The download helper records the resolved upstream commit and file hashes in
`MODEL_RECEIPT.json`. Pass `--revision COMMIT` to reproduce a download. The local
validated revision is `c7cbfc2048c462b0d63a45797104fc9db3ad62b7`.
The original model layout is required; a different native-Transformers conversion
is not interchangeable with this pinned qwen-asr adapter. Inference uses local
safetensors, no custom remote code, and no automatic weight download.

For contours/search alone, install `.[performance]` and omit `--model-dir`.
`--device cpu` supports alignment with a CPU PyTorch build, but this delivery's
throughput measurements concern CUDA. The `all` extra includes instruments and
contours; language-model installation remains an explicit `alignment` choice.

## Analyze and search

```powershell
# Inspect streams first; the indices below are examples.
.\.venv\Scripts\python.exe -m avevidence probe "episode.mkv" "runs/probe-01"
.\.venv\Scripts\python.exe -m avevidence performance "episode.mkv" "runs/performance-01" --cache-dir "cache" --audio-stream 1 --subtitle-stream 10 --model-dir "models/Qwen3-ForcedAligner-0.6B"
.\.venv\Scripts\python.exe -m avevidence verify "runs/performance-01" --verify-sources
.\.venv\Scripts\python.exe -m avevidence performance-index "runs/performance-01" "evidence.sqlite" --label "Episode 01"
.\.venv\Scripts\python.exe -m avevidence performance-search "evidence.sqlite" "先輩" --min-pitch 250 --limit 20
```

Every published output directory must be new. Reuse the same cache directory for
another run. It verifies stored hashes and reuses matching audio, contours and
alignments. Cache identity includes source bytes, stream, channel, timing policy,
parameters, model/configuration hashes, relevant package versions and implementation
hashes. Cache corruption stops reuse. Runtime failures remain retryable; structurally
rejected model timings are cached as `REJECTED_TIMING`, never usable word boundaries.

`performance-index` verifies artifact hashes before indexing. Identical evidence
reruns replace the indexed location rather than multiplying counts. Different
configurations remain separate entries. Search uses literal, normalized Japanese
substrings, with FTS5 trigram acceleration for queries of at least three characters
when the Python SQLite build supports it; shorter queries also work. Speaker filters
match supplied labels exactly. Numeric filters operate on mixed-audio cue summaries,
not isolated actors. Search checks manifest identity; run `ave verify` before relying
on referenced files after they have been moved or edited.

For a clip or still from a result, use the existing `clip-av`, `clip-audio` and `frames`
commands with its original source file, selected stream and source-relative start/end.
An explicit timing tolerance in a performance run does not change the defaults of
those older commands. Keep the selected evidence small when passing it to a cloud
assistant, and describe supplied text, inspected images and actual listening separately.

## Transcript interchange

Replace `--subtitle-stream` with either `--subtitle-run DIRECTORY` or
`--transcript transcript.json`. Only one text input is allowed. Example:

```json
{
  "source_sha256": "SHA256_OF_THIS_EXACT_MEDIA_FILE",
  "clock": "original_pts_minus_source_origin",
  "text_authority": "onscreen_text_not_verified_speech",
  "utterances": [
    {"utterance_id": "line-001", "start_s": 12.0, "end_s": 14.5,
     "speaker": "displayed character label", "text": "こちらです。"},
    {"utterance_id": "reaction-002", "start_s": 14.5, "end_s": 16.0,
     "text": "（悲鳴）", "kind": "non_speech_caption"}
  ]
}
```

Hash conflicts and incompatible clocks are refused. Omitting a source hash is
accepted for legacy transcripts, but the relationship is then explicitly
operator-supplied, not independently verified. Raw supplied text/times are retained.
SDH name prefixes and simple furigana are removed only in `alignment_text`.
Standalone parenthesized captions and punctuation/music-only text are skipped.
Override `alignment_text` and `kind` when a caption needs deliberate correction.
Labels are not inherited across unlabeled captions. Multiple labels and touching
duplicate subtitle text are flagged. Such overlapping captions may need human
segmentation; this version does not claim to solve them automatically.

## Clocks, channels and limits

Source time is original PTS minus source origin. PCM stays at native rate until the
bounded model input is resampled to 16 kHz. Compact audio does not fill source gaps
with silence. RMS windows never cross those gaps. Cues crossing a gap or exceeding
30 seconds of padded model input are skipped with a reason. Whole sources are bounded
to two hours by default; use `--max-duration` deliberately for longer recordings.

The default arithmetic channel mean is an explicitly recorded float32 analysis
derivative, not speaker separation. Antiphase stereo can cancel; use `--channel 0`
or another zero-based index when appropriate. No normalization is applied. The
default 75–600 Hz range may miss high screams or low voices; use `--pitch-floor` and
`--pitch-ceiling` deliberately and compare within the same settings. Pitch tracking
can follow music or another speaker. RMS dBFS includes music/effects and is not
calibrated loudness, effort, microphone level, or an emotion label.

Normally the native decoder joins timestamps only within one source clock tick.
The tested Yani Neko episode has measured timestamp offsets of -2.0 to +0.333 ms
relative to its contiguous sample clock. It therefore requires the explicit
`--timestamp-tolerance-ms 2` for this analysis. The actual maximum adjustment and
policy are saved in the result. The option is bounded to 10 ms, never changes sample
rate or audio samples, and does not override default retiming protections elsewhere.
Do not select a tolerance merely to hide an unexplained clock discrepancy.

Unvoiced/unavailable pitch is NaN in the numerical NPZ file and null in JSON summaries.
The plotted display uses 50 ms samples; the complete 10 ms tracks and integer sample
bounds remain in `contours.npz`. RMS is floored at -240 dBFS. Search uses
`rms_dbfs_energy_mean`: average linear window power, then convert to dBFS. This avoids
giving near-silent gaps disproportionate weight by averaging their logarithms.
The arithmetic mean of log levels is retained separately as `rms_dbfs_mean`.
Neither is an exact whole-utterance RMS, because the windows overlap. The summary uses the accepted
candidate alignment span when available and otherwise the supplied cue window.

## Storage and batch operation

The dominant cache is float32 mono audio: about 691 MB per hour at 48 kHz, plus much
smaller contours and alignment records. The original compressed media remains the
reference. Allow scratch space for native float64 audio during first decode
(about 2.8 GB/hour for stereo 48 kHz). Scratch PCM is removed after caching.

The cache is disposable; preserve result directories, original media and transcripts
before deciding to remove a cache. Do not edit individual cached files. Use a new
cache directory for a deliberate cold benchmark. This first version runs sequentially;
one worker per cache is recommended. Concurrent jobs for the exact same cache entry
fail safely rather than publishing competing entries. GPU out-of-memory failures are
reported and can be retried; no cloud fallback or paid service is invoked.

`scripts/benchmark_performance.py CONFIG NEW_DIRECTORY` runs cold/warm jobs in fresh
processes and saves complete wall times and stage times. The local config and game
transcript in `benchmarks/` are local-only examples tied to this machine. Portable
input templates are in `examples/performance-benchmark.json` and `examples/performance-transcript.json`.
The benchmark's cold phase means an empty toolkit cache, not a flushed operating-system
disk cache. Setup/downloads and manual text preparation are excluded.

## Analytical scope

Use these outputs to ask where a voice rises/falls, where pauses or reactions merit
inspection, and how selected passages differ under comparable conditions. Retrieve
original audio and neighboring frames before interpreting delivery, timing of a joke,
or a character interaction. Forced alignment conditions on supplied text and can align
incorrect text plausibly. No ASR, speaker diarization, source separation, emotion
classification, or new human listening is established by the pipeline.

The local game example uses visually checked on-screen text and the owner's earlier
approximate listening notes. No new manually verified word-boundary gold set exists.
This release demonstrates computation, retrieval and throughput; perceptual accuracy
still needs a small human-reviewed set before corpus-wide interpretive claims.
