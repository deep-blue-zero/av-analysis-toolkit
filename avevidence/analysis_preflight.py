"""Modality-aware admission and separate capability receipts; no implicit listening."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .common import (AVError, file_record, finite, finish_run, output_transaction, probe_source,
                     read_json, select_stream, sha256, write_json)
from .inventory import digest_value
from .performance_cache import versions

SOURCE_CLASSES = {"isolated_voice_asset", "isolated_music_asset", "mixed_episode_audio", "mixed_game_audio",
                  "screen_recording", "derived_clip", "unknown"}
CAPABILITY_STATUSES = {"NOT_CONFIGURED", "NOT_TESTED", "NOT_PRESENT", "INSTALLED_NOT_TESTED", "UNSUPPORTED_ROUTE",
    "DECODE_FAILED", "REQUEST_ACCEPTED", "OBSERVATION_COMPLETED", "PROBE_PASSED", "PROBE_FAILED", "UNRESOLVED"}


def capability_receipt(value):
    if not isinstance(value, dict) or value.get("status") not in CAPABILITY_STATUSES:
        raise AVError("Unknown capability receipt status")
    for key in ("interface", "model_revision", "adapter_revision"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise AVError(f"Capability receipt requires {key}")
    if "fixture_sha256" in value:
        digest_value(value["fixture_sha256"])
    if value["status"] == "PROBE_PASSED":
        if value.get("semantic_probe_passed") is not True or not value.get("fixture_sha256"):
            raise AVError("Audio acceptance alone cannot establish semantic capability")
    return value


def _optional_module(name):
    try:
        return bool(importlib.util.find_spec(name))
    except (ValueError, ModuleNotFoundError):
        return False


def inspect_source(input, *, audio_stream=None, video_stream=None, external_audio=None,
                   external_audio_offset=None, transcript=None, screenshots_manifest=None,
                   direct_receipt=None, observer_receipt=None, audio_class="unknown",
                   probe_seconds=2., timestamp_tolerance_ms=None, stage=None):
    if audio_class not in SOURCE_CLASSES:
        raise AVError("Unknown audio source class")
    probe_seconds = finite(probe_seconds, "preflight probe duration", 0)
    if not 0 < probe_seconds <= 30:
        raise AVError("Preflight decode probe must be bounded to 0..30 s")
    source = probe_source(input)
    audio_source = probe_source(external_audio) if external_audio else source
    audio = select_stream(audio_source, "audio", audio_stream, required=False)
    video = select_stream(source, "video", video_stream, required=False)
    deps = [source] + ([audio_source] if external_audio else [])
    if not audio and not video:
        raise AVError("Input has no selected audiovisual stream")
    mappings, flags = [], []
    if external_audio:
        if not audio:
            raise AVError("External audio source has no selected audio stream")
        if external_audio_offset is None:
            flags.append("UNMAPPED_EXTERNAL_SOURCE")
        else:
            offset = finite(external_audio_offset, "external audio offset")
            mappings.append({"from_source_sha256": audio_source["sha256"], "to_source_sha256": source["sha256"],
                "conversion": "primary_source_time = audio_source_time + offset_s", "offset_s": offset,
                "basis": "operator_declared", "residual_s": None, "av_sync_verified": False})
    caps = {
        "file_readable": {"status": "PROBE_PASSED", "basis": "local bytes hashed; no perceptual implication"},
        "audio_decodable": {"status": "NOT_TESTED" if audio else "NOT_PRESENT"},
        "waveform_measurable": {"status": "NOT_TESTED" if audio else "NOT_PRESENT"},
        "visual_extraction": {"status": "NOT_TESTED" if video else "NOT_PRESENT"},
        "audio_playable": {"status": "NOT_TESTED", "basis": "Player presentation is separate from decoding"},
        "audio_accepted_by_model": {"status": "NOT_TESTED"},
        "direct_audio_perception": {"status": "NOT_TESTED"},
        "auditory_observer": {"status": "NOT_CONFIGURED"},
        "human_listening": {"status": "NOT_TESTED"},
        "source_separation": {"status": "INSTALLED_NOT_TESTED" if _optional_module("demucs") else "NOT_CONFIGURED"},
        "ASR": {"status": "INSTALLED_NOT_TESTED" if _optional_module("whisper") else "NOT_CONFIGURED"},
        "forced_alignment": {"status": "INSTALLED_NOT_TESTED" if _optional_module("qwen_asr") else "NOT_CONFIGURED"},
    }
    decoded = None
    if audio and stage is not None:
        from .audio import _decode, _scratch
        start_bound = next((b.get("canonical_start_seconds") for b in audio_source["stream_timeline_bounds"]
                            if b["stream_index"] == audio["index"]), 0) or 0
        start = max(0., start_bound)
        end = min(start+probe_seconds, audio_source["duration_seconds"]) if audio_source["duration_seconds"] is not None else start+probe_seconds
        try:
            with _scratch(stage) as scratch:
                decoded = _decode(audio_source, audio, scratch, "capability", start=start, end=end,
                    max_output_bytes=67108864, timeout_s=60,
                    timestamp_tolerance_seconds=None if timestamp_tolerance_ms is None else timestamp_tolerance_ms/1000)
                decoded.pop("raw_path")
            caps["audio_decodable"] = {"status": "PROBE_PASSED", "scope": [start, end],
                "coverage": decoded["segments"], "whole_source_tested": False}
            caps["waveform_measurable"] = {"status": "PROBE_PASSED", "basis": "Native PCM sample/frame agreement", "whole_source_tested": False}
        except AVError as exc:
            caps["audio_decodable"] = {"status": "DECODE_FAILED", "error": str(exc), "scope": [start, end]}
            caps["waveform_measurable"] = {"status": "UNRESOLVED"}
    for path, key in ((direct_receipt, "direct_audio_perception"), (observer_receipt, "auditory_observer")):
        if path:
            deps.append(file_record(path, "external_capability_receipt"))
            value = capability_receipt(read_json(path))
            caps[key] = value
            if key == "direct_audio_perception":
                caps["audio_accepted_by_model"] = {"status": "UNSUPPORTED_ROUTE" if value["status"] == "UNSUPPORTED_ROUTE" else
                    "REQUEST_ACCEPTED" if value["status"] in {"REQUEST_ACCEPTED", "OBSERVATION_COMPLETED", "PROBE_PASSED"} else "UNRESOLVED"}
    text_present = bool(transcript or any(s.get("codec_type") == "subtitle" for s in source["streams"]))
    if transcript:
        deps.append(file_record(transcript, "canonical_text"))
    stills = None
    if screenshots_manifest:
        deps.append(file_record(screenshots_manifest, "external_still_manifest"))
        stills = read_json(screenshots_manifest)
        if stills.get("parent_source_sha256") not in {source["sha256"], audio_source["sha256"]}:
            flags.append("UNMAPPED_EXTERNAL_SOURCE")
        if stills.get("clock") != "original_pts_minus_source_origin":
            flags.append("UNMAPPED_EXTERNAL_SOURCE")
        if not isinstance(stills.get("rows"),list) or len(stills["rows"])>2000:
            raise AVError("Still admission requires a bounded frame manifest")
        for row in stills["rows"]:
            path=Path(row["path"])
            path=path.resolve() if path.is_absolute() else (Path(screenshots_manifest).resolve().parent/path).resolve()
            item=file_record(path,"visual_point")
            if item["sha256"] != row.get("sha256"):
                raise AVError("External still artifact hash mismatch")
            finite(row.get("source_seconds"),"still point",0)
            deps.append(item)
        if not stills["rows"]:
            stills=None
    summary = [{k: s.get(k) for k in ("index", "codec_type", "codec_name", "sample_rate", "channels", "channel_layout",
        "avg_frame_rate", "r_frame_rate", "time_base", "start_pts", "duration_ts")} for s in source["streams"]]
    return {"schema": "ave.analysis-preflight.v1", "source": source, "audio_source": audio_source,
        "modalities": {"audio": bool(audio), "video": bool(video), "text": text_present, "visual_stills": bool(video or stills)},
        "selected_streams": {"audio": audio["index"] if audio else None, "video": video["index"] if video else None},
        "stream_counts": {kind: sum(s.get("codec_type") == kind and not s.get("disposition", {}).get("attached_pic") for s in source["streams"])
                          for kind in ("audio", "video", "subtitle")},
        "streams": summary, "audio_class": audio_class, "capabilities": caps, "decoded_probe": decoded,
        "source_clock": {"name": "original_pts_minus_source_origin", "origin_s": source["origin_seconds"],
            "origin_basis": source["origin_basis"], "canonical_duration_s": source["duration_seconds"],
            "container_reported_duration_s": source["format_duration_seconds"],
            "discontinuities": "Only the bounded decoded probe is tested; whole-source continuity is not inferred"},
        "external_clock_mappings": mappings, "quality_flags": sorted(set(flags)),
        "direct_audio_perception": "unavailable" if caps["direct_audio_perception"]["status"] == "UNSUPPORTED_ROUTE" else "unverified",
        "trust_boundary": "External capability declarations and semantic probe results are attributed receipts; decoder success does not imply model listening"}, deps


def preflight(input, output, **options):
    paths = [input] + [v for k,v in options.items() if k in {"external_audio", "transcript", "screenshots_manifest", "direct_receipt", "observer_receipt"} and v]
    with output_transaction(output, paths) as stage:
        report, deps = inspect_source(input, stage=stage, **options)
        write_json(stage / "capabilities.json", report)
        parameters = {k: str(v) if isinstance(v,Path) else v for k,v in options.items()}
        return finish_run(stage, "preflight", deps, parameters, {"result_file": "capabilities.json", "modalities": report["modalities"]})


def resolve_source(source_sha256, bindings, *, allowed_roots):
    """Explicit hash-keyed relocation; never search a drive or trust an old basename."""
    digest_value(source_sha256)
    roots = [Path(p).resolve() for p in allowed_roots]
    path = Path(bindings.get(source_sha256, "")).resolve()
    if not roots or not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise AVError("Source is unavailable inside the explicitly allowed execution roots")
    if sha256(path) != source_sha256:
        raise AVError("Relocated source hash mismatch")
    return path
