# Audio Instruments quickstart

## Install on Windows or Linux

Use a fresh environment rather than changing an existing model environment.
FFmpeg and ffprobe 7.1+ must be available on PATH. No GPU is used.

```powershell
python -m venv .venv
# Windows: no activation script or execution-policy change is needed.
.\.venv\Scripts\python.exe -m pip install ".[instruments]"
.\.venv\Scripts\python.exe -m avevidence doctor
```

On Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
All examples below use `python`; use the appropriate environment's interpreter.
The `audio` extra is still available for the pre-existing librosa/pYIN workflow.

## Analyze a performance clip

```powershell
python -m avevidence probe "performance.mkv" "runs/source-01"
python -m avevidence audio-track "performance.mkv" "runs/audio-01" --profile music
python -m avevidence verify "runs/audio-01"
python -m avevidence pack "runs/audio-01" "audio-01-evidence.zip"
```

The command refuses ambiguous streams. Read the probe and supply the absolute
index with `--audio-stream 1`, for example, only when that is the intended track.
It does not select an English dub, commentary track, or language automatically.

A bounded passage can use `--start 12.5 --end 37.5`. These are source-clock seconds,
not arbitrary positions in a remuxed file. Both endpoint indices use sample-onset
half-open selection. The default limit is 300 seconds of analyzed source span.

For repeated inspection of an episode, avoid repeatedly decoding all of it:

```powershell
# Set these after checking the actual source; no GBC timestamps are assumed.
$Source = "H:\Media\Girls Band Cry\Episode 11.mkv"
$Start = Read-Host "Verified performance start in source seconds"
$End = Read-Host "Verified performance end in source seconds"
python -m avevidence clip-audio $Source "runs/gbc-e11-audio-clip" --start $Start --end $End
python -m avevidence audio-track "runs/gbc-e11-audio-clip" "runs/gbc-e11-audio-01" --profile music
```

Pass the **run directory**, not a bare copied WAV, to retain its parent episode
identity, original time offset and timestamp uncertainty. `clip-audio` itself
currently decodes the entire source once; follow-up track runs decode only its
sample-preserving derivative. Keep the run manifest and audio.json with the WAV.
For a 20-30 second sentinel, choose the actual passage yourself; do not treat
these examples or synthetic fixtures as selections from the real episode.

## Read the output

Start with `README.md` and `audio_tracks.json` in the result. `renders/` contains
PNG plots. `tracks_*.csv` and `.jsonl` have every measured frame; `*_spectrum.npz`
holds bounded linear/mel overviews with time/frequency support arrays.
`waveform.csv` retains 10 ms extrema including incomplete tails. `stereo.csv`
exists only when applicable. `loudness.csv` exists when EBU measurement works.
`audio_events.json` contains estimated change events, not beat/note annotations.

Original audio remains the witness for listening. Do not assign full-mix peaks,
flux or pitch-class energy to Nina, a guitar, or a drum just because one is visible.

## Control detail and cost

```powershell
# One 40 ms spectrum at 10 ms hops; no secondary spectra or plots.
python -m avevidence audio-track "clip.wav" "runs/fast-01" --window-ms 40 --hop-ms 10 --spectral-scales-ms --no-render

# Dense temporal steps, with 20/80 ms secondary windows.
python -m avevidence audio-track "clip.wav" "runs/dense-01" --window-ms 40 --hop-ms 5 --spectral-scales-ms 20 80

# Render again without decoding or re-running DSP.
python -m avevidence render-audio "runs/fast-01" "runs/fast-plots-01"
```

`--loudness require` refuses a run without valid EBU processing. Default `auto`
records a reason when unsupported or channel layout is unknown. `off` disables it.
`--stereo channels-0-1` permits pair algebra for an unknown two-channel source but
labels it operator-declared; it does not authorize guessed EBU channel weighting.
Do not use it to pretend an arbitrary dual-mono language pair is a stereo mix.

## Verify on the remote Windows host

```powershell
python scripts/self_test.py --report-dir "C:\AVPW-validation\audio-rc1-regression"
python scripts/audio_smoke_test.py "C:\AVPW-validation\audio-rc1-smoke"
```

Both destinations must be new. Run from the toolkit directory. These are synthetic
instrument tests; they do not load models, modify GPU drivers, contact AWS, upload
files or validate actual listening. See IMPLEMENTATION_HANDOFF.md before extending.
