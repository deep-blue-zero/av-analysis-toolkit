"""Local corpus instrumentation joined to source-bound text and candidate alignment."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import shutil
import sys
import time

from .common import (AVError, file_record, finite, finish_run, output_transaction, probe_source,
                     read_json, select_stream, sha256, write_json)
from .performance_alignment import QwenAligner, align_cue, speech_text
from .performance_cache import code_identity, digest
from .performance_contours import get_contours, prepare_audio


def _cues(source, stage, *, subtitle_stream=None, subtitle_run=None, transcript=None):
    if all(x is None for x in (subtitle_stream, subtitle_run, transcript)):
        return [], []
    if sum(x is not None for x in (subtitle_stream, subtitle_run, transcript)) != 1:
        raise AVError("Supply exactly one of --subtitle-stream, --subtitle-run or --transcript")
    dependencies = []
    if subtitle_stream is not None:
        from .subtitles import _embedded
        stream = select_stream(source, "subtitle", subtitle_stream)
        raw = _embedded(source, stream, stage)
        authority, binding = "subtitle", "embedded_original_packet_pts"
    elif subtitle_run is not None:
        from .inventory import verify_run
        folder = Path(subtitle_run)
        verify_run(folder)
        dependencies = [file_record(folder / "subtitles.json", "subtitle_binding")]
        payload = read_json(folder / "subtitles.json")
        if payload["parent_source_sha256"] != source["sha256"] or payload["clock"] != "original_pts_minus_source_origin":
            raise AVError("Subtitle run is not bound to this source and clock")
        raw, authority, binding = payload["rows"], "subtitle", payload["binding_method"]
    else:
        dependencies = [file_record(transcript, "transcript")]
        payload = read_json(transcript)
        if payload.get("source_sha256") not in (None, source["sha256"]):
            raise AVError("Transcript source hash conflicts with media")
        if payload.get("clock", "original_pts_minus_source_origin") != "original_pts_minus_source_origin":
            raise AVError("Transcript must use the toolkit's source-relative clock")
        raw, authority, binding = payload["utterances"], payload.get("text_authority", "unknown"), "operator_supplied_transcript"
    if not raw or len(raw) > 100000:
        raise AVError("Require between 1 and 100000 cues")
    rows, identities = [], set()
    for number, row in enumerate(raw, 1):
        a = finite(row.get("start_s", row.get("start_seconds")), "cue start", 0)
        b = finite(row.get("end_s", row.get("end_seconds")), "cue end", 0)
        if a >= b or (source["duration_seconds"] is not None and b > source["duration_seconds"] + .001):
            raise AVError("Cue lies outside source duration")
        text = row.get("text", row.get("text_plain", ""))
        if not isinstance(text, str) or not text.strip():
            raise AVError("Cue text is empty")
        identity = str(row.get("utterance_id") or row.get("cue_index") or f"cue-{number:06d}")
        if identity in identities:
            raise AVError(f"Duplicate cue identity: {identity}")
        identities.add(identity)
        cleaned, speaker, kind, flags = speech_text(text)
        if "alignment_text" in row:
            if not isinstance(row["alignment_text"], str):
                raise AVError("alignment_text must be a string")
            cleaned = row["alignment_text"].strip()
        kind = row.get("kind", kind)
        if kind not in {"speech_candidate", "non_speech_caption"}:
            raise AVError("Cue kind must be speech_candidate or non_speech_caption")
        if len(cleaned) > 4000:
            raise AVError("Alignment text exceeds the per-cue character limit")
        rows.append({"cue_id": identity, "start_s": a, "end_s": b, "text": text,
                     "speaker": row.get("speaker") or row.get("name") or speaker,
                     "alignment_text": cleaned, "kind": kind, "flags": flags,
                     "text_authority": authority, "text_binding": binding,
                     "boundary_origin": row.get("boundary_origin", "subtitle_boundary" if authority == "subtitle" else "manual_scene_boundary"),
                     "supplied_notes": row.get("notes", [])})
    for previous, current in zip(rows, rows[1:]):
        if (previous["alignment_text"] and previous["alignment_text"] == current["alignment_text"]
                and current["start_s"] <= previous["end_s"] + .05):
            for row in (previous, current):
                if "adjacent_duplicate_caption_text" not in row["flags"]:
                    row["flags"].append("adjacent_duplicate_caption_text")
    return rows, dependencies


def cue_summary(tracks, cue):
    import numpy as np
    a, b = cue["start_s"], cue["end_s"]
    units = cue["alignment"].get("units", [])
    basis = "supplied_cue_window"
    if units and units[-1]["end_s"] > units[0]["start_s"]:
        a, b = units[0]["start_s"], units[-1]["end_s"]
        basis = "aligned_candidate_span"
    mask = (tracks["time_s"] >= a) & (tracks["time_s"] < b)
    f0, energy = tracks["pitch_hz"][mask], tracks["rms_dbfs"][mask]
    voiced = f0[np.isfinite(f0)]
    valid_pitch = len(voiced) >= 3
    boundary_origin = "forced_aligned_candidate" if basis == "aligned_candidate_span" else cue.get("boundary_origin", "estimated_boundary")
    flags = ["SPEAKER_ISOLATION_NOT_ESTABLISHED"]
    if boundary_origin == "subtitle_boundary": flags.append("SUBTITLE_BOUNDARY_ONLY")
    if boundary_origin not in {"manual_verified", "forced_aligned_verified"}: flags.append("ALIGNMENT_UNCERTAIN")
    if len(f0) == 0 or len(voiced)/max(1,len(f0)) < .4: flags.append("LOW_F0_CONFIDENCE")
    return {"start_s": a, "end_s": b, "interval_basis": basis, "contour_rows": int(mask.sum()),
            "f0_median_hz": float(np.median(voiced)) if valid_pitch else None,
            "f0_p10_hz": float(np.quantile(voiced, .1)) if valid_pitch else None,
            "f0_p90_hz": float(np.quantile(voiced, .9)) if valid_pitch else None,
            "pitch_span_semitones": float(12*np.log2(np.quantile(voiced,.9)/np.quantile(voiced,.1))) if valid_pitch else None,
            "boundary_origin": boundary_origin, "quality_flags": flags, "speaker_baseline_eligible": False,
            "voiced_frame_fraction": float(len(voiced) / len(f0)) if len(f0) else None,
            "rms_dbfs_energy_mean": float(10 * np.log10(np.mean(10 ** (energy / 10)))) if len(energy) else None,
            "rms_dbfs_mean": float(np.mean(energy)) if len(energy) else None,
            "rms_dbfs_peak_window": float(np.max(energy)) if len(energy) else None,
            "definition": "rms_dbfs_energy_mean is 10 log10(mean window power), used for search; rms_dbfs_mean is the arithmetic mean of log levels. Both aggregate overlapping windows, not exact whole-cue RMS or calibrated vocal SPL",
            "speaker_isolation": "NOT_ESTABLISHED"}


def performance(input, output, *, cache_dir, audio_stream=None, subtitle_stream=None,
                subtitle_run=None, transcript=None, model_dir=None, device="cuda", channel="mean",
                hop_ms=10., window_ms=40., pitch_floor=75., pitch_ceiling=600.,
                max_duration=7200., max_alignment_window=30., alignment_padding=.2,
                timestamp_tolerance_ms=None, progress=True, coverage_mode="strict",
                coverage_end=None, max_output_bytes=2147483648, decode_timeout_s=180):
    try:
        import numpy as np
        import parselmouth  # Check the optional dependency before any media work.
    except ImportError as exc:
        raise AVError("Install the toolkit's [performance] or [alignment] extra for this command") from exc
    started = time.perf_counter()
    if device not in {"cpu", "cuda"}:
        raise AVError("Device must be cpu or cuda")
    if timestamp_tolerance_ms is not None and not 0 <= finite(timestamp_tolerance_ms) <= 10:
        raise AVError("Timestamp tolerance must be between 0 and 10 ms")
    for value, name in ((max_duration, "max duration"), (hop_ms, "hop"), (window_ms, "window")):
        if finite(value, name, 0) <= 0:
            raise AVError(f"{name} must be positive")
    source = probe_source(input)
    stream = select_stream(source, "audio", audio_stream)
    if coverage_mode not in {"strict", "decoded"}:
        raise AVError("Coverage mode must be strict or decoded")
    if coverage_mode == "decoded":
        if coverage_end is None or not 0 < finite(coverage_end) <= max_duration:
            raise AVError("Decoded coverage requires an explicit positive --coverage-end within --max-duration")
    elif coverage_end is not None:
        raise AVError("--coverage-end requires --coverage-mode decoded")
    if coverage_mode == "strict" and (source["duration_seconds"] is None or source["duration_seconds"] > max_duration):
        raise AVError("Source duration is unknown or exceeds --max-duration")
    cache_root = Path(cache_dir).resolve()
    target = Path(output).resolve()
    if target == cache_root or cache_root.is_relative_to(target) or target.is_relative_to(cache_root):
        raise AVError("Cache and published run directories must be separate")
    sources = [source]
    inputs = [input] + [p for p in (subtitle_run, transcript, model_dir) if p]
    with output_transaction(output, inputs) as stage:
        cues, dependencies = _cues(source, stage, subtitle_stream=subtitle_stream,
                                  subtitle_run=subtitle_run, transcript=transcript)
        sources += dependencies
        text_key = digest(cues)
        aligner = QwenAligner(model_dir, device) if model_dir else None
        timings = {"probe_text_model_identity_s": time.perf_counter() - started}
        step = time.perf_counter()
        audio_path, audio_metadata, audio_hit, audio_key = prepare_audio(source, stream, cache_root,
            channel, timestamp_tolerance_ms, coverage_end=coverage_end,
            max_output_bytes=max_output_bytes, decode_timeout_s=decode_timeout_s)
        from .audio import _selection
        coverage = {"mode": coverage_mode, "original_duration_seconds": source["duration_seconds"],
            "original_duration_basis": source.get("duration_basis"),
            "intervals_seconds": [[s["source_start_seconds"], s["source_end_seconds"]] for s in audio_metadata["segments"]],
            "decode_scope": audio_metadata.get("decode_scope"),
            "prior_default_refusal": "unknown_duration" if source["duration_seconds"] is None else None}
        decoded_support = dict(audio_metadata["identity"], segments=audio_metadata["segments"])
        for cue in cues:
            if _selection(decoded_support, cue["start_s"], cue["end_s"])["status"] != "COMPLETE":
                raise AVError(f"Cue {cue['cue_id']} crosses an unresolved audio gap or lies outside decoded coverage")
        timings["audio_cache_s"] = time.perf_counter() - step
        step = time.perf_counter()
        contour_path, contour_hit, contour_key = get_contours(audio_path, audio_metadata, audio_key, cache_root,
            hop_ms=hop_ms, window_ms=window_ms, floor=pitch_floor, ceiling=pitch_ceiling)
        timings["contours_s"] = time.perf_counter() - step
        tracks_file = np.load(contour_path / "contours.npz", allow_pickle=False)
        tracks = {k: tracks_file[k] for k in tracks_file.files}
        tracks_file.close()
        samples = np.load(audio_path / "mono.npy", mmap_mode="r", allow_pickle=False)
        alignment_started = time.perf_counter()
        try:
            for index, cue in enumerate(cues):
                if aligner:
                    try:
                        cue["alignment"] = align_cue(cue, samples, audio_metadata, aligner=aligner,
                            cache_root=cache_root, audio_key=audio_key, padding_s=alignment_padding,
                            max_window_s=max_alignment_window)
                    except (AVError, RuntimeError, ValueError) as exc:
                        cue["alignment"] = {"status": "FAILED", "units": [], "cache_hit": False, "error": str(exc)}
                else:
                    cue["alignment"] = {"status": "NOT_REQUESTED", "units": [], "cache_hit": False}
                cue["measurements"] = cue_summary(tracks, cue)
                if progress and (index == 0 or (index + 1) % 25 == 0 or index + 1 == len(cues)):
                    print(f"Processed {index + 1}/{len(cues)} cues", file=sys.stderr, flush=True)
        finally:
            del samples
        timings["alignment_and_cue_summary_s"] = time.perf_counter() - alignment_started
        statuses = dict(Counter(c["alignment"]["status"] for c in cues))
        if aligner and statuses.get("FAILED", 0) > 0 and statuses.get("FAILED") == sum(c["kind"] != "non_speech_caption" for c in cues):
            raise AVError("Every speech alignment failed: " + next(c["alignment"]["error"] for c in cues if c["alignment"]["status"] == "FAILED"))
        alignment_key = {"model": aligner.identity if aligner else None, "device": device if aligner else None,
                         "padding_s": alignment_padding, "max_window_s": max_alignment_window}
        report = {"schema": "ave.performance.v1", "source": source, "audio_stream_index": stream["index"],
            "clock": audio_metadata["clock"], "source_origin_seconds": source["origin_seconds"],
            "audio": audio_metadata, "coverage": coverage, "cues": cues, "alignment_counts": statuses,
            "alignment_configuration": alignment_key,
            "performance_key": digest({"audio": audio_key, "contours": contour_key, "text": text_key,
                "alignment": alignment_key, "code": code_identity("performance.py", "performance_alignment.py")}),
            "cache": {"audio_key": audio_key, "audio_hit": audio_hit, "contour_key": contour_key,
                      "contour_hit": contour_hit, "alignment_hits": sum(c["alignment"]["cache_hit"] for c in cues)},
            "timings_s": timings, "epistemic_class": "COMPUTED_AND_TEXT_CANDIDATES",
            "perceptual_review": "NOT_PERFORMED",
            "limitations": ["Alignment conditions on text; it does not verify transcription or speaker identity",
                "Subtitles may contain unspoken text; original wording and timing are retained",
                "Pitch is mixed-signal periodicity, not certified character F0 or emotion",
                "Mean channel downmix can cancel signals; original source remains the listening reference",
                "No model-based listening, speech separation, diarization or ASR was performed"]}
        shutil.copyfile(contour_path / "contours.npz", stage / "contours.npz")
        shutil.copyfile(contour_path / "contours.json", stage / "contours.json")
        from .performance_view import render_view
        render_view(stage / "performance.html", report, tracks)
        report["timings_s"]["before_manifest_s"] = time.perf_counter() - started
        write_json(stage / "performance.json", report)
        with (stage / "cues.jsonl").open("x", encoding="utf-8") as out:
            for cue in cues:
                out.write(json.dumps(cue, ensure_ascii=False, allow_nan=False) + "\n")
        return finish_run(stage, "performance", sources,
            {"audio_stream": stream["index"], "channel": channel, "alignment_requested": bool(aligner),
             "explicit_timestamp_tolerance_ms": timestamp_tolerance_ms},
            {"result_file": "performance.json", "view_file": "performance.html", "cue_count": len(cues),
             "alignment_counts": statuses, "cache": report["cache"],
             "elapsed_before_manifest_s": report["timings_s"]["before_manifest_s"]})
