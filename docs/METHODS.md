# Methods and limits

## Source identity and clocks

Each admitted source is hashed and probed. Canonical source time is original presentation PTS minus `origin_seconds`. Requested times, original PTS, derivative times, sample indices, packet boundaries, and inferred extents are never interchangeable without an explicit mapping.

Container duration is retained separately from canonical duration. MOV/MP4 declared presentation intervals and intrinsic lossless-audio sample clocks are used where defined; otherwise packet PTS plus positive packet duration are scanned. Incomplete endpoints remain unavailable rather than guessed.

## Video frame indexes and bounded extraction

`frame-index` records every decoded source frame's original integer PTS, time base, source-relative time, decoded duration when exposed, geometry, source hash, and stream index. It is an immutable explicit cache, not a hidden home-directory database.

A consumer verifies the frame-index run, source hash, absolute stream index, geometry, origin, and time base. Bounded extraction selects the expected original PTS values. It may seek near the earliest target to avoid decoding from the beginning, but success requires exact recovery of the expected PTS set. If seeking fails, one full-decode fallback is permitted and recorded.

Exact mode means the first real source frame at or after a requested instant. It does not create a frame at an off-grid time. Dense scheduling rates do not replace actual source timestamps.

HDR, wide-gamut, high-bit-depth, display-matrix rotation, and non-square-pixel transformations remain refused unless explicitly normalized outside this workflow.

## Review-clip frame extents

FFmpeg 7.1 may expose decoded frame PTS without `duration_time`. Video-frame extent resolution therefore uses:

1. positive explicit decoded duration;
2. next decoded PTS delta;
3. verified selected-stream endpoint for the final frame;
4. previous PTS delta as an explicitly marked final-frame estimate.

Non-increasing timestamps, unexplained overlap, and non-positive extents fail. Review mappings record method counts and whether estimation was needed. A source frame may cross a requested endpoint; requested clipping bounds are not presented as arbitrary frame-exact cuts.

## Audio representation and storage

Audio is decoded for verification at native rate and channel order into float64 samples. Continuous decoded segments map sample indices to source time; gaps remain unavailable rather than synthetic silence.

Persistent storage is separate from verification representation:

- `practical` copies/remuxes the native selected codec stream. A copied AAC/MP3 source remains lossy.
- `forensic` persists float64 WAV and verifies exact decoded sample/rate/channel identity.
- `verified-flac` accepts FLAC only when re-decoding yields exact f64 sample/rate/channel identity.

Stream copy does not by itself establish encoded-packet identity, timing equivalence, or AV synchronization. Reports state which predicate was actually tested.

## Signal measurements

Loudness, sample peak, RMS, spectral centroid, low-energy fractions, zero crossings, and estimated F0 describe a signal under a declared method. They are not speaker identity, emotion labels, or human-like listening.

pYIN uses a toolkit-owned explicit parameter dictionary. Reports record only the parameters actually passed and JSON-native representations. Optional pitch failure is recorded as a result rather than corrupting the run.

Mixed-program measurements can include music, effects, overlapping speakers, reverberation, and mastering. Subtitle boundaries are approximate windows, not validated diarization.

## External clip admission

`admit-external-clip` fingerprints encoded packet payloads for selected parent and derivative streams. A unique contiguous candidate packet-hash sequence in the parent establishes packet-subsequence identity for that modality. Actual packet PTS/durations generate parent/derivative coverage mappings.

A stream-copy clip may begin before the user-requested start because video must include an earlier keyframe or audio packet. The report preserves actual mapped boundaries and the delta from the claimed interval. No match, repeated identical sequences, codec mismatch, or incomplete evidence remains non-verified. A unique packet match with a user-declared interval outside the actual packet coverage retains the packet-identity result but is not an overall successful admission.

Packet-subsequence identity does not prove:

- equal container metadata;
- identical edit lists;
- decoded equivalence outside the mapped packets;
- matching unselected streams;
- complete AV synchronization;
- perceptual equivalence;
- interpretive correctness.

## Provenance and lifecycle

Every successful operation writes an immutable run manifest with source records, commands, parameters, environment identity, implementation hashes, artifact hashes, and operation metadata. Failures remain in marked staging directories and are never published under the requested output name.

Contact sheets and dependent operations consume verified current manifests rather than inferring evidence from directory contents. Archives are deterministic and allowlisted.

## Perception and interpretation

Use distinct evidence labels:

- `direct_text_source`
- `direct_visual_source_frame`
- `direct_visual_sequence`
- `direct_signal_measurement`
- `audiovisual_temporal_inference`
- `interpretive_inference`
- `direct_auditory_perception` only when an actual verified audio-input/playback mechanism exists

A numeric result can be direct signal evidence without being a listened-to sound. A generated image becomes inspected visual evidence only after presentation to a capable reviewer. The review validator checks declarations and mappings; it cannot prove attention, honesty, or analytical quality.
