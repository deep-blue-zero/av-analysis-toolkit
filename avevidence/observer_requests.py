"""Provider-independent request admission and source-bound clip comparisons."""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct

from .common import AVError, file_record, read_json, sha256
from .inventory import digest_value, identifier, text_value, verify_run
from .auditory_profiles import request_policy, validate_audio_bounds

CONTEXT_KEYS = ("text", "context", "character_names", "expected_emotion", "canonical_text", "speaker_mapping",
                "scene_context", "stage1_discrepancies", "reinspection_question", "context_evidence_ids")
REQUEST_KEYS = {"schema", "request_id", "question", "source_sha256", "stage", "clips", "stage1_run",
                "task_profile", "audio_mode", "allow_long_section", "section_budget_usd", "witness_run", *CONTEXT_KEYS}


def wav_identity(path, *, max_bytes=64*1024*1024):
    """Read bounded WAV framing, including the toolkit's native IEEE-f64 WAVs.

    No decode, conversion, downmix, resampling or guessed sample count occurs.
    File-format admission is separate from a provider successfully accepting it.
    """
    path = Path(path)
    size = path.stat().st_size
    if type(max_bytes) is not int or not 44 <= max_bytes <= 128*1024*1024:
        raise AVError("Observer WAV byte ceiling must be explicit and at most 128 MiB")
    if not 44 <= size <= max_bytes:
        raise AVError("Observer WAV exceeds its bounded byte policy")
    fmt, data = None, None
    with path.open("rb") as stream:
        head = stream.read(12)
        if head[:4] != b"RIFF" or head[8:] != b"WAVE" or struct.unpack("<I", head[4:8])[0]+8 != size:
            raise AVError("Observer input requires a complete little-endian RIFF WAV")
        while stream.tell() < size:
            header = stream.read(8)
            if len(header) != 8:
                raise AVError("Truncated WAV chunk")
            name, count = header[:4], struct.unpack("<I", header[4:])[0]
            if stream.tell()+count+(count % 2) > size:
                raise AVError("WAV chunk exceeds its actual file")
            if name == b"fmt ":
                if fmt is not None or not 16 <= count <= 4096:
                    raise AVError("WAV needs one bounded format chunk")
                chunk = stream.read(count)
                tag, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", chunk[:16])
                if tag == 65534:
                    if len(chunk) < 40 or struct.unpack("<H", chunk[16:18])[0] < 22:
                        raise AVError("Invalid extensible WAV format")
                    tag = struct.unpack("<I", chunk[24:28])[0]
                    if chunk[28:40] != bytes.fromhex("00001000800000aa00389b71"):
                        raise AVError("Unknown extensible WAV sample format")
                if tag not in {1, 3} or bits not in ({8, 16, 24, 32} if tag == 1 else {32, 64}):
                    raise AVError("Unsupported uncompressed WAV sample representation")
                if not 1 <= channels <= 8 or not 1000 <= rate <= 384000 or align != channels*(bits//8) or byte_rate != rate*align:
                    raise AVError("Inconsistent WAV sample framing")
                fmt = {"sample_rate_hz": rate, "channels": channels, "bits_per_sample": bits,
                       "sample_representation": "PCM" if tag == 1 else "IEEE_FLOAT", "block_align": align}
            else:
                if name == b"data":
                    if data is not None:
                        raise AVError("Multiple WAV data chunks are not a continuous witness")
                    data = count
                stream.seek(count, 1)
            stream.seek(count % 2, 1)
    if not fmt or data is None or not data or data % fmt["block_align"]:
        raise AVError("WAV does not contain complete sample frames")
    return {**fmt, "sample_frames": data//fmt["block_align"], "duration_seconds": data/fmt["block_align"]/fmt["sample_rate_hz"]}


def load_witness(folder, label="A", *, policy=None):
    policy = request_policy() if policy is None else policy
    folder = Path(folder).resolve()
    verify_run(folder)
    clip = read_json(folder/"witness.json")
    if not isinstance(clip, dict) or clip.get("schema") != "ave.audio-witness.v1" or clip.get("artifact_path") != "audio.wav":
        raise AVError("Require an existing native audio-witness run")
    digest_value(clip.get("source_sha256")); digest_value(clip.get("artifact_sha256"))
    if type(clip.get("stream_index")) is not int or clip["stream_index"] < 0:
        raise AVError("Listening witness requires the selected absolute audio stream")
    path = folder/"audio.wav"
    if path.is_symlink() or sha256(path) != clip["artifact_sha256"]:
        raise AVError("Listening WAV hash differs from its witness")
    wav = wav_identity(path, max_bytes=policy["max_audio_bytes"])
    identity = clip.get("identity")
    if not isinstance(identity, dict) or any(identity.get(k) != wav[k] for k in ("sample_rate_hz", "channels", "sample_frames")):
        raise AVError("WAV sample framing differs from its native witness identity")
    if not 0 < wav["duration_seconds"] <= policy["max_clip_seconds"]:
        raise AVError("Auditory observer witness exceeds its explicit duration policy; refine the interval before submission")
    mapping = clip.get("review_mapping")
    if (not isinstance(mapping, dict) or mapping.get("parent_source_sha256") != clip["source_sha256"]
        or mapping.get("parent_stream_index") != clip["stream_index"] or mapping.get("artifact_sha256") != clip["artifact_sha256"]
        or mapping.get("artifact_path") != "audio.wav" or mapping.get("modality") != "audio"):
        raise AVError("Listening witness has no matching audio-to-source mapping")
    segments = mapping.get("segments")
    if not isinstance(segments, list) or len(segments) != 1:
        raise AVError("Observer does not concatenate gaps in source coverage")
    seg = segments[0]
    keys = ("parent_start_seconds", "parent_end_seconds", "derivative_start_seconds", "derivative_end_seconds")
    if not isinstance(seg, dict) or any(type(seg.get(k)) not in (int, float) or not math.isfinite(seg[k]) for k in keys):
        raise AVError("Listening witness mapping is malformed")
    tolerance = 1/wav["sample_rate_hz"] + 1e-9
    clock_tolerance = identity.get("timestamp_quantization_tolerance_seconds", 0.)
    adjustment = identity.get("max_timestamp_adjustment_seconds", 0.)
    if (type(clock_tolerance) not in (int,float) or not math.isfinite(clock_tolerance) or not 0 <= clock_tolerance <= .010000001
        or type(adjustment) not in (int,float) or not math.isfinite(adjustment) or not 0 <= adjustment <= clock_tolerance+1e-9):
        raise AVError("Observer admission requires native timing or explicit timestamp uncertainty of at most 10 ms")
    if (seg["parent_start_seconds"] < 0 or seg["derivative_start_seconds"] != 0
        or abs(seg["derivative_end_seconds"]-wav["duration_seconds"]) > tolerance
        or abs(seg["parent_end_seconds"]-seg["parent_start_seconds"]-wav["duration_seconds"]) > tolerance):
        raise AVError("Listening witness does not map every native sample continuously")
    interval = clip.get("source_interval_seconds")
    if (not isinstance(interval, list) or len(interval) != 2
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in interval)
        or not 0 <= interval[0] < interval[1]
        or max(abs(interval[0]-seg["parent_start_seconds"]), abs(interval[1]-seg["parent_end_seconds"])) > tolerance+clock_tolerance):
        raise AVError("Witness requested interval disagrees with the native sample mapping")
    if not isinstance(clip.get("transformations"), list):
        raise AVError("Listening witness must declare its transformations")
    return {**clip, "label": label, "duration_seconds": wav["duration_seconds"], "wav_format": wav,
            "source_clock_uncertainty_seconds": clock_tolerance,
            "actual_source_interval_seconds": [seg["parent_start_seconds"], seg["parent_end_seconds"]],
            "clip_sha256": clip["artifact_sha256"], "execution_audio_path": str(path), "witness_run": str(folder)}


def load_request(request_file, witness_run=None):
    path = Path(request_file).resolve()
    if path.stat().st_size > 65536:
        raise AVError("Observer request exceeds the 64 KiB context budget")
    from .providers.openai_common import strict_json
    try:
        request = strict_json(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        raise AVError("Observer request is not strict UTF-8 JSON") from None
    if not isinstance(request, dict) or set(request)-REQUEST_KEYS:
        raise AVError("Unknown observer request fields; credentials and provider configuration do not belong in evidence")
    if "schema" in request and request["schema"] != "ave.observer-request.v1":
        raise AVError("Unknown observer request schema")
    identifier(request.get("request_id"), "request ID")
    text_value(request.get("question"), "observer question")
    if len(request["question"]) > 2000:
        raise AVError("Observer question exceeds the neutral-question budget")
    policy = request_policy(request)
    stage = request.get("stage", 1)
    if type(stage) is not int or stage not in {1, 2}:
        raise AVError("Observer stage must be 1 or 2")
    if stage == 1 and (any(k in request for k in CONTEXT_KEYS) or "stage1_run" in request):
        raise AVError("Stage 1 must minimize context; use a separate logged stage 2 for interpretation")
    context = {k: request[k] for k in CONTEXT_KEYS if k in request}
    for name in ("canonical_text", "scene_context", "reinspection_question"):
        if name in context:
            text_value(context[name], name)
    if "speaker_mapping" in context:
        mapping = context["speaker_mapping"]
        if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 100:
            raise AVError("Stage 2 speaker mapping must be a bounded explicit label-to-speaker dictionary")
        for key, value in mapping.items():
            identifier(key, "speaker label"); text_value(value, "mapped speaker")
    for name in ("stage1_discrepancies", "context_evidence_ids"):
        if name in context:
            if not isinstance(context[name], list) or len(context[name]) > 100:
                raise AVError("Stage 2 discrepancy/reference fields must be bounded lists")
            for value in context[name]:
                (identifier if name == "context_evidence_ids" else text_value)(value, name)
            if name == "context_evidence_ids" and len(set(context[name])) != len(context[name]):
                raise AVError("Stage 2 context evidence references must be unique")
    if len(json.dumps(context, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 16384:
        raise AVError("Stage 2 supplied context exceeds the 16 KiB budget")
    rows = request.get("clips")
    if rows is None:
        if "witness_run" in request:
            if witness_run:
                raise AVError("Choose request.witness_run or --witness-run, not both")
            if not isinstance(request["witness_run"], str) or not request["witness_run"]:
                raise AVError("Embedded single-witness path must be a nonempty string")
            folder = Path(request["witness_run"])
            witness_run = folder if folder.is_absolute() else path.parent/folder
        if not witness_run:
            raise AVError("Single-clip request requires --witness-run")
        clips = [load_witness(witness_run, policy=policy)]
        if request.get("source_sha256") != clips[0]["source_sha256"]:
            raise AVError("Observer question is bound to a different source")
    else:
        if witness_run or "witness_run" in request:
            raise AVError("Use request.clips for comparisons or --witness-run for a single clip, not both")
        if not isinstance(rows, list) or not 2 <= len(rows) <= 4:
            raise AVError("Comparison requests require 2..4 independent clips")
        clips, labels = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"label", "witness_run"} or not isinstance(row["label"],str) or row["label"] not in {"A", "B", "C", "D"}:
                raise AVError("Comparison clips need opaque A..D labels and witness-run paths only")
            if row["label"] in labels or not isinstance(row["witness_run"], str) or not row["witness_run"]:
                raise AVError("Duplicate comparison label or missing witness run")
            labels.add(row["label"])
            folder = Path(row["witness_run"])
            clips.append(load_witness(folder if folder.is_absolute() else path.parent/folder, row["label"], policy=policy))
        if "source_sha256" in request and any(c["source_sha256"] != request["source_sha256"] for c in clips):
            raise AVError("Comparison request source hash differs from a clip")
    validate_audio_bounds([c["duration_seconds"] for c in clips], policy)
    deps = [file_record(path, "observer_request")]
    for c in clips:
        deps += [file_record(Path(c["witness_run"])/name, "observer_input") for name in ("run.json", "witness.json", "audio.wav")]
    return request, clips, context, deps


def validate_comparison_response(raw, clips):
    from .auditory_observer import validate_response
    from .providers.openai_common import strict_json
    try:
        obj = strict_json(raw)
    except (ValueError, TypeError):
        raise AVError("Comparison response is not strict JSON") from None
    keys = {"clips", "comparison", "alternatives", "interference", "abstentions", "confidence"}
    if not isinstance(obj, dict) or set(obj) != keys or not isinstance(obj["confidence"],str) or obj["confidence"] not in {"high", "medium", "low", "unknown"}:
        raise AVError("Comparison response schema is invalid")
    for name in ("clips", "comparison", "alternatives", "interference", "abstentions"):
        if not isinstance(obj[name], list) or len(obj[name]) > 100:
            raise AVError("Comparison response list is invalid or unbounded")
    expected = {c["label"]: c["duration_seconds"] for c in clips}
    seen = set()
    for row in obj["clips"]:
        if not isinstance(row, dict) or set(row) != {"label", "observation"} or not isinstance(row["label"], str) or row["label"] not in expected or row["label"] in seen:
            raise AVError("Comparison must identify each independent clip exactly once")
        seen.add(row["label"])
        validate_response(json.dumps(row["observation"], allow_nan=False), expected[row["label"]])
    if seen != set(expected):
        raise AVError("Comparison omitted a requested clip")
    for name in ("comparison", "alternatives", "interference", "abstentions"):
        for item in obj[name]:
            text_value(item, name)
    return obj


def public_clip(c):
    return {k: c[k] for k in ("label", "source_sha256", "source_interval_seconds", "stream_index", "clip_sha256",
                              "duration_seconds", "review_mapping", "transformations", "identity", "wav_format",
                              "source_clock_uncertainty_seconds", "actual_source_interval_seconds")}


def source_observations(parsed, clip):
    seg = clip["review_mapping"]["segments"][0]
    offset = seg["parent_start_seconds"]
    return [{**row, "source_start_s": offset+row["start_s"], "source_end_s": offset+row["end_s"],
             "source_sha256": clip["source_sha256"], "stream_index": clip["stream_index"]}
            for row in parsed["observations"]]
