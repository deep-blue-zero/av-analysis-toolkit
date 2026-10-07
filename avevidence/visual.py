"""Source-clock still preparation. Generated images are never review receipts."""
from __future__ import annotations

import bisect
import math
from fractions import Fraction
from pathlib import Path
import re

from .common import (AVError, file_record, finite, finish_run, interval as checked_interval,
                     output_transaction, parse_time, probe_source, read_json, run,
                     safe_member, select_stream, sha256, verify_source, write_csv, write_json)

CLOCK = "original_pts_minus_source_origin"
MAX_FRAMES = 10000
MAX_SOURCE_SECONDS = 60.0
_PTS = re.compile(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\b")
_TIME_BASE = re.compile(r"config in time_base:\s*(\d+/\d+)")


def load_verified_run(directory, operation=None):
    """Validate manifest-owned files; directory contents are never inferred evidence."""
    root = Path(directory).resolve()
    record = file_record(root / "run.json", kind="evidence_run_manifest")
    manifest = read_json(root / "run.json")
    if manifest.get("schema") != "ave.run.v1":
        raise AVError("Unsupported evidence run schema")
    if operation is not None and manifest.get("operation") not in set(operation):
        raise AVError("Unexpected evidence run operation")
    artifacts = {}
    for item in manifest.get("artifacts", []):
        name = item.get("path")
        if name in artifacts:
            raise AVError("Duplicate manifest artifact")
        path = safe_member(root, name)
        if path.is_symlink() or not path.is_file() or sha256(path) != item.get("sha256"):
            raise AVError(f"Missing or changed evidence artifact: {name}")
        artifacts[name] = item
    result_name = manifest.get("metadata", {}).get("result_file")
    if result_name not in artifacts:
        raise AVError("Run result is not a verified manifest artifact")
    result = read_json(safe_member(root, result_name))
    if sha256(root / "run.json") != record["sha256"]:
        raise AVError("Evidence manifest changed while loading")
    return root, manifest, result, artifacts, record


def _geometry(stream, width):
    if not isinstance(width, int) or not 0 <= width <= 8192:
        raise AVError("width must be an integer from 0 to 8192 (0 keeps source geometry)")
    for side in stream.get("side_data_list", []):
        if "display matrix" in side.get("side_data_type", "").lower() or side.get("rotation", 0):
            raise AVError("Display-matrix/rotation transforms are unsupported; prepare a verified SDR square-pixel source")
    if finite(stream.get("tags", {}).get("rotate", 0), "rotation") != 0:
        raise AVError("Rotated sources require an explicitly verified geometry conversion")
    sar = stream.get("sample_aspect_ratio", "1:1")
    if sar not in (None, "N/A", "0:1", "1:1"):
        raise AVError("Non-square sample aspect ratio is not supported without an explicit geometry conversion")
    transfer = stream.get("color_transfer", "unknown")
    primaries = stream.get("color_primaries", "unknown")
    space = stream.get("color_space", "unknown")
    pixel = stream.get("pix_fmt", "unknown")
    depth = int(stream.get("bits_per_raw_sample") or 0)
    if (transfer in {"smpte2084", "arib-std-b67"} or "2020" in primaries or "2020" in space
            or depth > 8 or re.search(r"(?:p|gray)(?:9|10|12|14|16)(?:le|be)?$", pixel)):
        raise AVError("HDR/wide-gamut/high-depth source requires an explicit color-managed conversion; automatic conversion refused")
    return {"source_width": stream.get("width"), "source_height": stream.get("height"),
            "sample_aspect_ratio": sar, "pixel_format": pixel,
            "color_range": stream.get("color_range", "unknown"), "color_space": space,
            "color_primaries": primaries, "color_transfer": transfer,
            "output_pixel_format": "rgb24", "output_container": "PNG",
            "conversion": "FFmpeg SDR decode and optional scale; no HDR tone mapping; untagged color remains unspecified",
            "requested_width": width, "autorotate": False}


def _frame_index(source, stream):
    result = run(["ffprobe", "-v", "error", "-select_streams", str(stream["index"]),
                  "-show_frames", "-show_entries", "frame=pts,duration,pkt_duration,width,height",
                  "-of", "json", source["path"]])
    import json
    raw = json.loads(result.stdout)
    try:
        tb = Fraction(stream["time_base"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise AVError("Source video time base is unavailable") from exc
    if tb <= 0:
        raise AVError("Invalid source video time base")
    entries = []
    for i, row in enumerate(raw.get("frames", [])):
        if "pts" not in row:
            raise AVError("A decoded frame has no original presentation PTS; inferred timestamps are refused")
        pts = int(row["pts"])
        if entries and pts <= entries[-1]["pts"]:
            raise AVError("Non-increasing/duplicate source PTS requires a specialized clock audit")
        if row.get("width") != stream.get("width") or row.get("height") != stream.get("height"):
            raise AVError("Midstream geometry changes are unsupported")
        original = float(pts * tb)
        duration = int(row.get("duration", row.get("pkt_duration", 0)) or 0) * tb
        entries.append({"index": i, "pts": pts, "original": original,
                        "time": original - source["origin_seconds"], "duration": float(duration)})
    if not entries:
        raise AVError("Source has no decoded video frames")
    return entries, tb


def build_frame_index(input, output, *, stream_index=None):
    """Create one immutable original-PTS frame index for later reuse."""
    with output_transaction(output, inputs=[input]) as stage:
        source = probe_source(input)
        stream = select_stream(source, "video", stream_index)
        geometry = _geometry(stream, 0)
        entries, tb = _frame_index(source, stream)
        rows = [{"source_frame_index": row["index"], "source_pts": row["pts"],
                 "source_time_base": str(tb), "original_pts_seconds": row["original"],
                 "source_seconds": row["time"], "decoded_duration_seconds": row["duration"]}
                for row in entries]
        data = {"schema": "ave.frame_index.v1", "parent_source_sha256": source["sha256"],
                "parent_stream_index": stream["index"], "clock": CLOCK,
                "origin_seconds": source["origin_seconds"], "source_time_base": str(tb),
                "geometry": geometry, "frame_count": len(rows), "rows": rows,
                "duration_policy": "Decoded duration is retained when ffprobe exposes it; zero means unavailable, not zero visual extent.",
                "status": "GENERATED_NOT_REVIEWED"}
        write_json(stage / "frame_index.json", data)
        write_csv(stage / "frame_index.csv", rows,
                  ["source_frame_index", "source_pts", "source_time_base",
                   "original_pts_seconds", "source_seconds", "decoded_duration_seconds"])
        return finish_run(stage, "frame_index", [source],
                          {"stream_index": stream["index"]},
                          {"result_file": "frame_index.json", "csv_file": "frame_index.csv",
                           "frame_count": len(rows), "parent_source_sha256": source["sha256"],
                           "parent_stream_index": stream["index"]})


def _cached_frame_index(frame_index_run, input, requested_stream_index=None):
    root, manifest, data, _, record = load_verified_run(frame_index_run, {"frame_index"})
    if data.get("schema") != "ave.frame_index.v1" or data.get("clock") != CLOCK:
        raise AVError("Unsupported or mismatched frame-index cache")
    sources = manifest.get("sources", [])
    if len(sources) != 1:
        raise AVError("Frame-index run must identify exactly one source")
    cached = sources[0]
    current = Path(input).resolve()
    if not current.is_file() or sha256(current) != cached.get("sha256"):
        raise AVError("Frame-index cache source hash does not match the requested input")
    stream_index = data.get("parent_stream_index")
    if requested_stream_index is not None and requested_stream_index != stream_index:
        raise AVError("Requested video stream does not match the frame-index cache")
    source = dict(cached, path=str(current), size_bytes=current.stat().st_size)
    verify_source(source)
    stream = select_stream(source, "video", stream_index)
    if data.get("geometry") != _geometry(stream, 0):
        raise AVError("Frame-index cache geometry no longer matches the selected stream")
    try:
        tb = Fraction(data["source_time_base"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise AVError("Frame-index cache has an invalid time base") from exc
    entries = []
    for expected, row in enumerate(data.get("rows", [])):
        if row.get("source_frame_index") != expected or row.get("source_time_base") != str(tb):
            raise AVError("Frame-index cache rows are not contiguous or time-base consistent")
        pts = int(row["source_pts"])
        original = finite(row["original_pts_seconds"], "cached original PTS")
        source_seconds = finite(row["source_seconds"], "cached source time")
        duration = finite(row.get("decoded_duration_seconds", 0), "cached frame duration", 0)
        if abs(original - float(pts * tb)) > max(1e-9, abs(float(tb)) * 1e-6):
            raise AVError("Frame-index cache PTS conversion is inconsistent")
        if abs(source_seconds - (original - source["origin_seconds"])) > 1e-8:
            raise AVError("Frame-index cache origin mapping is inconsistent")
        if entries and pts <= entries[-1]["pts"]:
            raise AVError("Frame-index cache contains non-increasing PTS")
        entries.append({"index": expected, "pts": pts, "original": original,
                        "time": source_seconds, "duration": duration})
    if not entries or len(entries) != data.get("frame_count"):
        raise AVError("Frame-index cache is empty or has a count mismatch")
    return source, stream, entries, tb, {
        "run_manifest_path": str(root / "run.json"),
        "run_manifest_sha256": record["sha256"],
        "frame_index_sha256": sha256(root / manifest["metadata"]["result_file"])}


def _membership(indices):
    # A balanced expression avoids evaluating thousands of equalities per frame.
    if len(indices) == 1:
        return f"eq(n,{indices[0]})"
    mid = len(indices) // 2
    return f"if(lt(n,{indices[mid]}),{_membership(indices[:mid])},{_membership(indices[mid:])})"


def extract_frames(input, output, *, mode="interval", start=None, end=None, interval=2.0,
                   fps=10.0, timestamps=None, threshold=0.35, width=1280, stream_index=None,
                   frame_index_run=None, seek_preroll=2.0, input_seek=True):
    if mode not in {"interval", "dense", "source", "exact", "shots"}:
        raise AVError("Unknown frame extraction mode")
    dependencies = [input] + ([frame_index_run] if frame_index_run else [])
    with output_transaction(output, inputs=dependencies) as stage:
        cache_reference = None
        if frame_index_run:
            source, stream, entries, tb, cache_reference = _cached_frame_index(
                frame_index_run, input, stream_index)
        else:
            source = probe_source(input)
            stream = select_stream(source, "video", stream_index)
            entries, tb = _frame_index(source, stream)
        geometry = _geometry(stream, width)
        preroll = finite(seek_preroll, "seek preroll", 0)
        if preroll > 120:
            raise AVError("seek preroll is limited to 120 seconds")
        times = [row["time"] for row in entries]
        tail = entries[-1]["duration"]
        endpoint_method = "last_original_pts_plus_decoded_frame_duration"
        if tail <= 0:
            tail = times[-1] - times[-2] if len(times) > 1 else 0
            endpoint_method = "last_original_pts_plus_previous_frame_delta_estimate"
        endpoint = times[-1] + tail
        if endpoint <= 0:
            raise AVError("Source has no positive video timeline")
        requests = []
        selections = []
        if mode == "exact":
            if start is not None or end is not None:
                raise AVError("Exact mode uses timestamps, not start/end")
            if not timestamps or len(timestamps) > MAX_FRAMES:
                raise AVError(f"Exact mode requires 1–{MAX_FRAMES} requested timestamps")
            requests = [parse_time(x) for x in timestamps]
            if any(x is None for x in requests):
                raise AVError("Empty requested timestamp")
            for target in requests:
                pos = bisect.bisect_left(times, target - 1e-10)
                if pos == len(times):
                    raise AVError(f"No source frame at or after requested time {target}")
                selections.append(pos)
            a, b = None, None
        else:
            if mode in {"source", "dense"} and (start is None or end is None):
                raise AVError("Dense/source extraction requires an explicit bounded start/end")
            a, b = checked_interval(start, end, endpoint)
            if mode == "source" and b - a > MAX_SOURCE_SECONDS:
                raise AVError(f"Source-frame extraction is limited to {MAX_SOURCE_SECONDS:g} seconds per run")
            if mode in {"interval", "dense"}:
                step = finite(interval, "interval", 0) if mode == "interval" else 1 / finite(fps, "fps", 1e-9)
                if step <= 0:
                    raise AVError("interval must be positive")
                count = int(math.ceil((b - a) / step - 1e-10))
                if count > MAX_FRAMES:
                    raise AVError(f"Requested frame count exceeds {MAX_FRAMES}")
                requests = [a + i * step for i in range(count)]
                for target in requests:
                    pos = bisect.bisect_left(times, target - 1e-10)
                    if pos == len(times) or times[pos] >= b - 1e-10:
                        raise AVError(f"No source frame at/after {target} inside the half-open requested interval")
                    selections.append(pos)
            elif mode == "source":
                selections = [i for i, t in enumerate(times) if a - 1e-10 <= t < b - 1e-10]
                requests = [None] * len(selections)
            else:
                threshold = finite(threshold, "threshold")
                if not 0 < threshold < 1:
                    raise AVError("Scene threshold must lie strictly between 0 and 1")
        if mode != "shots" and (not selections or len(selections) > MAX_FRAMES):
            raise AVError("No frames selected or source-frame count exceeds the per-run limit")
        if mode == "shots":
            select = f"gte(t,{a+source['origin_seconds']:.12f})*lt(t,{b+source['origin_seconds']:.12f})*gt(scene,{threshold:.9f})"
        else:
            selected_pts = [entries[i]["pts"] for i in sorted(set(selections))]
            select = _membership(selected_pts).replace("n,", "pts,")
        filters = [f"select='{select}'"]
        if mode == "shots":
            filters.append("metadata=print:key=lavfi.scene_score")
        filters.append("showinfo")
        if width:
            filters.append(f"scale={width}:-1")
        filters.append("format=rgb24")
        script = stage / "selection.filter"
        script.write_text(",".join(filters), encoding="utf-8")
        images = stage / "frames"

        def decode(selected_seek):
            if images.exists():
                for old in images.iterdir():
                    if not old.is_file() or old.is_symlink():
                        raise AVError("Unexpected object in frame staging directory")
                    old.unlink()
                images.rmdir()
            images.mkdir()
            command = ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-xerror", "-err_detect", "explode"]
            if selected_seek is not None:
                command += ["-ss", f"{selected_seek:.9f}"]
            command += ["-copyts", "-noautorotate", "-i", source["path"],
                        "-map", f"0:{stream['index']}", "-an", "-sn", "-dn",
                        "-filter_script:v", script, "-fps_mode", "passthrough",
                        "-frames:v", str(MAX_FRAMES + 1 if mode == "shots" else len(set(selections))),
                        "-c:v", "png", "-pix_fmt", "rgb24", images / "frame_%06d.png"]
            return run(command)

        seek_seconds = None
        if input_seek and mode != "shots" and selections:
            earliest = min(entries[i]["time"] for i in selections)
            if earliest > preroll + 0.001:
                seek_seconds = max(0.0, earliest - preroll)
        fallback_reason = None
        try:
            decoded = decode(seek_seconds)
        except AVError as exc:
            if seek_seconds is None:
                raise
            fallback_reason = f"bounded seek command failed: {exc}"
            decoded = decode(None)
        actual_pts = [int(m.group(1)) for line in decoded.stderr.splitlines()
                      if "Parsed_showinfo" in line and (m := _PTS.search(line))]
        bases = {Fraction(x) for x in _TIME_BASE.findall(decoded.stderr)}
        if bases != {tb}:
            raise AVError("Decoded filter time base does not match admitted original video time base")
        artifacts = sorted(images.glob("frame_*.png"))
        if len(artifacts) != len(actual_pts):
            raise AVError("Decoded timestamp/image count mismatch")
        pts_to_entry = {entry["pts"]: entry["index"] for entry in entries}
        if any(pts not in pts_to_entry for pts in actual_pts):
            raise AVError("Extracted PTS could not be matched to the original frame index")
        if mode == "shots":
            if len(actual_pts) > MAX_FRAMES:
                raise AVError("Shot artifact count exceeds the per-run limit")
            selections = [pts_to_entry[pts] for pts in actual_pts]
            requests = [None] * len(selections)
            scores = [float(x) for x in re.findall(r"lavfi\.scene_score=([0-9.]+)", decoded.stderr)]
            if len(scores) != len(actual_pts):
                raise AVError("Shot-score/frame correspondence failed")
        else:
            expected = [entries[i]["pts"] for i in sorted(set(selections))]
            if actual_pts != expected and seek_seconds is not None and fallback_reason is None:
                fallback_reason = "bounded seek did not recover the exact selected PTS set"
                decoded = decode(None)
                actual_pts = [int(m.group(1)) for line in decoded.stderr.splitlines()
                              if "Parsed_showinfo" in line and (m := _PTS.search(line))]
                bases = {Fraction(x) for x in _TIME_BASE.findall(decoded.stderr)}
                artifacts = sorted(images.glob("frame_*.png"))
                if bases != {tb}:
                    raise AVError("Fallback decoded filter time base does not match admitted original video time base")
                if len(artifacts) != len(actual_pts):
                    raise AVError("Fallback decoded timestamp/image count mismatch")
                if any(pts not in pts_to_entry for pts in actual_pts):
                    raise AVError("Fallback extracted PTS could not be matched to the original frame index")
            if actual_pts != expected:
                raise AVError("Decoded frames differ from selected original PTS")
            scores = None
        by_index = {pts_to_entry[pts]: path for pts, path in zip(actual_pts, artifacts)}
        rows, first_ids = [], {}
        for i, (index, requested) in enumerate(zip(selections, requests), 1):
            entry = entries[index]
            path = by_index[index]
            if not path.is_file() or not path.stat().st_size:
                raise AVError("A selected frame artifact is missing or empty")
            frame_id = f"F{i:06d}"
            duplicate = first_ids.get(index)
            first_ids.setdefault(index, frame_id)
            rows.append({"frame_id": frame_id, "path": path.relative_to(stage).as_posix(), "sha256": sha256(path),
                         "source_sha256": source["sha256"], "stream_index": stream["index"],
                         "source_frame_index": index, "source_pts": entry["pts"], "source_time_base": str(tb),
                         "original_pts_seconds": entry["original"], "source_seconds": entry["time"],
                         "requested_seconds": requested,
                         "timing_delta_seconds": None if requested is None else entry["time"] - requested,
                         "duplicate_of": duplicate, "scene_score": None if scores is None else scores[i-1],
                         "status": "GENERATED_NOT_REVIEWED"})
        result = {"schema": "ave.frames.v1", "mode": mode, "parent_source_sha256": source["sha256"],
                  "parent_stream_index": stream["index"], "clock": CLOCK, "origin_seconds": source["origin_seconds"],
                  "selection_policy": "first source frame at or after each request; source/shot modes retain original PTS",
                  "source_video_endpoint_seconds": endpoint, "video_endpoint_method": endpoint_method,
                  "geometry": geometry, "rows": rows,
                  "frame_index_cache": cache_reference,
                  "decode_strategy": {"input_seek_requested": bool(input_seek),
                                      "seek_preroll_seconds": preroll,
                                      "input_seek_seconds": seek_seconds,
                                      "used": "full_decode_fallback" if fallback_reason else
                                              "bounded_input_seek" if seek_seconds is not None else "full_decode",
                                      "fallback_reason": fallback_reason,
                                      "selection_basis": "original integer PTS"},
                  "unique_frame_count": len(artifacts), "request_row_count": len(rows),
                  "zero_shots": mode == "shots" and not rows}
        write_json(stage / "frames.json", result)
        write_csv(stage / "frames.csv", rows, ["frame_id", "path", "sha256", "source_seconds", "requested_seconds",
                                               "timing_delta_seconds", "source_frame_index", "source_pts", "source_time_base",
                                               "duplicate_of", "scene_score", "status"])
        mapping = {"kind": "still_points", "parent_source_sha256": source["sha256"],
                   "parent_stream_index": stream["index"], "frames": [
                       {"frame_id": r["frame_id"], "artifact_path": r["path"], "artifact_sha256": r["sha256"],
                        "source_seconds": r["source_seconds"]} for r in rows]}
        return finish_run(stage, "extract_frames", [source],
                          {"mode": mode, "start": a, "end": b, "interval": interval, "fps": fps,
                           "timestamps": timestamps, "threshold": threshold, "width": width,
                           "stream_index": stream["index"], "frame_index_run": str(Path(frame_index_run).resolve()) if frame_index_run else None,
                           "seek_preroll": preroll, "input_seek": bool(input_seek)},
                          {"result_file": "frames.json", "csv_file": "frames.csv", "review_mapping": mapping,
                           "frame_count": len(rows), "unique_frame_count": len(artifacts),
                           "frame_index_cache": cache_reference,
                           "decode_strategy": result["decode_strategy"]})


def contact_sheets(frame_run, output, *, columns=4, rows=4, thumb_width=360):
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise AVError("Pillow is required for contact sheets") from exc
    if any(not isinstance(x, int) or x <= 0 for x in (columns, rows, thumb_width)):
        raise AVError("Contact-sheet dimensions must be positive integers")
    if columns * rows > 100 or columns * thumb_width > 8192:
        raise AVError("Contact sheet exceeds the bounded layout limit")
    with output_transaction(output, inputs=[frame_run]) as stage:
        root, parent, data, owned, parent_record = load_verified_run(frame_run, {"extract_frames"})
        points = data.get("rows", [])
        if not points:
            raise AVError("Frame run contains no frame points")
        for point in points:
            if point.get("path") not in owned or owned[point["path"]]["sha256"] != point.get("sha256"):
                raise AVError("Frame row is not bound to a verified artifact")
        with Image.open(safe_member(root, points[0]["path"])) as im:
            thumb_height = max(1, round(thumb_width * im.height / im.width))
        if rows * (thumb_height + 40) > 8192:
            raise AVError("Contact sheet exceeds the bounded layout limit")
        sheets = []
        per_sheet = columns * rows
        for n, offset in enumerate(range(0, len(points), per_sheet), 1):
            subset = points[offset:offset+per_sheet]
            canvas = Image.new("RGB", (columns * thumb_width, rows * (thumb_height + 40)), "black")
            draw = ImageDraw.Draw(canvas)
            for j, point in enumerate(subset):
                path = safe_member(root, point["path"])
                if sha256(path) != point["sha256"]:
                    raise AVError("Frame changed before rendering")
                with Image.open(path) as im:
                    im = ImageOps.contain(im.convert("RGB"), (thumb_width, thumb_height))
                    x, y = (j % columns) * thumb_width, (j // columns) * (thumb_height + 40)
                    canvas.paste(im, (x, y))
                if sha256(path) != point["sha256"]:
                    raise AVError("Frame changed while rendering")
                draw.text((x+3, y+thumb_height+3), f"{point['frame_id']}  {point['source_seconds']:.6f}s", fill="white")
            filename = f"sheet_{n:04d}.png"
            canvas.save(stage / filename)
            sheets.append({"path": filename, "sha256": sha256(stage / filename),
                           "frame_ids": [p["frame_id"] for p in subset]})
        parent_link = {"run_manifest_path": str(root / "run.json"), "run_manifest_sha256": parent_record["sha256"]}
        data_out = {"schema": "ave.contacts.v1", "parent_frame_run": parent_link,
                    "parent_source_sha256": data["parent_source_sha256"], "parent_stream_index": data["parent_stream_index"],
                    "sheets": sheets, "status": "GENERATED_NOT_REVIEWED", "note": "Navigation images only; no temporal coverage inferred"}
        write_json(stage / "contacts.json", data_out)
        mapping = {"kind": "contact_sheet_points", "parent_source_sha256": data["parent_source_sha256"],
                   "parent_stream_index": data["parent_stream_index"], "parent_frame_run": parent_link,
                   "sheets": [{"artifact_path": s["path"], "artifact_sha256": s["sha256"], "frame_ids": s["frame_ids"]} for s in sheets]}
        return finish_run(stage, "contact_sheets", [parent_record],
                          {"columns": columns, "rows": rows, "thumb_width": thumb_width},
                          {"result_file": "contacts.json", "parent_frame_run": parent_link, "review_mapping": mapping})
