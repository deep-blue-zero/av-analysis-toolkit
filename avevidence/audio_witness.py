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
        return {"schema": "ave.audio-witness.v1", "witness_kind": "FORENSIC_AUDIO_WITNESS", "source_sha256": source["sha256"], "stream_index": stream["index"],
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


def provider_witness(witness_run, output, *, max_duration_seconds=120., max_bytes=24*1024*1024):
    """Quantize an immutable native witness, retaining its source clock.

    No resampling, downmix, gain change, gap concatenation or base64 storage.
    PCM16 is a declared lossy sample conversion, not native byte preservation.
    """
    import copy
    import wave
    import numpy as np
    from .audio import _decode, _scratch
    from .common import file_record, read_json
    from .observer_requests import load_witness, wav_identity
    from .auditory_profiles import request_policy
    finite(max_duration_seconds, "provider duration ceiling")
    if not 0 < max_duration_seconds <= 120 or type(max_bytes) is not int or not 44 <= max_bytes <= 24*1024*1024:
        raise AVError("Provider witness ceilings must be at most 120 s and 24 MiB")
    policy = request_policy({"task_profile": "PERFORMANCE_MUSIC", "audio_mode": "coherent_section",
                             "allow_long_section": True, "section_budget_usd": .01})
    native = load_witness(witness_run, policy=policy)
    if native.get("witness_kind") == "PROVIDER_SUBMISSION_WITNESS":
        raise AVError("Derive submission audio from the forensic witness, not a second quantization")
    if native["duration_seconds"] > max_duration_seconds or not 1 <= native["identity"]["channels"] <= 2:
        raise AVError("Provider witness exceeds the declared duration or mono/stereo policy")
    if native["identity"]["sample_frames"]*native["identity"]["channels"]*2+44 > max_bytes:
        raise AVError("PCM16 provider witness exceeds the declared byte ceiling")
    source_path = Path(native["execution_audio_path"])
    parent_path = Path(witness_run)/"witness.json"
    with output_transaction(output, [witness_run]) as stage:
        with _scratch(stage) as scratch:
            source = probe_source(source_path)
            decoded = _decode(source, select_stream(source, "audio"), scratch, "provider-native")
            values = np.memmap(decoded["raw_path"], mode="r", dtype="<f8",
                               shape=(decoded["sample_frames"], decoded["channels"]))
            if not np.isfinite(values).all() or np.max(np.abs(values)) > 1.000001:
                raise AVError("PCM16 conversion refuses nonfinite or materially out-of-range native samples")
            pcm = np.rint(np.clip(values, -1., 32767/32768)*32768).astype("<i2")
            destination = stage/"audio.wav"
            with wave.open(str(destination), "wb") as stream:
                stream.setnchannels(decoded["channels"]); stream.setsampwidth(2)
                stream.setframerate(decoded["sample_rate_hz"]); stream.writeframes(pcm.tobytes())
            frame = wav_identity(destination, max_bytes=max_bytes)
            check_source = probe_source(destination)
            check = _decode(check_source, select_stream(check_source, "audio"), scratch, "provider-check")
            actual = np.memmap(check["raw_path"], mode="r", dtype="<f8", shape=values.shape)
            if (any(check[k] != decoded[k] for k in ("sample_frames", "sample_rate_hz", "channels"))
                or not np.array_equal(actual, pcm.astype("<f8")/32768)):
                raise AVError("PCM16 witness failed decoded sample verification")
            error = float(np.max(np.abs(actual-values)))
            del actual, values, pcm
        mapping = copy.deepcopy(native["review_mapping"])
        mapping["artifact_sha256"] = sha256(destination)
        original = read_json(parent_path)
        (stage/"parent-witness.json").write_bytes(parent_path.read_bytes())
        transform = {"schema": "ave.provider-audio-transformation.v1", "provider": "openai",
            "route": "/v1/chat/completions", "input_audio_sha256": native["clip_sha256"],
            "input_witness_sha256": sha256(parent_path), "output_audio_sha256": sha256(destination),
            "input_format": native["wav_format"], "output_format": frame,
            "operation": "ROUND_TO_NEAREST_SIGNED_PCM16", "normalization": False, "resampling": False,
            "channel_strategy": "preserve", "sample_frames_preserved": True,
            "source_clock_preserved": True, "decoded_sample_verification": "PASS",
            "max_sample_error": error, "byte_identity_claimed": False,
            "max_duration_seconds": max_duration_seconds, "max_bytes": max_bytes}
        write_json(stage/"transformation.json", transform)
        report = {**original, "witness_kind": "PROVIDER_SUBMISSION_WITNESS",
            "identity": {**original["identity"], "pcm_sha256": check["pcm_sha256"],
                         "pcm_bytes": check["pcm_bytes"]},
            "artifact_sha256": sha256(destination), "review_mapping": mapping,
            "transformations": original["transformations"]+[transform],
            "provider_transformation_sha256": sha256(stage/"transformation.json"),
            "fidelity": "PCM16 quantization; same frames, rate, channels and original-source clock"}
        write_json(stage/"witness.json", report)
        return finish_run(stage, "provider-audio-witness", [file_record(parent_path), file_record(source_path)],
                          metadata={"result_file": "witness.json", "witness_kind": report["witness_kind"]})


def validate_provider_witness(folder, clip):
    """Revalidate derivative framing, immutable receipt and source lineage."""
    from .common import read_json
    from .observer_requests import wav_identity
    folder = Path(folder)
    receipt = read_json(folder/"transformation.json")
    parent_path = folder/"parent-witness.json"
    parent = read_json(parent_path)
    frame = wav_identity(folder/"audio.wav", max_bytes=24*1024*1024)
    from .common import finite
    finite(receipt.get("max_sample_error"), "PCM16 quantization error", minimum=0)
    finite(receipt.get("max_duration_seconds"), "provider duration ceiling", minimum=0)
    if (sha256(folder/"transformation.json") != clip.get("provider_transformation_sha256")
        or receipt.get("schema") != "ave.provider-audio-transformation.v1"
        or receipt.get("input_witness_sha256") != sha256(parent_path)
        or receipt.get("input_audio_sha256") != parent.get("artifact_sha256")
        or receipt.get("output_audio_sha256") != clip.get("artifact_sha256")
        or receipt.get("output_format") != frame or frame["sample_representation"] != "PCM"
        or frame["bits_per_sample"] != 16 or not 1 <= frame["channels"] <= 2
        or receipt.get("decoded_sample_verification") != "PASS"
        or receipt.get("provider") != "openai" or receipt.get("route") != "/v1/chat/completions"
        or receipt.get("operation") != "ROUND_TO_NEAREST_SIGNED_PCM16"
        or receipt.get("channel_strategy") != "preserve"
        or receipt["max_sample_error"] > 1/32768 + .0000011
        or not 0 < frame["duration_seconds"] <= receipt["max_duration_seconds"] <= 120
        or type(receipt.get("max_bytes")) is not int
        or not (folder/"audio.wav").stat().st_size <= receipt["max_bytes"] <= 24*1024*1024
        or receipt.get("byte_identity_claimed") is not False
        or receipt.get("resampling") is not False or receipt.get("normalization") is not False
        or receipt.get("sample_frames_preserved") is not True or receipt.get("source_clock_preserved") is not True
        or any(parent.get(k) != clip.get(k) for k in ("source_sha256", "stream_index", "source_interval_seconds"))
        or parent["review_mapping"]["segments"] != clip["review_mapping"]["segments"]
        or any(parent["identity"][k] != frame[k] for k in ("sample_frames", "sample_rate_hz", "channels"))):
        raise AVError("Provider witness framing or transformation lineage is inconsistent")
    return receipt
