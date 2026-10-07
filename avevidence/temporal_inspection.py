"""Bounded adaptive source-frame inspection; extraction never establishes review."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .common import (AVError, file_record, finish_run, interval, output_transaction,
                     read_json, sha256, write_json)

PROFILES = {"TEMPORAL_LOW": 2, "TEMPORAL_MEDIUM": 8, "TEMPORAL_HIGH": 12,
            "FRAME_COMPLETE_WINDOW": None}
ALIASES = {"LOW": "TEMPORAL_LOW", "MEDIUM": "TEMPORAL_MEDIUM", "HIGH": "TEMPORAL_HIGH"}
QUESTION_TYPES = {"GENERAL_MOTION", "CONTACT_ORDER", "EXACT_CONTACT", "AV_SYNC"}
DEFAULT_LIMITS = {"max_window_seconds": 30., "max_complete_window_seconds": 3.,
                  "max_frames": 360, "max_pixels": 250_000_000,
                  "max_source_pixels": 8_388_608, "max_output_bytes": 1_073_741_824,
                  "max_index_source_seconds": 1800., "max_index_frames": 200_000,
                  "wall_timeout_seconds": 120.}


def _number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise AVError(f"Invalid {label}")
    return float(value)


def _text(value, label, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AVError(f"Require a bounded nonempty {label}")
    return value


def _profile(value):
    value = ALIASES.get(value, value)
    if value not in PROFILES:
        raise AVError("Unknown temporal inspection profile")
    return value


def _limits(overrides):
    if overrides is not None and (not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_LIMITS)):
        raise AVError("Unknown temporal resource limit")
    result = dict(DEFAULT_LIMITS, **(overrides or {}))
    for key, value in result.items():
        _number(value, key, .001)
    for key in ("max_frames", "max_pixels", "max_source_pixels", "max_output_bytes", "max_index_frames"):
        if type(result[key]) is not int:
            raise AVError(f"{key} must be an integer")
    if result["max_frames"] > 10000 or result["max_complete_window_seconds"] > 60 or result["wall_timeout_seconds"] > 3600:
        raise AVError("Temporal resource limits exceed hard safety ceilings")
    return result


def _bounds(start, end):
    if start is None or end is None:
        raise AVError("Temporal inspection requires an explicit bounded interval")
    return list(interval(start, end))


def _critical(value, requested):
    if value is None:
        return list(requested)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AVError("Critical interval must contain start and end")
    a, b = _bounds(*value)
    if a < requested[0] - 1e-9 or b > requested[1] + 1e-9:
        raise AVError("Critical interval must be within the requested interval")
    return [a, b]


def _prepare_arguments(question, start, end, profile, question_type, critical_interval, width, limits):
    question = _text(question, "temporal question")
    profile = _profile(profile)
    if question_type not in QUESTION_TYPES:
        raise AVError("Unknown temporal question type")
    if type(width) is not int or not 0 <= width <= 8192:
        raise AVError("Temporal width must be an integer from 0 to 8192")
    limits = _limits(limits)
    requested = _bounds(start, end)
    critical = _critical(critical_interval, requested)
    seconds = requested[1] - requested[0]
    if seconds > limits["max_window_seconds"]:
        raise AVError("Temporal window exceeds the duration ceiling before source decode")
    fps = PROFILES[profile]
    if profile == "FRAME_COMPLETE_WINDOW" and seconds > limits["max_complete_window_seconds"]:
        raise AVError("Frame-complete inspection requires a short supplied window")
    if fps is not None and math.ceil(seconds * fps - 1e-10) > limits["max_frames"]:
        raise AVError("Requested temporal sample count exceeds the frame ceiling before source decode")
    return {"question": question, "question_type": question_type, "profile": profile,
            "interval_seconds": requested, "critical_interval_seconds": critical,
            "fps": fps, "width": width, "limits": limits}


def _perform(input_path, stage, arguments, stream_index=None, frame_index_run=None):
    """Worker-only reuse of the established source-clock/geometry extraction path."""
    from . import common, visual
    from .common import probe_source, select_stream
    begun = time.monotonic()
    original_run = common.run

    def bounded_run(argv, **kwargs):
        remaining = arguments["limits"]["wall_timeout_seconds"] - (time.monotonic() - begun)
        if remaining <= 0:
            raise AVError("Temporal wall-clock ceiling exhausted")
        kwargs["timeout"] = min(kwargs.get("timeout", 180), remaining)
        return original_run(argv, **kwargs)

    # This assignment is confined to a dedicated child interpreter; other callers
    # cannot inherit a patched runner or a different deadline.
    common.run = visual.run = bounded_run
    stage = Path(stage)
    limits = arguments["limits"]
    if frame_index_run:
        source, stream, entries, _, index_reference = visual._cached_frame_index(frame_index_run, input_path, stream_index)
        index_path = Path(frame_index_run).resolve()
    else:
        source = probe_source(input_path)
        stream = select_stream(source, "video", stream_index)
        visual._geometry(stream, arguments["width"])
        if source["duration_seconds"] is None or source["duration_seconds"] > limits["max_index_source_seconds"]:
            raise AVError("Uncached source exceeds index-generation ceiling; supply a verified frame-index run")
        if stream.get("width", 0) * stream.get("height", 0) > limits["max_source_pixels"]:
            raise AVError("Source geometry exceeds the decoded pixel ceiling")
        # A known average rate can reject obviously oversized indexing before
        # show_frames. VFR counts are subsequently checked against actual rows.
        from fractions import Fraction
        try:
            average = float(Fraction(stream.get("avg_frame_rate", "0/1")))
        except (ValueError, ZeroDivisionError):
            average = 0
        if average > 0 and source["duration_seconds"] * average > limits["max_index_frames"]:
            raise AVError("Uncached source exceeds estimated index-frame ceiling")
        index_path = stage / "frame-index"
        visual.build_frame_index(input_path, index_path, stream_index=stream["index"])
        source, stream, entries, _, index_reference = visual._cached_frame_index(index_path, input_path, stream["index"])
    geometry = visual._geometry(stream, arguments["width"])
    if len(entries) > limits["max_index_frames"]:
        raise AVError("Source index exceeds its actual frame ceiling")
    if stream["width"] * stream["height"] > limits["max_source_pixels"]:
        raise AVError("Source geometry exceeds the decoded pixel ceiling")
    a, b = arguments["interval_seconds"]
    source_rows = [{"source_frame_index": row["index"], "source_pts": row["pts"],
                    "original_pts_seconds": row["original"], "source_seconds": row["time"],
                    "decoded_duration_seconds": row["duration"]} for row in entries if a-1e-10 <= row["time"] < b-1e-10]
    expected_count = len(source_rows) if arguments["fps"] is None else math.ceil((b-a)*arguments["fps"]-1e-10)
    if not source_rows or expected_count > limits["max_frames"]:
        raise AVError("Temporal actual selection exceeds the frame ceiling or has no source frames")
    w = arguments["width"] or stream["width"]
    h = math.ceil(stream["height"] * w / stream["width"])
    pixel_bound = expected_count * w * h
    if pixel_bound > limits["max_pixels"] or pixel_bound * 4 + expected_count * 4096 > limits["max_output_bytes"]:
        raise AVError("Temporal predicted output exceeds its pixel/byte ceiling before image decode")
    frame_output = stage / "frame-sequence"
    visual.extract_frames(input_path, frame_output, mode="source" if arguments["fps"] is None else "dense",
                          start=a, end=b, fps=arguments["fps"] or 1, width=arguments["width"],
                          stream_index=stream["index"], frame_index_run=index_path)
    _, frame_manifest, frames, _, frame_record = visual.load_verified_run(frame_output, {"extract_frames"})
    rows = [dict(row, path="frame-sequence/"+row["path"]) for row in frames["rows"]]
    if sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()) > limits["max_output_bytes"]:
        raise AVError("Temporal actual output exceeds its byte ceiling")
    ca, cb = arguments["critical_interval_seconds"]
    expected_critical = [row["source_frame_index"] for row in source_rows if ca-1e-10 <= row["source_seconds"] < cb-1e-10]
    selected = sorted(set(row["source_frame_index"] for row in rows))
    full = selected == [row["source_frame_index"] for row in source_rows]
    report = {"schema": "ave.temporal-inspection.v1", "status": "GENERATED_NOT_REVIEWED",
              "source_sha256": source["sha256"], "stream_index": stream["index"], "clock": visual.CLOCK,
              "origin_seconds": source["origin_seconds"], "source_time_base": frames["rows"][0]["source_time_base"],
              "question": arguments["question"], "question_type": arguments["question_type"],
              "interval_seconds": [a,b], "critical_interval_seconds": [ca,cb],
              "profile": arguments["profile"], "nominal_sampling_fps": arguments["fps"],
              "frame_rows": rows, "source_frames_in_interval": source_rows,
              "expected_critical_source_frame_indices": expected_critical,
              "unique_frame_count": len(selected), "request_row_count": len(rows),
              "source_frame_count_in_interval": len(source_rows), "all_source_frames_extracted": full,
              "frame_index_reference": dict(index_reference, run_manifest_path="frame-index/run.json") if not frame_index_run else index_reference,
              "frame_sequence_manifest": {"path": "frame-sequence/run.json", "sha256": frame_record["sha256"]},
              "extraction_parameters": frame_manifest["parameters"], "decode_strategy": frames["decode_strategy"],
              "geometry": geometry, "limits": limits,
              "resource_usage": {"source_index_frames": len(entries), "predicted_output_pixels": pixel_bound,
                                 "worker_wall_seconds": time.monotonic()-begun},
              "assessment": {"status": "OPEN", "actual_review_declared": False,
                             "reason": "Generated frames do not establish inspected motion, order, contact or synchrony"},
              "limitations": ["Nominal sampling selects the first actual source frame at/after each request; VFR gaps and duplicate selections remain explicit",
                              "Frame-complete refers to original source frames in the half-open window, not physical time between frames",
                              "HDR, high-depth, wide-gamut, rotation and non-square pixels remain refused by the inherited geometry policy",
                              "No native video model is invoked; adequacy requires a separate attributed review declaration"]}
    write_json(stage / "temporal_report.json", report)
    write_json(stage / "worker-sources.json", frame_manifest["sources"])


def _kill_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def extract_temporal_inspection(input, output, *, question, start, end, profile="TEMPORAL_LOW",
                                question_type="GENERAL_MOTION", critical_interval=None,
                                stream_index=None, frame_index_run=None, width=960, limits=None):
    arguments = _prepare_arguments(question,start,end,profile,question_type,critical_interval,width,limits)
    if stream_index is not None and (type(stream_index) is not int or stream_index < 0):
        raise AVError("Video stream index must be a nonnegative integer")
    dependencies = [input] + ([frame_index_run] if frame_index_run else [])
    with output_transaction(output, inputs=dependencies) as stage:
        request = stage / "worker-request.json"
        write_json(request, {"input": str(Path(input).resolve()), "stage": str(stage), "arguments": arguments,
                             "stream_index": stream_index,
                             "frame_index_run": str(Path(frame_index_run).resolve()) if frame_index_run else None})
        package_root = str(Path(__file__).resolve().parents[1])
        code = "import sys;sys.path.insert(0,sys.argv[1]);from avevidence.temporal_inspection import _worker;_worker(sys.argv[2])"
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", "-c", code, package_root, str(request)], **kwargs)
        try:
            stdout, stderr = process.communicate(timeout=arguments["limits"]["wall_timeout_seconds"])
        except subprocess.TimeoutExpired as exc:
            _kill_tree(process)
            process.communicate(timeout=10)
            raise AVError("Temporal extraction wall-clock ceiling exhausted; no successful packet published") from exc
        if process.returncode:
            raise AVError("Temporal extraction refused: "+(stderr or stdout)[-3000:])
        sources = read_json(stage / "worker-sources.json")
        request.unlink()
        (stage / "worker-sources.json").unlink()
        report = read_json(stage / "temporal_report.json")
        return finish_run(stage, "temporal_inspection", sources, arguments,
                          {"result_file": "temporal_report.json", "temporal_status": "OPEN",
                           "profile": report["profile"], "frame_count": report["unique_frame_count"]})


def _worker(request):
    try:
        data = read_json(request)
        _perform(data["input"], data["stage"], data["arguments"], data["stream_index"], data["frame_index_run"])
    except (AVError, OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)


def load_temporal_inspection(directory):
    from .visual import load_verified_run
    root, manifest, report, artifacts, record = load_verified_run(directory, {"temporal_inspection"})
    if (report.get("schema") != "ave.temporal-inspection.v1" or report.get("clock") != "original_pts_minus_source_origin"
            or report.get("status") != "GENERATED_NOT_REVIEWED"
            or report.get("assessment",{}).get("status") != "OPEN"
            or report.get("assessment",{}).get("actual_review_declared") is not False):
        raise AVError("Unsupported temporal inspection report")
    sources = manifest.get("sources", [])
    if not sources or report.get("source_sha256") != sources[0].get("sha256"):
        raise AVError("Temporal report source binding differs")
    from .common import verify_source
    verify_source(sources[0])
    reference = report.get("frame_index_reference", {})
    reference_path = Path(reference.get("run_manifest_path", ""))
    index_path = (reference_path if reference_path.is_absolute() else root/reference_path).parent
    _, _, index, _, index_record = load_verified_run(index_path, {"frame_index"})
    if (index_record["sha256"] != reference.get("run_manifest_sha256")
            or sha256(index_path/"frame_index.json") != reference.get("frame_index_sha256")
            or index.get("parent_source_sha256") != report["source_sha256"]
            or index.get("parent_stream_index") != report["stream_index"]):
        raise AVError("Temporal source-index dependency binding differs")
    expected_rows = [{k:r[k] for k in ("source_frame_index","source_pts","original_pts_seconds","source_seconds","decoded_duration_seconds")}
                     for r in index["rows"] if report["interval_seconds"][0]-1e-10 <= r["source_seconds"] < report["interval_seconds"][1]-1e-10]
    if expected_rows != report.get("source_frames_in_interval"):
        raise AVError("Temporal critical coverage inventory differs from the verified source index")
    ca, cb = _critical(report.get("critical_interval_seconds"),report["interval_seconds"])
    expected_critical = [r["source_frame_index"] for r in expected_rows if ca-1e-10 <= r["source_seconds"] < cb-1e-10]
    if expected_critical != report.get("expected_critical_source_frame_indices"):
        raise AVError("Temporal critical frame inventory differs from the verified source index")
    by_index = {r["source_frame_index"]:r for r in expected_rows}
    for row in report.get("frame_rows", []):
        owned = artifacts.get(row.get("path"))
        if (not owned or owned["sha256"] != row.get("sha256") or row.get("source_sha256") != report["source_sha256"]
                or row.get("stream_index") != report["stream_index"] or row.get("source_time_base") != index["source_time_base"]
                or row.get("status") != "GENERATED_NOT_REVIEWED"):
            raise AVError("Temporal frame is not bound to the verified packet")
        expected = by_index.get(row.get("source_frame_index"))
        if not expected or any(row.get(k) != expected[k] for k in ("source_pts","source_seconds","original_pts_seconds")):
            raise AVError("Temporal frame PTS/index differs from the verified source index")
    selected = set(r["source_frame_index"] for r in report["frame_rows"])
    if (report.get("unique_frame_count") != len(selected) or report.get("request_row_count") != len(report["frame_rows"])
            or report.get("source_frame_count_in_interval") != len(expected_rows)
            or report.get("all_source_frames_extracted") != (selected == set(by_index))):
        raise AVError("Temporal frame-count or completeness assertion differs from the source inventory")
    return root, report, record


def validate_temporal_review(temporal_run, declaration):
    root, report, record = load_temporal_inspection(temporal_run)
    if isinstance(declaration, (str, Path)):
        declaration = read_json(declaration)
    if not isinstance(declaration, dict) or declaration.get("schema") != "ave.temporal-review.v1":
        raise AVError("Require an explicit temporal review declaration")
    for key, expected in (("temporal_run_manifest_sha256",record["sha256"]),
                          ("source_sha256",report["source_sha256"]), ("stream_index",report["stream_index"]),
                          ("temporal_report_sha256",sha256(root/"temporal_report.json"))):
        if declaration.get(key) != expected:
            raise AVError(f"Temporal review binding differs: {key}")
    reviewer = _text(declaration.get("reviewer"), "reviewer", 300)
    declared = declaration.get("actual_review_declared") is True
    indices = declaration.get("inspected_source_frame_indices", [])
    if (not isinstance(indices,list) or any(type(i) is not int for i in indices)
            or len(set(indices)) != len(indices) or indices != sorted(indices)):
        raise AVError("Inspected source-frame indices must be a unique ordered integer list")
    available = {row["source_frame_index"] for row in report["frame_rows"]}
    if not set(indices) <= available:
        raise AVError("Review names frames outside the verified packet")
    requested = report["critical_interval_seconds"]
    inspected_interval = _critical(declaration.get("inspected_interval_seconds"),report["interval_seconds"])
    reasons = []
    if declaration.get("inspected_interval_seconds") is None:
        reasons.append("Reviewer must explicitly declare the inspected interval")
    if isinstance(declaration.get("observation"),str) and len(declaration["observation"]) > 4000:
        raise AVError("Temporal review observation exceeds its text ceiling")
    if not declared or declaration.get("inspection_mode") not in {"source_frame_sequence","synchronized_av"}:
        reasons.append("Actual temporal sequence review was not declared")
    if declaration.get("question_answered") is not True or not isinstance(declaration.get("observation"),str) or not declaration["observation"].strip():
        reasons.append("Reviewer has not declared a sufficient observation answering this question")
    if not indices or inspected_interval[0] > requested[0]+1e-9 or inspected_interval[1] < requested[1]-1e-9:
        reasons.append("Declared inspection does not cover the critical interval")
    expected = set(report["expected_critical_source_frame_indices"])
    if len(expected & set(indices)) < 2:
        reasons.append("At least two reviewed source frames in the critical window are required for temporal evidence")
    exact = report["question_type"] in {"CONTACT_ORDER","EXACT_CONTACT","AV_SYNC"}
    if exact and (not expected or not expected <= set(indices)):
        reasons.append("Exact contact/order/sync requires review of every original source frame in the critical window")
    synchrony = None
    if report["question_type"] == "AV_SYNC":
        audio = declaration.get("audio_review", {})
        if (declaration.get("inspection_mode") != "synchronized_av" or not isinstance(audio,dict)
            or audio.get("actual_audio_inspection_declared") is not True or audio.get("source_sha256") != report["source_sha256"]
            or type(audio.get("stream_index")) is not int or not isinstance(audio.get("audio_event_source_seconds"),(int,float))
            or audio.get("visual_event_source_frame_index") not in set(indices)
            or audio.get("clock") != report["clock"]):
            reasons.append("AV synchrony needs separately source-bound actual audio inspection and an event pair")
        else:
            event = _number(audio["audio_event_source_seconds"],"audio event source time")
            tolerance = _number(audio.get("tolerance_seconds"),"declared synchrony tolerance")
            valid_audio = {s["index"] for s in read_json(root/"run.json")["sources"][0].get("streams",[]) if s.get("codec_type")=="audio"}
            audio_interval = _critical(audio.get("inspected_interval_seconds"), report["interval_seconds"])
            visual_time = next(row["source_seconds"] for row in report["frame_rows"] if row["source_frame_index"] == audio["visual_event_source_frame_index"])
            synchrony = {"audio_event_source_seconds":event,"visual_event_source_seconds":visual_time,
                         "audio_minus_visual_seconds":event-visual_time,"declared_tolerance_seconds":tolerance,
                         "within_declared_tolerance":abs(event-visual_time)<=tolerance}
            if (audio["stream_index"] not in valid_audio or audio.get("inspected_interval_seconds") is None
                    or not audio_interval[0] <= event < audio_interval[1]):
                reasons.append("Audio/visual event pair does not support the declared source-clock tolerance")
    return {"schema": "ave.temporal-review-assessment.v1", "status": "OPEN" if reasons else "ADEQUATE_DECLARED",
            "actual_review_declared": declared, "reviewer": reviewer,
            "source_sha256": report["source_sha256"], "stream_index": report["stream_index"],
            "temporal_run_manifest_sha256": record["sha256"], "temporal_report_sha256": sha256(root/"temporal_report.json"),
            "question_type": report["question_type"], "adequate_interval_seconds": requested if not reasons else None,
            "inspected_source_frame_indices": indices, "complete_critical_source_frame_coverage_declared": bool(expected) and expected <= set(indices),
            "reasons": reasons, "declaration": declaration, "synchrony_event_pair": synchrony,
            "scope": "Validated attributed declaration and artifact coverage; does not certify that a reviewer actually saw/heard them or that the interpretation is true"}


def plan_escalation(report, *, review_assessment=None, critical_interval=None,
                    remaining_frame_budget=360, remaining_seconds_budget=120.):
    """Pure planner; callers must validate any review assessment before adoption."""
    if not isinstance(report,dict) or report.get("schema") != "ave.temporal-inspection.v1":
        raise AVError("Require a temporal inspection report")
    current = _profile(report.get("profile"))
    limits = _limits(report.get("limits"))
    if type(remaining_frame_budget) is not int or remaining_frame_budget < 0:
        raise AVError("Remaining frame budget must be a nonnegative integer")
    _number(remaining_seconds_budget,"remaining seconds budget")
    bounds = _critical(critical_interval or report.get("critical_interval_seconds"),report["interval_seconds"])
    base = {"schema":"ave.temporal-escalation.v1","question":report["question"],"question_type":report["question_type"],
            "source_sha256":report["source_sha256"],"stream_index":report["stream_index"],
            "current_profile":current,"critical_interval_seconds":bounds,
            "remaining_frame_budget":remaining_frame_budget,"remaining_seconds_budget":remaining_seconds_budget}
    if review_assessment is not None:
        if not isinstance(review_assessment,dict) or review_assessment.get("schema") != "ave.temporal-review-assessment.v1":
            raise AVError("Unsupported temporal review assessment")
        if any(review_assessment.get(k) != report.get(k) for k in ("source_sha256","stream_index","question_type")):
            raise AVError("Review assessment does not match the temporal question/source")
        serialized = (json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")
        if review_assessment.get("temporal_report_sha256") != hashlib.sha256(serialized).hexdigest():
            raise AVError("Review assessment does not bind this exact temporal report")
        if (review_assessment.get("status") == "ADEQUATE_DECLARED" and review_assessment.get("actual_review_declared") is True
                and review_assessment.get("adequate_interval_seconds") == bounds):
            return dict(base,status="STOP_ADEQUATE",next_profile=None,next_interval_seconds=None,
                        reason="Separate validated review declares this critical window adequate")
    chain=list(PROFILES)
    if current == chain[-1]:
        return dict(base,status="OPEN",next_profile=None,next_interval_seconds=None,
                    reason="All source frames prepared; more extraction cannot replace missing or inconclusive review")
    if current == chain[0] and bounds == report["interval_seconds"]:
        return dict(base,status="OPEN_NO_NARROWER_WINDOW",next_profile=None,next_interval_seconds=None,
                    reason="Supply a narrower critical interval before increasing sparse inspection density")
    next_profile=chain[chain.index(current)+1]
    seconds=bounds[1]-bounds[0]
    fps=PROFILES[next_profile]
    count=(sum(bounds[0]-1e-10 <= row["source_seconds"] < bounds[1]-1e-10 for row in report["source_frames_in_interval"])
           if fps is None else math.ceil(seconds*fps-1e-10))
    if (count <= 0 or count > min(remaining_frame_budget,limits["max_frames"])
        or remaining_seconds_budget < .001 or seconds > remaining_seconds_budget
        or fps is None and seconds > limits["max_complete_window_seconds"]):
        return dict(base,status="OPEN_BUDGET",next_profile=None,next_interval_seconds=None,
                    estimated_frame_count=count,reason="Explicit frame/window/time budget prevents further escalation")
    return dict(base,status="EXTRACT_PLANNED",next_profile=next_profile,next_interval_seconds=bounds,
                estimated_frame_count=count,
                next_limits=dict(limits,max_frames=min(limits["max_frames"],remaining_frame_budget),
                                 wall_timeout_seconds=min(limits["wall_timeout_seconds"],remaining_seconds_budget)),
                reason="Insufficient reviewed adequacy; inspect the supplied critical window at the next density")
