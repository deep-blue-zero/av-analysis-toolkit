"""Bounded native-quality listening witnesses with visible channel transformations."""
from __future__ import annotations

from pathlib import Path

from .common import AVError, finite, finish_run, interval, output_transaction, probe_source, select_stream, sha256, write_json


def write_witness(source, stream, stage, *, start, end, channel="preserve", timestamp_tolerance_ms=None):
    import numpy as np
    from .audio import _decode, _encode_wav, _identity, _scratch, _selection
    a, b = interval(start, end, source["duration_seconds"])
    if b-a > 120:
        raise AVError("Audio witnesses must be at most 120 s")
    strategy = {"mean": "mono_mix", "left": "channel:0", "right": "channel:1"}.get(channel, channel)
    count = int(stream.get("channels", 0))
    selected = None
    if strategy not in {"preserve", "mono_mix"}:
        if not strategy.startswith("channel:") or not strategy.split(":", 1)[1].isdigit():
            raise AVError("Channel strategy must be preserve, mono_mix, left, right or channel:N")
        selected = int(strategy.split(":", 1)[1])
        if not 0 <= selected < count:
            raise AVError("Requested audio channel is absent")
    with _scratch(stage) as scratch:
        decoded = _decode(source, stream, scratch, "native", start=a, end=b,
            max_output_bytes=536870912, timeout_s=120,
            timestamp_tolerance_seconds=None if timestamp_tolerance_ms is None else timestamp_tolerance_ms/1000)
        selection = _selection(decoded, a, b)
        if selection["status"] != "COMPLETE" or len(decoded["segments"]) != 1:
            raise AVError("Listening witness crosses missing coverage; gap concatenation is refused")
        raw = np.memmap(decoded["raw_path"], mode="r", dtype="<f8", shape=(decoded["sample_frames"], count))
        if not np.isfinite(raw).all():
            raise AVError("Nonfinite listening samples")
        powers = np.mean(raw*raw, axis=0)
        mean_power = float(np.mean(raw.mean(axis=1)**2))
        original = _identity(decoded)
        if strategy != "preserve":
            values = raw.mean(axis=1) if selected is None else raw[:, selected]
            transformed = scratch / "channel.f64le"
            values.astype("<f8").tofile(transformed)
            decoded = dict(decoded, raw_path=transformed, channels=1, channel_layout="mono",
                pcm_bytes=transformed.stat().st_size, pcm_sha256=sha256(transformed))
            del values
        del raw
        destination = Path(stage) / "audio.wav"
        _encode_wav(decoded, destination)
        # Decode the witness again: sample preservation applies to the declared
        # channel transform, not falsely to the original multichannel stream.
        derivative = probe_source(destination)
        check = _decode(derivative, select_stream(derivative, "audio"), scratch, "check")
        if any(check[k] != decoded[k] for k in ("pcm_sha256", "sample_frames", "sample_rate_hz", "channels")):
            raise AVError("Native listening witness failed decoded sample verification")
        seg = decoded["segments"][0]
        mapping = {"modality": "audio", "parent_source_sha256": source["sha256"], "parent_stream_index": stream["index"],
            "artifact_path": "audio.wav", "artifact_sha256": sha256(destination), "derivative_stream_index": 0,
            "segments": [{"parent_start_seconds": seg["source_start_seconds"], "parent_end_seconds": seg["source_end_seconds"],
                          "derivative_start_seconds": 0., "derivative_end_seconds": decoded["sample_frames"]/decoded["sample_rate_hz"]}]}
        return {"schema": "ave.audio-witness.v1", "source_sha256": source["sha256"], "stream_index": stream["index"],
            "source_interval_seconds": [a,b], "source_identity": original, "identity": _identity(decoded),
            "artifact_sha256": sha256(destination), "artifact_path": "audio.wav", "review_mapping": mapping,
            "transformations": [{"channel_strategy": strategy, "resampling": False, "normalization": False}],
            "channel_inspection": {"native_channel_mean_square": powers.tolist(), "mono_mix_mean_square": mean_power,
                "cancellation_warning": bool(mean_power < max(float(np.mean(powers)), 1e-24)*.1)},
            "fidelity": "Native decoded sample preservation after the explicitly declared channel transform",
            "clean_voice": "NOT_ESTABLISHED_BY_LOSSLESS_FORMAT", "perceptual_review": "NOT_PERFORMED"}


def audio_witness(input, output, *, start, end, stream_index=None, channel="preserve", timestamp_tolerance_ms=None):
    source = probe_source(input)
    stream = select_stream(source, "audio", stream_index)
    with output_transaction(output, [input]) as stage:
        report = write_witness(source, stream, stage, start=start, end=end, channel=channel,
                               timestamp_tolerance_ms=timestamp_tolerance_ms)
        write_json(stage / "witness.json", report)
        return finish_run(stage, "audio-witness", [source], metadata={"result_file": "witness.json", "review_mapping": report["review_mapping"]})
