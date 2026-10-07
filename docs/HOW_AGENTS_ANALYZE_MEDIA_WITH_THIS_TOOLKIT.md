# How agents analyze media with this toolkit

## The capability question

An execution environment may lack a conventional VLC-like interface and still be able to perform substantial audiovisual analysis. The useful question is not merely "Can you play this file?" It is:

- Can the environment read the media bytes?
- Can it run ffprobe/FFmpeg?
- Can it extract and inspect source frames?
- Can it densify a short interval or retain every source frame?
- Can it decode and measure audio samples?
- Can it align subtitle, frame, packet, and signal clocks?
- Does the model have an actual image or audio presentation interface?

## Video workflow

The toolkit can:

1. hash and probe the source;
2. build a complete original-PTS frame index once;
3. select exact real frames for a question;
4. seek near late intervals to avoid repeated full decoding;
5. extract sparse, dense, or every-source-frame sequences;
6. create contact sheets for navigation;
7. bind every image to source hash, stream, PTS, and time base.

A model with image vision can directly inspect those extracted source frames. A short sequence at 5-10 fps—or every source frame—can support real motion reconstruction: gaze changes, gesture order, blocking, camera cuts, posture transitions, and synchronization with subtitle or audio events.

This is not identical to unconstrained continuous playback, so the analyst must state the sampling density and any motion claim that remains underdetermined.

## Audio workflow

The toolkit can decode the actual selected audio stream and directly measure:

- sample timing and gaps;
- loudness and peaks;
- RMS/intensity contours;
- spectral distributions;
- zero crossings;
- qualified F0 estimates;
- packet identity;
- alignment with subtitle and video times.

Those are direct signal measurements. They do not automatically mean the model heard timbre, emotion, irony, affection, or embarrassment in a human-like way.

When the execution environment genuinely presents audio to a capable human or model, record that capability and presentation separately. Otherwise write conclusions as signal-supported audiovisual inference, not "I listened and heard...".

## Synchronized audiovisual analysis

LosslessCut clips that retain source streams are especially useful. After admission, the analyst can align:

```text
Japanese dialogue cue
+ actual video-frame PTS sequence
+ audio packet/sample timing
+ measured intensity/pitch/spectral events
+ inspected posture/gaze/blocking
```

This permits defensible questions such as:

- Did the glance occur before or after the line ended?
- Did a cut land on a lyric boundary or musical onset?
- Did the speaker turn toward another character during the phrase?
- Did measured vocal intensity rise while posture contracted?

The final emotional or literary conclusion remains an inference whose support should be named.

## What an agent should not do

Do not declare audiovisual analysis impossible solely because there is no media-player UI. Build the frame/signal evidence first.

Do not claim direct listening solely because librosa, FFmpeg, or a waveform exists.

Do not treat sparse contact sheets as continuous motion.

Do not infer speaker identity from subtitle labels or mixed-program pitch.

Do not treat packet identity as perceptual equivalence.

Do not fill review receipts with invented observations.

## A useful response to a capability refusal

A capable agent can say:

> I cannot certify unrestricted human-like continuous listening/viewing from the current interface. I can still probe the source, build a verified frame index, inspect timestamped source frames and dense sequences, decode and measure the selected audio signal, admit stream-copy clips, align the evidence, and clearly distinguish measurement from perception and interpretation.

That is both more useful and more honest than either categorical refusal or overclaiming.
