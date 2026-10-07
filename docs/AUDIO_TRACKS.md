# Audio track methods, fields and limits

## Source and clock

Canonical native-rate interleaved f64 PCM comes from the existing decoder unchanged.
The clock is `original_pts_minus_source_origin`. The canonical sample file is
compact: its indices are not a global wall clock when source timestamps contain
gaps. Each row therefore carries both input_sample_start/end (half-open) and
source_start/end/center_seconds. Gaps start new segments and reset all temporal
state. Timestamp tolerance inherited from the source/parent run is recorded in
identity; 10 ms hops are NOT a claim of 10 ms source-clock or perceptual precision.
Source-clock times are stored using the existing toolkit's floating-second
representation plus exact sample integers, not a newly invented exact rational
clock. Full f64 decoding does not add information to compressed sources.

## Temporal and spectral tracks

General uses 40 ms windows, speech 64 ms, music 2048/22050 seconds (~92.88 ms).
All default to 10 ms hops with extra 20/80 ms windows. Values are rounded to
native sample integers, with a minimum four-sample FFT. Window is symmetric Hann;
center=False; there is no zero padding or waveform resampling. Every scale must
be at least the hop length. Short/tail data is unavailable for full-frame spectra,
not invented. Per-segment summaries and waveform extrema still use all samples.

- RMS, sample peak, min/max, DC and ZCR use raw unwindowed samples.
- dBFS = 20 log10(amplitude), digital full scale 1.0. Digital zero is null.
- Crest factor dB = 20 log10(sample peak/RMS); not a measured true-peak crest.
- ZCR = adjacent sign-bit changes/(N-1); not speech or breath detection.
- Single-sided FFT amplitude uses |rFFT(Hann*x)|/sum(Hann), doubling interior bins.
- Centroid, bandwidth (weighted standard deviation) and 85% rolloff use magnitude.
- Flatness uses geometric mean power/arithmetic mean power, with machine-tiny
  protection inside logs only. Entropy is Shannon entropy of power / log(bin count).
- spectral_flux_l1 sums positive differences of L1-normalized amplitude spectra;
  onset_strength_amplitude sums positive UNNORMALIZED amplitude differences.
  First values per segment are null. Difference support starts at the preceding
  window's start, not just the current frame center.
- low_energy uses a declared RMS threshold (default -45 dBFS); it is NOT VAD,
  silence, breath, hesitation, speech rate, or absence of accompaniment.

These definitions are toolkit-owned, not a promise of numerical identity with
librosa/Essentia/FFmpeg similarly named functions. Sparse or coarse FFTs can give
unstable low-frequency descriptors. Music/voice claims require other evidence.

## Numerical and rendered representations

STFT overviews max-pool calibrated amplitude into at most 384 frequency bins x
1200 time bins PER segment/channel/scale. NPZ carries low/high frequency bins and
source support start/end per time column. Pooling retains maxima but cannot resolve
multiple events within a bin. It must not be used to estimate integrated energy.

Primary-scale log-mel overviews use 64 triangular HTK bands between zero/Nyquist:
mel(f)=2595 log10(1+f/700). Each sampled filter is divided by its discrete sum; power
is averaged by those weights, then max pooled in time. Empty filters are flagged.
This is not a drop-in learned-model input or the librosa Slaney filter convention.
No audio resampling/normalization occurs. PNGs show -120..0 dB (expanded upward
only for above-full-scale samples). Floors apply only to display, never raw data.

Plots use Pillow and bundled default drawing support, with no distributed font
files. They are single-chart images. Source gaps/nulls break line graphs. Waveform
extrema pool min/max by display column rather than decimating away transient peaks.
A picture is an inspection aid, not auditory exposure or continuous AV review.

## Stereo

Automatic mode requires a source-declared `stereo` layout and two channels.
An explicit channels-0-1 mode records an operator assertion, not a verified layout.
M=(L+R)/2, S=(L-R)/2; both are algebraic analysis views, not isolated voices/stems.
side_energy_fraction=E(S)/(E(M)+E(S)); 0 for identical channels, 1 for antiphase.
Pearson correlation is mean-centered and undefined for constant/silent channels.
right_minus_left_db=10 log10(E(R)/E(L)), null if either energy is zero.
The energy identity E(M)+E(S)=(E(L)+E(R))/2 is also checked numerically.
These are spatial/mix descriptors, not a calibrated psychoacoustic width metric.
No phase-based causal attribution, channel-delay estimate or vocal isolation is made.

## Loudness

Use installed FFmpeg ebur128=metadata=1:peak=true followed by ashowinfo and ametadata.
Raw metadata and stderr are retained. Native-rate filter-frame PTS and sample counts
bind meter outputs to the end of each output block. The nominal cadence is 100 ms;
actual sample counts are authoritative. M uses 400 ms history, S uses 3 seconds.
Warm-up values are null. Integrated loudness and LRA reset at each selected segment,
so they are not necessarily whole-song/episode values. Historical filter support
before the selection is not assumed. No meter bridges a missing timestamp interval.

FFmpeg metadata reports true peak as a running maximum, rounded in LINEAR amplitude
to three decimals. Converting that to dB is consequently coarse, particularly at
low levels; null may reflect rounding to zero. It is NOT a block-local true peak.
Use the original audio and raw logs for follow-up. Very quiet floor/gate sentinels
are null; raw strings survive. LRA for short clips is provisional regardless of
whether FFmpeg emits a number. A final incomplete meter block is counted explicitly.

## Elementary music

Music profile folds FFT-bin power into nearest 12-tone equal-tempered pitch classes
(A4=440, 50-8000 Hz capped at Nyquist), normalizing nonzero vectors to sum 1. Low FFT
resolution, detuning, transients, distortion and mixtures limit interpretation.
Chroma is not melody, a chord label or instrument transcription.

Onset candidates: local maxima of positive amplitude flux above median + max(3*MAD,
10% of peak), with strongest-first 80 ms nonmaximum suppression. Event support spans
previous+current analysis windows. Start/end boundary onsets can be missed. No onset
has a calibrated probability, instrument identity or sample-exact physical timing.

Tempo: requires at least 4 onset candidates and >=4 seconds. FFT autocorrelation of
mean-centered onset strength yields positive local-max lag hypotheses for 40-240 BPM;
report at most 3 with normalized autocorrelation >0.1. Half/double-time alternatives
remain. This does NOT track beats/downbeats, meter or variable tempo.

## Compatibility and resource limits

audio-track and render-audio are additive commands. Existing audio-metrics,
cue-features, frame/source clocks, clips, review-check and archive behavior remain.
In particular the older audio-metrics volumedetect output stays as-is for compatibility;
the new track summaries use direct f64 RMS/peak. The legacy timeline --audio-run
still expects cue-features, not a new audio-track run. Share via common source clock
and artifact handles; automatic interval joining is deferred rather than guessed.

Full-source decode precedes bounded analysis. Do not repeatedly point a 20-second
query at a huge episode when a verified clip-audio run can be reused. A disk-space
estimate and row/span caps prevent common mistakes; they are not operating-system
resource quotas. Scratch uses a memory map and FFT batches of 64 windows. Rows for
one track and waveform/navigation data remain in memory within declared caps.

## Primary technical references (checked 2026-09-13)

- FFmpeg filters (ebur128/ashowinfo/ametadata): https://ffmpeg.org/ffmpeg-filters.html
- Meter state, metadata rounding and cumulative peaks:
  https://ffmpeg.org/doxygen/trunk/f__ebur128_8c_source.html
- Spectral flatness reference (not the implementation used here):
  https://librosa.org/doc/0.11.0/generated/librosa.feature.spectral_flatness.html
- Spectral-flux onset reference (not identical to this detector):
  https://librosa.org/doc/0.11.0/generated/librosa.onset.onset_strength.html

Runtime qualification depends on the recorded installed versions and tests, not
these documentation links alone.
