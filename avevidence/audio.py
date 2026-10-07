"""Audio preparation with stable identities, explicit clocks and bounded claims.

Stored PCM is compact: missing timestamp intervals are not invented as silence.
Every derivative carries a sample-range to parent-clock map. No function here
establishes perceptual listening, speaker identity, or audiovisual equivalence.
"""
from __future__ import annotations

from contextlib import contextmanager
from fractions import Fraction
import importlib.metadata
import json
import math
from pathlib import Path
import re
import shutil
import struct

from .common import (AVError, file_record, finite, finish_run, interval,
                     output_transaction, probe_source, read_csv, read_json, run,
                     safe_member, select_stream, sha256, write_csv, write_json)

CLOCK = "original_pts_minus_source_origin"
_FRAME = re.compile(r"\bn:(\d+)\s+pts:(-?\d+)\s+pts_time:[^\s]+.*?\brate:(\d+)\s+nb_samples:(\d+)")


@contextmanager
def _scratch(stage):
    folder = Path(stage) / ".audio-working"
    folder.mkdir()
    try:
        yield folder
    finally:
        # Only files created in this fixed, fresh staging subdirectory are removed.
        for p in folder.iterdir():
            if not p.is_file() or p.is_symlink():
                raise AVError("Unexpected object in audio scratch directory")
            p.unlink()
        folder.rmdir()


def _layout(stream):
    layout = stream.get("channel_layout")
    if layout and layout != "unknown":
        return layout
    return "mono" if int(stream.get("channels", 0)) == 1 else None


def _decode(source, stream, folder, stem, *, timestamp_tolerance_seconds=None,
            start=None, end=None, max_output_bytes=None, timeout_s=180):
    """Decode unchanged rate/channel order to f64, keeping decoder-frame PTS."""
    rate = int(stream.get("sample_rate", 0))
    channels = int(stream.get("channels", 0))
    if rate <= 0 or channels <= 0:
        raise AVError("Audio rate and channel count must be known")
    raw = Path(folder) / (stem + ".f64le")
    filters = "aformat=sample_fmts=dbl"
    if start is not None or end is not None:
        a, b = interval(start, end, source.get("duration_seconds"))
        filters += f",atrim=start={a + source['origin_seconds']:.12f}:end={b + source['origin_seconds']:.12f}"
    else:
        a = b = None
    filters += f",asettb=expr=1/{rate},ashowinfo"
    if max_output_bytes is not None:
        if type(max_output_bytes) is not int or max_output_bytes <= 8 * channels:
            raise AVError("Decoded output budget must be a positive integer byte limit")
    timeout_s = finite(timeout_s, "decode timeout", 0)
    if not 0 < timeout_s <= 3600:
        raise AVError("Decode timeout must be positive and at most 3600 s")
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-copyts",
               "-i", source["path"], "-map", f"0:{stream['index']}", "-vn", "-sn", "-dn",
               "-af", filters, "-c:a", "pcm_f64le"]
    if b is not None:
        # Stop the output at the absolute source endpoint as well as trimming
        # the filter. atrim alone can continue decoding an entire long source.
        command += ["-to", f"{b + source['origin_seconds']:.12f}"]
    if max_output_bytes is not None:
        command += ["-fs", str(max_output_bytes)]
    command += ["-f", "f64le", "-"]
    result = run(command, binary=True, stdout_file=raw, timeout=timeout_s)
    stderr = result.stderr.decode("utf8", "replace") if isinstance(result.stderr, bytes) else result.stderr
    size = raw.stat().st_size
    if max_output_bytes is not None and size >= max_output_bytes:
        raise AVError("Decoded audio reached its output-byte budget; partial output is not admitted")
    if size == 0 or size % (8 * channels):
        raise AVError("No complete positive-length decoded audio; empty audio cannot be verified")
    frames = []
    for match in _FRAME.finditer(stderr):
        number, pts, frame_rate, count = map(int, match.groups())
        if number != len(frames) or frame_rate != rate or count <= 0:
            raise AVError("Inconsistent decoded audio frame timing")
        frames.append({"pts_samples": pts, "sample_count": count})
    if not frames or sum(x["sample_count"] for x in frames) != size // (8 * channels):
        raise AVError("Cannot bind every decoded sample to a timestamped frame")
    try:
        tick = abs(float(Fraction(stream.get("time_base", f"1/{rate}"))))
    except (ValueError, ZeroDivisionError):
        raise AVError("Audio stream time base is unavailable")
    tolerance = max(tick, 1 / rate) + 1e-9
    if timestamp_tolerance_seconds is not None:
        requested = finite(timestamp_tolerance_seconds, "timestamp tolerance", 0)
        if requested > .010:
            raise AVError("Explicit timestamp tolerance is limited to 10 ms; larger clock repairs require separate evidence")
        tolerance = max(tolerance, requested + 1e-9)
    segments, offset, max_adjustment = [], 0, 0.0
    for frame in frames:
        observed = frame["pts_samples"] / rate - source["origin_seconds"]
        count = frame["sample_count"]
        if segments:
            prior = segments[-1]
            delta = observed - prior["source_end_seconds"]
            if abs(delta) <= tolerance:
                max_adjustment = max(max_adjustment, abs(delta))
                prior["sample_end"] += count
                prior["source_end_seconds"] = prior["source_start_seconds"] + (prior["sample_end"] - prior["sample_start"]) / rate
                offset += count
                continue
            if delta < 0:
                raise AVError("Overlapping audio timestamps exceed source granularity; explicit clock repair is required")
        segments.append({"sample_start": offset, "sample_end": offset + count,
                         "source_start_seconds": observed, "source_end_seconds": observed + count / rate})
        offset += count
    return {"raw_path": raw, "pcm_sha256": sha256(raw), "pcm_bytes": size,
            "sample_frames": offset, "sample_rate_hz": rate, "channels": channels,
            "channel_layout": _layout(stream), "stream_index": stream["index"],
            "encoding": "f64le interleaved native rate and channel order; no normalization or downmix",
            "segments": segments, "decoder_frames": frames,
            "clock": CLOCK, "origin_seconds": source["origin_seconds"],
            "timestamp_quantization_tolerance_seconds": tolerance,
            "max_timestamp_adjustment_seconds": max_adjustment,
            "decode_scope": {"requested_start_s": a, "requested_end_s": b,
                "max_output_bytes": max_output_bytes, "timeout_s": timeout_s,
                "whole_stream_claimed": a is None},
            "timestamp_policy": ("Contiguous samples within one source time-base tick are joined; larger gaps remain explicit; larger overlaps refuse"
                if timestamp_tolerance_seconds is None else
                f"Explicit analysis timestamp tolerance {timestamp_tolerance_seconds:g} s; native samples unchanged; joined frame starts may shift by at most the reported tolerance; larger gaps remain explicit and larger overlaps refuse")}


def _identity(decoded):
    return {k: decoded[k] for k in ("pcm_sha256", "pcm_bytes", "sample_frames", "sample_rate_hz",
            "channels", "channel_layout", "encoding", "clock", "origin_seconds",
            "timestamp_quantization_tolerance_seconds", "max_timestamp_adjustment_seconds", "timestamp_policy")}


def _sample_equal(a, b):
    return all(a[k] == b[k] for k in ("pcm_sha256", "pcm_bytes", "sample_frames", "sample_rate_hz", "channels"))


def _raw_input(decoded, path=None):
    args = ["-f", "f64le", "-ar", str(decoded["sample_rate_hz"]), "-ac", str(decoded["channels"])]
    if decoded["channel_layout"]:
        args += ["-channel_layout", decoded["channel_layout"]]
    else:
        args += ["-guess_layout_max", "0"]
    return args + ["-i", str(path or decoded["raw_path"])]


def _selection(decoded, start, end):
    """Half-open interval selection; count missing time separately from rounding."""
    rate = decoded["sample_rate_hz"]
    parts, coverage = [], 0.0
    for segment in decoded["segments"]:
        lo = max(start, segment["source_start_seconds"])
        hi = min(end, segment["source_end_seconds"])
        if hi <= lo:
            continue
        coverage += hi - lo
        first = segment["sample_start"] + max(0, math.ceil((lo - segment["source_start_seconds"]) * rate - 1e-6))
        last = segment["sample_start"] + math.ceil((hi - segment["source_start_seconds"]) * rate - 1e-6)
        last = min(last, segment["sample_end"])
        if last > first:
            actual = segment["source_start_seconds"] + (first - segment["sample_start"]) / rate
            parts.append({"input_sample_start": first, "input_sample_end": last,
                          "source_start_seconds": actual, "source_end_seconds": actual + (last-first)/rate})
    missing = max(0.0, end-start-coverage)
    status = "MISSING" if not parts else "COMPLETE" if missing <= 1/rate + 1e-8 else "PARTIAL"
    return {"status": status, "requested_start_seconds": start, "requested_end_seconds": end,
            "covered_seconds": coverage, "missing_seconds": missing, "parts": parts,
            "boundary_rounding": "Sample onsets in the half-open interval; both endpoint indices use ceil"}


def _slice(decoded, selection, target):
    if not selection["parts"]:
        raise AVError("Requested interval contains no decoded audio")
    stride, offset, segments = decoded["channels"] * 8, 0, []
    with decoded["raw_path"].open("rb") as src, Path(target).open("xb") as dst:
        for part in selection["parts"]:
            count = part["input_sample_end"] - part["input_sample_start"]
            src.seek(part["input_sample_start"] * stride)
            remaining = count * stride
            while remaining:
                block = src.read(min(remaining, 1024 * 1024))
                if not block:
                    raise AVError("Decoded sample file ended unexpectedly")
                dst.write(block)
                remaining -= len(block)
            segments.append({"sample_start": offset, "sample_end": offset+count,
                             "source_start_seconds": part["source_start_seconds"], "source_end_seconds": part["source_end_seconds"]})
            offset += count
    result = dict(decoded, raw_path=Path(target), segments=segments, sample_frames=offset,
                  pcm_bytes=offset*stride, pcm_sha256=sha256(target))
    return result


def _preserve_wav_channel_mask(decoded, output):
    """Upgrade generated mono/stereo IEEE-float headers without changing payload.

    FFmpeg can omit the channel mask for <=2-channel float WAV even with an
    explicit layout. Only layouts known from the parent are supplied here;
    channel counts alone never establish a stereo layout. Re-decoding and
    exact ordered sample/layout verification remain mandatory at the caller.
    """
    masks = {"mono": (1, 0x4), "stereo": (2, 0x3)}
    specification = masks.get(decoded["channel_layout"])
    if specification is None:
        return
    channels, mask = specification
    if decoded["channels"] != channels:
        raise AVError("Known WAV layout does not match the decoded channel count")
    output = Path(output)
    temporary = output.with_name(output.name + ".layout-working")
    with output.open("rb") as original:
        header = bytearray(original.read(12))
        if len(header) != 12 or header[:4] not in (b"RIFF", b"RF64") or header[8:] != b"WAVE":
            raise AVError("Generated float WAV has no supported RIFF/RF64 header")
        prefix = bytearray()
        ds64_offset = None
        while True:
            chunk = original.read(8)
            if len(chunk) != 8:
                raise AVError("Generated float WAV has no complete fmt chunk")
            length = struct.unpack_from("<I", chunk, 4)[0]
            if chunk[:4] == b"fmt ":
                value = original.read(length)
                if len(value) != length:
                    raise AVError("Generated float WAV fmt chunk is truncated")
                if length >= 40 and struct.unpack_from("<H", value)[0] == 0xFFFE:
                    return  # Existing mask is checked by the caller's re-decode.
                break
            # This operates only on a freshly generated header, never arbitrary
            # input media. Refuse surprising large pre-format metadata.
            if chunk[:4] == b"data" or length > 1024 * 1024:
                raise AVError("Unexpected generated WAV header before fmt chunk")
            value = original.read(length + (length & 1))
            if len(value) != length + (length & 1):
                raise AVError("Generated float WAV metadata is truncated")
            if chunk[:4] == b"ds64":
                if ds64_offset is not None or length < 28:
                    raise AVError("Generated RF64 has an invalid ds64 chunk")
                ds64_offset = len(prefix) + 8
            prefix.extend(chunk + value)
        rate = decoded["sample_rate_hz"]
        expected = (3, channels, rate, rate * channels * 8, channels * 8, 64, 0)
        if length != 18 or struct.unpack("<HHIIHHH", value) != expected:
            raise AVError("Generated float WAV header is not the expected native f64 format")
        extension = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * channels * 8,
                                channels * 8, 64, 22, 64, mask)
        extension += bytes.fromhex("0300000000001000800000aa00389b71")
        delta = len(extension) - length
        if header[:4] == b"RF64":
            if ds64_offset is None:
                raise AVError("Generated RF64 has no size mapping")
            old_size = struct.unpack_from("<Q", prefix, ds64_offset)[0]
            struct.pack_into("<Q", prefix, ds64_offset, old_size + delta)
        else:
            old_size = struct.unpack_from("<I", header, 4)[0]
            if old_size + delta > 0xFFFFFFFF:
                raise AVError("WAV layout header exceeds RIFF capacity; explicit RF64 is required")
            struct.pack_into("<I", header, 4, old_size + delta)
        created = False
        try:
            with temporary.open("xb") as changed:
                created = True
                changed.write(header)
                changed.write(prefix)
                changed.write(b"fmt " + struct.pack("<I", len(extension)) + extension)
                shutil.copyfileobj(original, changed, length=1024 * 1024)
        except BaseException:
            if created and temporary.exists():
                temporary.unlink()
            raise
    temporary.replace(output)


def _encode_wav(decoded, output):
    run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n"] + _raw_input(decoded) +
        ["-map", "0:a:0", "-c:a", "pcm_f64le", "-rf64", "auto", output])
    _preserve_wav_channel_mask(decoded, output)


def _review_segments(parent, artifact):
    result = []
    rate = parent["sample_rate_hz"]
    for original in parent["segments"]:
        for derived in artifact["segments"]:
            first = max(original["sample_start"], derived["sample_start"])
            last = min(original["sample_end"], derived["sample_end"])
            if last <= first:
                continue
            result.append({"parent_start_seconds": original["source_start_seconds"]+(first-original["sample_start"])/rate,
                           "parent_end_seconds": original["source_start_seconds"]+(last-original["sample_start"])/rate,
                           "derivative_start_seconds": derived["source_start_seconds"]+(first-derived["sample_start"])/rate,
                           "derivative_end_seconds": derived["source_start_seconds"]+(last-derived["sample_start"])/rate})
    return result


def _codec_class(codec):
    name = (codec or "").lower()
    if name.startswith("pcm_") or name in {"flac", "alac", "wavpack", "ape", "truehd", "mlp"}:
        return "LOSSLESS_OR_UNCOMPRESSED"
    if name in {"aac", "mp3", "opus", "vorbis", "ac3", "eac3", "dts", "wma", "wmav2"}:
        return "LOSSY"
    return "UNKNOWN"


def _derivative_result(stage, source, stream, decoded, artifact, artifact_decoded, *, kind,
                       selection=None, storage_profile=None, stream_copy_requested=False,
                       artifact_codec=None, original_file_copy=False):
    if not _sample_equal(decoded, artifact_decoded):
        raise AVError("Derivative changed decoded f64 samples/rate/channels; sample-preserving conversion refused")
    if decoded["channel_layout"] is not None and artifact_decoded["channel_layout"] != decoded["channel_layout"]:
        raise AVError("Derivative changed the known channel layout")
    relative = Path(artifact).relative_to(stage).as_posix()
    digest = sha256(artifact)
    segments = [dict(s, sample_rate_hz=decoded["sample_rate_hz"], parent_source_sha256=source["sha256"],
                     parent_stream_index=stream["index"]) for s in decoded["segments"]]
    mapping = {"modality": "audio", "artifact_kind": kind, "artifact_path": relative, "artifact_sha256": digest,
               "derivative_stream_index": artifact_decoded["stream_index"],
               "parent_source_sha256": source["sha256"], "parent_stream_index": stream["index"],
               "clock": CLOCK, "origin_seconds": source["origin_seconds"], "segments": _review_segments(decoded, artifact_decoded)}
    if len(mapping["segments"]) == 1:
        mapping.update(mapping["segments"][0])
    result = {"schema": "ave.audio.v1", "parent_source_sha256": source["sha256"], "parent_stream_index": stream["index"],
              "clock": CLOCK, "origin_seconds": source["origin_seconds"],
              "artifact_path": relative, "artifact_sha256": digest, "derivative_stream_index": artifact_decoded["stream_index"],
              "identity": _identity(decoded), "segments": segments, "review_mapping": mapping,
              "sample_preservation_verified": True, "artifact_channel_layout": artifact_decoded["channel_layout"],
              "channel_layout_preservation": "MATCH" if decoded["channel_layout"] is not None else "SOURCE_LAYOUT_UNKNOWN",
              "wav_layout_policy": "Known mono/stereo native f64 WAV exports use WAVEFORMATEXTENSIBLE masks; source-unknown layouts are not inferred from channel count; exact decoded sample/layout checks still apply",
              "selection": selection,
              "storage_profile": storage_profile,
              "source_codec": stream.get("codec_name"),
              "source_codec_class": _codec_class(stream.get("codec_name")),
              "artifact_codec": artifact_codec,
              "artifact_codec_class": _codec_class(artifact_codec),
              "stream_copy_requested": bool(stream_copy_requested),
              "stream_copy_status": ("SOURCE_FILE_COPIED_NOT_REMUXED" if original_file_copy else
                                     "COMPLETED_WITHOUT_AUDIO_REENCODING_PACKET_IDENTITY_NOT_VERIFIED"
                                     if stream_copy_requested else "NOT_REQUESTED"),
              "original_file_copy": original_file_copy,
              "container_identity": "EXACT_FILE_SHA256_MATCH" if original_file_copy else "NOT_PRESERVED_OR_CLAIMED",
              "encoded_packet_identity": "IMPLIED_BY_EXACT_FILE_COPY" if original_file_copy else "NOT_ESTABLISHED",
              "source_information_loss_status": (
                  "SOURCE_IS_LOSSY_AND_REMAINS_LOSSY"
                  if _codec_class(stream.get("codec_name")) == "LOSSY" else
                  "SOURCE_IS_LOSSLESS_OR_UNCOMPRESSED"
                  if _codec_class(stream.get("codec_name")) == "LOSSLESS_OR_UNCOMPRESSED" else
                  "SOURCE_CODEC_FIDELITY_CLASS_UNKNOWN"),
              "fidelity_statement": (
                  "Complete source WAV copied byte-for-byte and verified by whole-file SHA-256." if original_file_copy else
                  "Native codec stream was copied/remuxed without audio re-encoding; this preserves a lossy source as lossy and does not restore discarded information."
                  if stream_copy_requested else
                  "Decoded native-rate/native-channel samples were re-encoded and accepted only after exact f64 decoded-sample/rate/channel verification."),
              "storage_policy": "PCM sample indices are compact; source gaps/delays remain explicit in the mapping, never assumed to be silence",
              "perceptual_review": "NOT_PERFORMED", "av_synchronization_verified": False}
    write_json(Path(stage)/"audio.json", result)
    return {"result_file": "audio.json", "review_mapping": mapping}


def extract_audio(input, output, *, stream_index=None, format=None, storage_profile=None):
    if storage_profile is not None and storage_profile not in {"practical", "forensic", "verified-flac"}:
        raise AVError("Audio storage profile must be practical, forensic or verified-flac")
    if format is not None and format not in {"wav", "preserve", "flac"}:
        raise AVError("Audio format must be wav, preserve or flac")
    if storage_profile is not None and format is not None:
        raise AVError("Choose storage_profile or the legacy format alias, not both")
    selected_profile = storage_profile or {None: "forensic", "wav": "forensic",
                                           "preserve": "practical", "flac": "verified-flac"}[format]
    selected_format = {"practical": "preserve", "forensic": "wav",
                       "verified-flac": "flac"}[selected_profile]
    with output_transaction(output, [input]) as stage:
        source = probe_source(input)
        stream = select_stream(source, "audio", stream_index)
        with _scratch(stage) as scratch:
            decoded = _decode(source, stream, scratch, "source")
            original_file_copy = False
            if selected_format == "preserve":
                codec = stream.get("codec_name", "")
                ext = ".wav" if codec.startswith("pcm_") else {"aac": ".m4a", "alac": ".m4a", "mp3": ".mp3", "flac": ".flac", "opus": ".ogg", "vorbis": ".ogg"}.get(codec, ".mka")
                artifact = stage / ("audio" + ext)
                native_wav = ("wav" in source.get("probe", {}).get("format", {}).get("format_name", "").split(",")
                              and len(source["streams"]) == 1 and ext == ".wav")
                if native_wav:
                    shutil.copyfile(source["path"], artifact)
                    if sha256(artifact) != source["sha256"]:
                        raise AVError("Source WAV changed during byte-preserving copy")
                    original_file_copy = True
                else:
                    run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n", "-i", source["path"],
                         "-map", f"0:{stream['index']}", "-vn", "-sn", "-dn", "-c:a", "copy", artifact])
                    if ext == ".wav" and codec == "pcm_f64le":
                        _preserve_wav_channel_mask(decoded, artifact)
            elif selected_format == "flac":
                artifact = stage / "audio.flac"
                run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-n"] + _raw_input(decoded) + ["-map", "0:a:0", "-c:a", "flac", artifact])
            else:
                artifact = stage / "audio.wav"
                _encode_wav(decoded, artifact)
            derived_source = probe_source(artifact)
            derived_stream = select_stream(derived_source, "audio")
            derived = _decode(derived_source, derived_stream, scratch, "derivative")
            kind = {"practical": "audio_native_stream_copy", "forensic": "audio_forensic_f64_wav",
                    "verified-flac": "audio_verified_flac"}[selected_profile]
            meta = _derivative_result(stage, source, stream, decoded, artifact, derived, kind=kind,
                                      storage_profile=selected_profile,
                                      stream_copy_requested=selected_profile == "practical",
                                      artifact_codec=derived_stream.get("codec_name"),
                                      original_file_copy=original_file_copy)
        result = finish_run(stage, "extract_audio", [source],
                            {"stream_index": stream["index"], "storage_profile": selected_profile,
                             "legacy_format_alias": format}, meta)
    return result


def clip_audio(input, output, *, stream_index=None, start, end, pad=0.0):
    a, b = interval(start, end)
    pad = finite(pad, "padding", 0)
    with output_transaction(output, [input]) as stage:
        source = probe_source(input)
        stream = select_stream(source, "audio", stream_index)
        with _scratch(stage) as scratch:
            decoded = _decode(source, stream, scratch, "source")
            requested = _selection(decoded, a, b)
            if requested["status"] == "MISSING":
                raise AVError("Requested clip interval contains no decoded audio")
            upper = b + pad
            if source["duration_seconds"] is not None:
                upper = min(upper, source["duration_seconds"])
            selected = _selection(decoded, max(0, a-pad), upper)
            clipped = _slice(decoded, selected, scratch/"clip.f64le")
            artifact = stage / "audio.wav"
            _encode_wav(clipped, artifact)
            derived_source = probe_source(artifact)
            derived = _decode(derived_source, select_stream(derived_source, "audio"), scratch, "derivative")
            selected["requested_unpadded"] = requested
            meta = _derivative_result(stage, source, stream, clipped, artifact, derived, kind="audio_clip", selection=selected)
        result = finish_run(stage, "clip_audio", [source], {"stream_index": stream["index"], "start": a, "end": b, "pad": pad}, meta)
    return result


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _loudness(decoded):
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats"] + _raw_input(decoded) + ["-map", "0:a:0"]
    loud = run(base + ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    vol = run(base + ["-af", "volumedetect", "-f", "null", "-"])
    match = re.search(r'\{\s*"input_i".*?\}', loud.stderr, flags=re.S)
    if not match:
        raise AVError("FFmpeg did not supply loudnorm input measurements")
    raw = json.loads(match.group(0))
    values = {}
    for key in ("mean_volume", "max_volume"):
        found = re.findall(key + r":\s*([-+\d.inf]+)\s*dB", vol.stderr)
        if not found:
            raise AVError("FFmpeg did not supply volumedetect measurements")
        values[key] = _number(found[-1])
    return {"loudnorm_input": {"integrated_lufs": _number(raw["input_i"]), "loudness_range_lu": _number(raw["input_lra"]),
            "true_peak_dbtp": _number(raw["input_tp"]), "integrated_gating_threshold_lufs": _number(raw["input_thresh"])},
            "raw_loudnorm_report": raw,
            "volumedetect": {"mean_volume_dbfs": values["mean_volume"], "sample_peak_dbfs": values["max_volume"],
                             "processing": "FFmpeg volumedetect converts to signed 16-bit samples"},
            "normalization_written_to_source": False,
            "null_policy": "Nonfinite filter results are unavailable; exact raw filter strings are retained"}


def measure_audio(input, output, *, stream_index=None, start=None, end=None):
    with output_transaction(output, [input]) as stage:
        source = probe_source(input)
        stream = select_stream(source, "audio", stream_index)
        with _scratch(stage) as scratch:
            decoded = _decode(source, stream, scratch, "source")
            if start is None and end is None:
                a, b = decoded["segments"][0]["source_start_seconds"], decoded["segments"][-1]["source_end_seconds"]
            else:
                a, b = interval(start, end, decoded["segments"][-1]["source_end_seconds"])
            selection = _selection(decoded, a, b)
            measurements = None
            if selection["status"] == "COMPLETE":
                selected = _slice(decoded, selection, scratch/"selected.f64le")
                measurements = _loudness(selected)
            report = {"schema": "ave.audio_metrics.v1", "parent_source_sha256": source["sha256"],
                      "parent_stream_index": stream["index"], "clock": CLOCK, "origin_seconds": source["origin_seconds"],
                      "container_duration_seconds": source.get("format_duration_seconds", source["duration_seconds"]),
                      "canonical_source_duration_seconds": source["duration_seconds"],
                      "decoded_audio_seconds": decoded["sample_frames"]/decoded["sample_rate_hz"],
                      "identity": _identity(decoded), "selection": selection, "measurements": measurements,
                      "scope": "Selected final-mix channels; gaps are unavailable, not invented silence; not speaker-isolated",
                      "perceptual_review": "NOT_PERFORMED"}
            write_json(stage/"metrics.json", report)
        result = finish_run(stage, "measure_audio", [source], {"stream_index": stream["index"], "start": start, "end": end},
                            {"result_file": "metrics.json", "coverage_status": selection["status"]})
    return result


def compare_audio(a, b, output, *, a_stream=None, b_stream=None):
    with output_transaction(output, [a, b]) as stage:
        sources = [probe_source(a), probe_source(b)]
        streams = [select_stream(sources[0], "audio", a_stream), select_stream(sources[1], "audio", b_stream)]
        with _scratch(stage) as scratch:
            left = _decode(sources[0], streams[0], scratch, "a")
            right = _decode(sources[1], streams[1], scratch, "b")
            samples_equal = _sample_equal(left, right)
            layout = "UNKNOWN" if left["channel_layout"] is None or right["channel_layout"] is None else "MATCH" if left["channel_layout"] == right["channel_layout"] else "DIFFERENT"
            tolerance = max(left["timestamp_quantization_tolerance_seconds"], right["timestamp_quantization_tolerance_seconds"])
            timing_equal = len(left["segments"]) == len(right["segments"]) and all(
                x["sample_start"] == y["sample_start"] and x["sample_end"] == y["sample_end"] and
                abs(x["source_start_seconds"]-y["source_start_seconds"]) <= tolerance and
                abs(x["source_end_seconds"]-y["source_end_seconds"]) <= tolerance
                for x, y in zip(left["segments"], right["segments"]))
            status = "DIFFERENT" if not samples_equal or not timing_equal or layout == "DIFFERENT" else "INDETERMINATE" if layout == "UNKNOWN" else "MATCH"
            report = {"schema": "ave.audio_comparison.v1", "comparison_status": status,
                      "a": dict(_identity(left), segments=left["segments"], source_sha256=sources[0]["sha256"], stream_index=streams[0]["index"]),
                      "b": dict(_identity(right), segments=right["segments"], source_sha256=sources[1]["sha256"], stream_index=streams[1]["index"]),
                      "native_decoded_samples_equal": samples_equal, "channel_layout_comparison": layout,
                      "canonical_timing_equal_within_source_granularity": timing_equal, "timing_tolerance_seconds": tolerance,
                      "source_origins_equal": sources[0]["origin_seconds"] == sources[1]["origin_seconds"],
                      "encoded_packet_identity": "NOT_ESTABLISHED", "audiovisual_equivalence": "NOT_ESTABLISHED",
                      "perceptual_review": "NOT_PERFORMED",
                      "comparison_scope": "Native f64 decoded ordered samples, known channel layout and canonical audio timing; not matching video or narrative"}
            write_json(stage/"comparison.json", report)
        result = finish_run(stage, "compare_audio", sources, {"a_stream": streams[0]["index"], "b_stream": streams[1]["index"]},
                            {"result_file": "comparison.json", "comparison_status": status})
    return result


def _verified_artifact(folder, manifest, relative):
    path = safe_member(folder, relative)
    entries = [x for x in manifest.get("artifacts", []) if x.get("path") == relative]
    if len(entries) != 1 or not path.is_file() or path.stat().st_size != entries[0]["size_bytes"] or sha256(path) != entries[0]["sha256"]:
        raise AVError("Audio run artifact is missing or changed")
    return path


def _cue_input(value, stream_index, scratch):
    path = Path(value).resolve()
    folder = path if path.is_dir() else path.parent
    if (folder/"run.json").is_file() and (folder/"audio.json").is_file():
        manifest = read_json(folder/"run.json")
        if manifest.get("operation") not in {"extract_audio", "clip_audio"}:
            raise AVError("Expected an extraction or audio-clip run")
        receipt_path = _verified_artifact(folder, manifest, manifest["metadata"]["result_file"])
        receipt = read_json(receipt_path)
        artifact = _verified_artifact(folder, manifest, receipt["artifact_path"])
        if not path.is_dir() and path != artifact:
            raise AVError("The supplied path is not this audio run's artifact")
        if sha256(artifact) != receipt["artifact_sha256"]:
            raise AVError("Audio receipt and artifact disagree")
        if stream_index is not None and int(stream_index) != receipt["parent_stream_index"]:
            raise AVError("Requested parent audio stream differs from the extraction")
        source = probe_source(artifact)
        decoded = _decode(source, select_stream(source, "audio", receipt["derivative_stream_index"]), scratch, "cue_input")
        if not all(decoded[k] == receipt["identity"][k] for k in ("pcm_sha256", "pcm_bytes", "sample_frames", "sample_rate_hz", "channels")):
            raise AVError("Audio derivative no longer reproduces its parent sample identity")
        decoded["segments"] = receipt["segments"]
        decoded["origin_seconds"] = receipt["origin_seconds"]
        parent = {"parent_source_sha256": receipt["parent_source_sha256"], "parent_stream_index": receipt["parent_stream_index"],
                  "clock": CLOCK, "origin_seconds": receipt["origin_seconds"]}
        dependencies = [source, file_record(folder/"run.json", "audio_run_manifest"), file_record(receipt_path, "audio_mapping")]
    else:
        source = probe_source(path)
        stream = select_stream(source, "audio", stream_index)
        decoded = _decode(source, stream, scratch, "cue_input")
        parent = {"parent_source_sha256": source["sha256"], "parent_stream_index": stream["index"], "clock": CLOCK, "origin_seconds": source["origin_seconds"]}
        dependencies = [source]
    return decoded, parent, dependencies


def _db(amplitude):
    return 20*math.log10(amplitude) if amplitude > 0 else None


def _features(samples, rate, profile, pitch_options):
    import numpy as np
    total_energy, peak, crossings, previous = 0.0, 0.0, 0, None
    for offset in range(0, len(samples), 1024*1024):
        chunk = samples[offset:offset+1024*1024]
        if not np.isfinite(chunk).all():
            raise AVError("Nonfinite audio samples cannot be measured")
        total_energy += float(np.sum(chunk*chunk, dtype=np.float64))
        peak = max(peak, float(np.max(np.abs(chunk))))
        signs = np.signbit(chunk)
        crossings += int(np.count_nonzero(signs[1:] != signs[:-1]))
        if previous is not None:
            crossings += int(previous != signs[0])
        previous = signs[-1]
    if not math.isfinite(total_energy):
        raise AVError("Audio energy exceeds the numeric measurement range")
    frame = max(32, round(rate * (1024/16000 if profile == "speech" else 2048/22050)))
    hop = max(1, round(rate * (160/16000 if profile == "speech" else 512/22050)))
    result = {"rms_dbfs": _db(math.sqrt(total_energy/len(samples))),
              "sample_peak_dbfs": _db(peak), "sample_frames": len(samples),
              "zero_crossing_rate": crossings/(len(samples)-1) if len(samples) > 1 else None,
              "rms_zero_amplitude": total_energy == 0, "frame_length": frame, "hop_length": hop,
              "center": False, "window": "hann", "channel_policy": "independent channel; no averaging/downmix"}
    if len(samples) >= frame:
        frames = np.lib.stride_tricks.sliding_window_view(samples, frame)[::hop]
        freq = np.fft.rfftfreq(frame, 1/rate)
        window = np.hanning(frame)
        centroids, rolloffs, low_energy = [], [], 0
        for offset in range(0, len(frames), 256):
            batch = frames[offset:offset+256]
            energy = np.sqrt(np.mean(batch*batch, axis=1, dtype=np.float64))
            low_energy += int(np.count_nonzero(energy < 10**(pitch_options["energy_threshold_dbfs"]/20)))
            spectral = np.abs(np.fft.rfft(batch * window, axis=1))
            totals = spectral.sum(axis=1)
            valid = totals > 1e-12
            if valid.any():
                centroids.extend((np.sum(spectral[valid]*freq, axis=1)/totals[valid]).tolist())
                rolloffs.extend(freq[np.argmax(np.cumsum(spectral[valid], axis=1) >= .85*totals[valid,None], axis=1)].tolist())
        result.update({"analysis_frames": len(frames), "nonempty_spectral_frames": len(centroids),
                       "low_energy_frame_fraction": low_energy/len(frames),
                       "low_energy_is_not_silence_or_vad": True,
                       "spectral_centroid_hz_median": float(np.median(centroids)) if centroids else None,
                       "spectral_rolloff85_hz_median": float(np.median(rolloffs)) if rolloffs else None})
    else:
        result.update({"analysis_frames": 0, "nonempty_spectral_frames": 0, "low_energy_frame_fraction": None,
                       "spectral_centroid_hz_median": None, "spectral_rolloff85_hz_median": None,
                       "frame_status": "INSUFFICIENT_SAMPLES"})
    if pitch_options["pitch"]:
        result["pitch"] = _pitch(samples, rate, pitch_options)
    else:
        result["pitch"] = {"status": "NOT_REQUESTED"}
    return result


def _pitch(samples, rate, options):
    import numpy as np
    try:
        import librosa
    except ImportError as exc:
        raise AVError("Pitch requires the optional librosa dependencies") from exc
    base = {"algorithm": "librosa.pyin", "librosa_version": importlib.metadata.version("librosa"),
            "sample_rate_hz": 16000, "frame_length": 1024, "hop_length": 160, "center": False,
            "fmin_hz": options["fmin"], "fmax_hz": options["fmax"],
            "minimum_voiced_probability": options["min_voiced_probability"],
            "energy_threshold_dbfs": options["energy_threshold_dbfs"],
            "isolation": "OPERATOR_DECLARED_NOT_VERIFIED",
            "resampler": "librosa.resample soxr_hq, fix=True, scale=False when rate differs",
            "scope": "Estimated F0; not speaker identity, emotion, voice quality or perceptual listening"}
    if len(samples)/rate > 60:
        return dict(base, status="SKIPPED_INTERVAL_OVER_60_SECONDS", qualified_f0_median_hz=None)
    y = librosa.resample(samples, orig_sr=rate, target_sr=16000, res_type="soxr_hq", fix=True, scale=False) if rate != 16000 else samples
    if len(y) < 1024:
        return dict(base, status="INSUFFICIENT_SAMPLES", qualified_f0_median_hz=None)
    try:
        # Record only parameters owned and explicitly supplied by this toolkit.
        # Third-party signature defaults may contain sentinels or other objects
        # that are intentionally not part of our stable JSON contract.
        parameters = {"sr": 16000, "fmin": float(options["fmin"]),
                      "fmax": float(options["fmax"]), "frame_length": 1024,
                      "hop_length": 160, "center": False, "fill_na": np.nan}
        base["resolved_pyin_parameters"] = {
            "sr": 16000, "fmin": float(options["fmin"]),
            "fmax": float(options["fmax"]), "frame_length": 1024,
            "hop_length": 160, "center": False,
            "fill_na": "NaN (unvoiced; serialized as null in frame values)"}
        f0, voiced, probability = librosa.pyin(y, **parameters)
        framed = np.lib.stride_tricks.sliding_window_view(y, 1024)[::160]
        energy = np.sqrt(np.mean(framed*framed, axis=1, dtype=np.float64))
        if len(energy) != len(f0):
            raise AVError("Pitch and energy frames do not align")
        valid = voiced & np.isfinite(f0) & np.isfinite(probability) & (probability >= options["min_voiced_probability"]) & (energy > 10**(options["energy_threshold_dbfs"]/20))
        track = [{"relative_center_seconds": (i*160+512)/16000,
                  "estimated_f0_hz": float(value) if math.isfinite(float(value)) else None,
                  "voiced_probability": _number(prob), "qualified": bool(keep)}
                 for i, (value, prob, keep) in enumerate(zip(f0, probability, valid))]
        return dict(base, status="QUALIFIED_ESTIMATES" if valid.any() else "NO_QUALIFIED_FRAMES",
                    qualified_frames=int(valid.sum()), total_frames=len(f0),
                    qualified_f0_median_hz=float(np.median(f0[valid])) if valid.any() else None,
                    qualified_f0_p10_hz=float(np.percentile(f0[valid], 10)) if valid.any() else None,
                    qualified_f0_p90_hz=float(np.percentile(f0[valid], 90)) if valid.any() else None,
                    frame_track=track)
    except Exception as exc:
        return dict(base, status="FAILED", error=f"{type(exc).__name__}: {exc}", qualified_f0_median_hz=None)


def cue_features(audio_run_or_audio_path, dialogue_csv, output, *, stream_index=None,
                 pitch=False, confirm_isolated_speech=False, profile="speech", fmin=65.0, fmax=1000.0,
                 min_voiced_probability=0.9, energy_threshold_dbfs=-45.0):
    if profile not in {"speech", "music"}:
        raise AVError("Profile must be speech or music")
    if pitch and (not confirm_isolated_speech or profile != "speech"):
        raise AVError("Pitch requires speech profile and explicit isolated-speech confirmation")
    fmin, fmax = finite(fmin, "fmin", 0), finite(fmax, "fmax", 0)
    probability = finite(min_voiced_probability, "voiced probability", 0)
    threshold = finite(energy_threshold_dbfs, "energy threshold")
    if not 0 < fmin < fmax < 8000 or probability > 1:
        raise AVError("Require 0 < fmin < fmax < 8000 and probability between zero and one")
    try:
        import numpy as np
    except ImportError as exc:
        raise AVError("Cue features require NumPy") from exc
    with output_transaction(output, [audio_run_or_audio_path, dialogue_csv]) as stage:
        dialogue_record = file_record(dialogue_csv, "dialogue_csv")
        cues = read_csv(dialogue_csv)
        with _scratch(stage) as scratch:
            decoded, parent, dependencies = _cue_input(audio_run_or_audio_path, stream_index, scratch)
            dependencies.append(dialogue_record)
            options = {"pitch": pitch, "fmin": fmin, "fmax": fmax, "min_voiced_probability": probability, "energy_threshold_dbfs": threshold}
            method = {"profile": profile, "numpy_version": importlib.metadata.version("numpy"), "sample_rate_hz": decoded["sample_rate_hz"],
                      "channels": decoded["channels"], "channel_layout": decoded["channel_layout"], "channel_policy": "Separate channels; no downmix",
                      "rms": "Whole complete cue, float64 mean square, no amplitude floor; zero amplitude dBFS is null",
                      "framing": "Complete uncentered frames; speech 64 ms/10 ms, music 2048/22050 s and 512/22050 s; rounded to native samples",
                      "spectral": "NumPy rFFT, symmetric Hann, magnitude centroid and 85% rolloff, median over nonempty frames",
                      "zero_crossing_rate": "Fraction of adjacent sample sign-bit changes, measured per channel; not voicing/activity",
                      "low_energy": "Fraction of full RMS frames under the declared threshold; not silence/pause duration or VAD",
                      "pitch_options": options}
            memory = np.memmap(decoded["raw_path"], dtype="<f8", mode="r", shape=(decoded["sample_frames"], decoded["channels"]))
            results, csv_rows, all_bound = [], [], True
            try:
                for number, cue in enumerate(cues, 1):
                    if cue.get("start_seconds") in (None, "") or cue.get("end_seconds") in (None, ""):
                        raise AVError("Every cue requires explicit start_seconds and end_seconds")
                    a, b = interval(cue["start_seconds"], cue["end_seconds"])
                    bound = bool(cue.get("parent_source_sha256"))
                    if bound and (cue["parent_source_sha256"] != parent["parent_source_sha256"] or cue.get("clock") != CLOCK or
                                  abs(finite(cue.get("origin_seconds"), "cue origin")-parent["origin_seconds"]) > 1e-9):
                        raise AVError("Cue source hash/clock/origin does not match the audio parent")
                    all_bound = all_bound and bound
                    selection = _selection(decoded, a, b)
                    row = {"cue_index": cue.get("cue_index", str(number)), "source_start_seconds": a, "source_end_seconds": b,
                           "status": selection["status"], "coverage": selection, "per_channel": None,
                           "name": cue.get("name", ""), "text": cue.get("text_plain", cue.get("text", "")),
                           "dialogue_clock_status": "VERIFIED_SOURCE_BINDING" if bound else "UNBOUND_OPERATOR_TIMES"}
                    if selection["status"] == "COMPLETE":
                        pieces = [np.asarray(memory[p["input_sample_start"]:p["input_sample_end"]]) for p in selection["parts"]]
                        samples = pieces[0] if len(pieces) == 1 else np.concatenate(pieces, axis=0)
                        row["per_channel"] = []
                        for channel in range(decoded["channels"]):
                            value = _features(samples[:, channel], decoded["sample_rate_hz"], profile, options)
                            value["channel_index"] = channel
                            if "frame_track" in value["pitch"]:
                                offset = selection["parts"][0]["source_start_seconds"]
                                for frame in value["pitch"]["frame_track"]:
                                    frame["source_center_seconds"] = offset + frame["relative_center_seconds"]
                            row["per_channel"].append(value)
                    for channel in range(decoded["channels"]):
                        value = row["per_channel"][channel] if row["per_channel"] else {}
                        csv_rows.append(dict(parent, dialogue_csv_sha256=dialogue_record["sha256"], cue_index=row["cue_index"],
                                             start_seconds=a, end_seconds=b, status=row["status"], dialogue_clock_status=row["dialogue_clock_status"],
                                             channel_index=channel, rms_dbfs=value.get("rms_dbfs"), sample_peak_dbfs=value.get("sample_peak_dbfs"),
                                             zero_crossing_rate=value.get("zero_crossing_rate"),
                                             spectral_centroid_hz_median=value.get("spectral_centroid_hz_median"),
                                             pitch_status=value.get("pitch", {}).get("status"), qualified_f0_median_hz=value.get("pitch", {}).get("qualified_f0_median_hz")))
                    results.append(row)
            finally:
                # Release mmap before the scratch file is deleted on Windows.
                memory._mmap.close()
            report = dict(parent, schema="ave.cue_features.v1", dialogue_csv_sha256=dialogue_record["sha256"],
                          dialogue_clock_status="VERIFIED_SOURCE_BINDING" if all_bound and cues else "UNBOUND_OPERATOR_TIMES",
                          method=method, rows=results, perceptual_review="NOT_PERFORMED",
                          limitations=["Subtitle names do not isolate or identify an audible speaker", "Missing/partial windows have null measurements", "Acoustics do not establish emotion or narrative intent"])
            write_json(stage/"features.json", report)
            fields = ["parent_source_sha256", "parent_stream_index", "clock", "origin_seconds", "dialogue_csv_sha256", "cue_index",
                      "start_seconds", "end_seconds", "status", "dialogue_clock_status", "channel_index", "rms_dbfs", "sample_peak_dbfs",
                      "zero_crossing_rate", "spectral_centroid_hz_median", "pitch_status", "qualified_f0_median_hz"]
            write_csv(stage/"features.csv", csv_rows, fields)
        result = finish_run(stage, "cue_features", dependencies, dict(options, profile=profile, confirm_isolated_speech=confirm_isolated_speech),
                            {"result_file": "features.json", "csv_file": "features.csv", "parent_source_sha256": parent["parent_source_sha256"]})
    return result
