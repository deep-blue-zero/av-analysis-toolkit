"""Source-timed pitch and digital RMS tracks, using bounded native-clock blocks."""
from __future__ import annotations

from fractions import Fraction
import math
from pathlib import Path

from .audio import _decode, _identity, _scratch
from .audio_tracks import _validate_segments
from .common import AVError, read_json, run, write_json
from .performance_cache import cache_get, code_identity, versions


def prepare_audio(source, stream, cache_root, channel="mean", timestamp_tolerance_ms=None,
                  *, coverage_end=None, max_output_bytes=None, decode_timeout_s=180):
    import numpy as np

    count = int(stream.get("channels", 0))
    channel = {"mono_mix": "mean", "left": "0", "right": "1"}.get(str(channel), str(channel))
    if channel.startswith("channel:"):
        channel = channel.split(":", 1)[1]
    if channel != "mean":
        try:
            index = int(channel)
        except (ValueError, TypeError) as exc:
            raise AVError("Channel must be 'mean' or a zero-based channel index") from exc
        if not 0 <= index < count:
            raise AVError("Selected channel is outside the audio stream")
        channel = str(index)
    key_data = {"schema": "mono-cache-v1", "source_sha256": source["sha256"],
                "audio_stream": stream["index"], "channel": channel,
                "explicit_timestamp_tolerance_ms": timestamp_tolerance_ms,
                "coverage_end": coverage_end, "max_output_bytes": max_output_bytes,
                "decode_timeout_s": decode_timeout_s,
                "ffmpeg": run(["ffmpeg", "-version"]).stdout.splitlines()[0],
                "numpy": versions("numpy"),
                "code": code_identity("audio.py", "common.py", "performance_contours.py")}

    def build(stage):
        with _scratch(stage) as scratch:
            decoded = _decode(source, stream, scratch, "native", timestamp_tolerance_seconds=(
                timestamp_tolerance_ms / 1000 if timestamp_tolerance_ms is not None else None),
                start=0 if coverage_end is not None else None, end=coverage_end,
                max_output_bytes=max_output_bytes, timeout_s=decode_timeout_s)
            _validate_segments(decoded)
            raw = np.memmap(decoded["raw_path"], dtype="<f8", mode="r",
                            shape=(decoded["sample_frames"], count))
            mono = np.lib.format.open_memmap(stage / "mono.npy", mode="w+", dtype="<f4",
                                            shape=(decoded["sample_frames"],))
            block = values = None
            try:
                for first in range(0, len(raw), 1048576):
                    block = raw[first:first + 1048576]
                    if not np.isfinite(block).all():
                        raise AVError("Nonfinite decoded audio")
                    values = block.mean(axis=1) if channel == "mean" else block[:, int(channel)]
                    mono[first:first + len(block)] = values
                mono.flush()
            finally:
                del block, values, mono, raw
            metadata = {"identity": _identity(decoded), "segments": decoded["segments"],
                        "source_sha256": source["sha256"], "stream_index": stream["index"],
                        "transform": {"channel": channel, "dtype": "float32",
                            "meaning": "Arithmetic channel mean" if channel == "mean" else "Selected source channel",
                            "normalization": False, "resampling": False,
                            "is_isolated_speaker": False},
                        "clock": "original_pts_minus_source_origin"}
            metadata["decode_scope"] = decoded["decode_scope"]
            write_json(stage / "audio.json", metadata)
    path, hit, key = cache_get(cache_root, "audio", key_data, build)
    return path, read_json(path / "audio.json"), hit, key


def compute_contours(samples, metadata, *, hop_ms=10., window_ms=40., floor=75., ceiling=600., block_s=30.):
    import numpy as np
    import parselmouth

    values = (hop_ms, window_ms, floor, ceiling, block_s)
    if not all(math.isfinite(float(x)) and x > 0 for x in values):
        raise AVError("Contour parameters must be finite and positive")
    if not (20 <= floor < ceiling <= 2000 and hop_ms <= window_ms <= 1000 and block_s >= 1):
        raise AVError("Invalid pitch range, contour window, hop or block duration")
    rate = metadata["identity"]["sample_rate_hz"]
    hop, window = max(1, round(rate * hop_ms / 1000)), max(2, round(rate * window_ms / 1000))
    margin = math.ceil(rate * max(.15, 3 / floor))
    block_n = max(hop, round(rate * block_s) // hop * hop)
    columns = {k: [] for k in ("sample_center", "sample_start", "sample_end", "segment_id",
                               "time_s", "pitch_hz", "pitch_strength", "rms_dbfs")}
    for segment_id, segment in enumerate(metadata["segments"]):
        a, b = segment["sample_start"], segment["sample_end"]
        base = Fraction(str(segment["source_start_seconds"]))
        for first in range(a, b, block_n):
            last = min(first + block_n, b)
            centers = np.arange(first, last, hop, dtype=np.int64)
            centers = centers[(centers - window // 2 >= a) & (centers + (window - window // 2) <= b)]
            if not len(centers):
                continue
            lo, hi = max(a, first - margin), min(b, last + margin)
            signal = np.asarray(samples[lo:hi], dtype=np.float64)
            # RMS windows never cross a timestamp gap. No per-line normalization.
            cumulative = np.r_[0., np.cumsum(signal * signal)]
            starts, ends = centers - window // 2, centers + (window - window // 2)
            rms = np.sqrt(np.maximum(0., (cumulative[ends-lo] - cumulative[starts-lo]) / window))
            f0 = np.full(len(centers), np.nan)
            strength = np.full(len(centers), np.nan)
            if len(signal) / rate >= 3 / floor:
                pitch = parselmouth.Sound(signal, sampling_frequency=rate).to_pitch_ac(
                    time_step=hop / rate, pitch_floor=floor, pitch_ceiling=ceiling)
                times = pitch.xs()
                if len(times):
                    indices = np.rint(((centers - lo) / rate - times[0]) / pitch.dx).astype(int)
                    valid = (indices >= 0) & (indices < len(times))
                    f = pitch.selected_array["frequency"][indices[valid]]
                    f0[valid] = np.where(f > 0, f, np.nan)
                    strength[valid] = pitch.selected_array["strength"][indices[valid]]
            columns["sample_center"].append(centers)
            columns["sample_start"].append(starts)
            columns["sample_end"].append(ends)
            columns["segment_id"].append(np.full(len(centers), segment_id, dtype=np.int32))
            columns["time_s"].append(float(base) + (centers - a) / rate)
            columns["pitch_hz"].append(f0)
            columns["pitch_strength"].append(strength)
            columns["rms_dbfs"].append(20 * np.log10(np.maximum(rms, 1e-12)))
    return {k: np.concatenate(v) if v else np.array([], dtype=np.float64) for k, v in columns.items()}


def get_contours(audio_path, metadata, audio_key, cache_root, *, hop_ms=10., window_ms=40., floor=75., ceiling=600.):
    import numpy as np
    config = {"hop_ms": float(hop_ms), "window_ms": float(window_ms),
              "floor": float(floor), "ceiling": float(ceiling)}
    key_data = {"schema": "contours-v1", "audio_key": audio_key, "config": config,
                "versions": versions("numpy", "praat-parselmouth"),
                "code": code_identity("performance_contours.py")}
    def build(stage):
        samples = np.load(Path(audio_path) / "mono.npy", mmap_mode="r", allow_pickle=False)
        try:
            tracks = compute_contours(samples, metadata, **config)
        finally:
            del samples
        np.savez_compressed(stage / "contours.npz", **tracks)
        write_json(stage / "contours.json", {"schema": "ave.performance-contours.v1",
            "audio_cache_key": audio_key, "configuration": config,
            "row_count": len(tracks["time_s"]), "clock": metadata["clock"],
            "segments": metadata["segments"], "sample_rate_hz": metadata["identity"]["sample_rate_hz"],
            "units": {"pitch_hz": "Candidate mixed-signal F0 in Hz; NaN means unvoiced/unavailable",
                      "pitch_strength": "Praat candidate correlation, not calibrated confidence",
                      "rms_dbfs": "Digital RMS in dBFS; includes background; floor -240 dBFS"},
            "limitations": ["No speaker isolation, emotion inference or direct listening",
                "Arithmetic downmix can cancel antiphase channels; select a source channel when needed",
                "Window-edge centers are omitted; source gaps are not padded or measured",
                "Sample bounds describe RMS windows; pitch uses Praat's longer analysis window"],
            "epistemic_class": "COMPUTED_SIGNAL_EVIDENCE"})
    return cache_get(cache_root, "contours", key_data, build)
