"""Bounded AV review clips with explicit source/derivative clock mappings."""
from __future__ import annotations

import json
from .mapping import timing_basis
from collections import Counter
from pathlib import Path

from .common import (AVError, finite, finish_run, interval, output_transaction,
                     probe_source, run, select_stream, sha256, write_json)


def _rate(value):
    try:
        a, b = str(value).split("/")
        return float(a) / float(b)
    except (ValueError, ZeroDivisionError):
        return None


def _windows(source, stream):
    data = json.loads(run(["ffprobe", "-v", "error", "-select_streams", str(stream["index"]),
                           "-show_frames", "-show_entries",
                           "frame=best_effort_timestamp_time,pts_time,duration_time,pkt_duration_time,nb_samples",
                           "-of", "json", source["path"]]).stdout)
    rows = []
    tolerance = max(0.000002, (_rate(stream.get("time_base")) or 0) * 1.01)
    for frame in data.get("frames", []):
        raw = frame.get("best_effort_timestamp_time", frame.get("pts_time"))
        if raw is None:
            raise AVError("Decoded clip frame has no presentation timestamp")
        start = finite(raw, "decoded PTS")
        if stream["codec_type"] == "audio":
            samples = int(frame.get("nb_samples", 0))
            duration = samples / int(stream["sample_rate"]) if samples > 0 else 0.0
            method = "decoded_audio_sample_count"
        else:
            value = frame.get("duration_time", frame.get("pkt_duration_time"))
            duration = finite(value, "frame duration", 0) if value is not None else 0.0
            method = "explicit_decoded_frame_duration" if duration > 0 else None
        rows.append({"start": start, "duration": duration, "method": method})
    if not rows:
        raise AVError("Requested interval has no decoded frames for a selected modality")
    if any(rows[i]["start"] <= rows[i-1]["start"] for i in range(1, len(rows))):
        raise AVError("Non-increasing decoded clip timestamps need explicit normalization")

    endpoint = None
    for bound in source.get("stream_timeline_bounds", []):
        if bound.get("stream_index") == stream.get("index") and bound.get("end_seconds") is not None:
            endpoint = finite(bound["end_seconds"], "selected derivative stream endpoint")
            break
    methods = Counter()
    estimated_final = False
    if stream["codec_type"] == "video":
        for i, row in enumerate(rows):
            next_start = rows[i+1]["start"] if i + 1 < len(rows) else None
            if row["duration"] > 0:
                if next_start is not None and row["start"] + row["duration"] > next_start + tolerance:
                    raise AVError("Decoded clip frame extents overlap beyond source granularity")
            elif next_start is not None:
                row["duration"] = next_start - row["start"]
                row["method"] = "next_decoded_pts_delta"
            elif endpoint is not None and endpoint > row["start"]:
                row["duration"] = endpoint - row["start"]
                row["method"] = "verified_stream_endpoint"
            elif len(rows) > 1:
                row["duration"] = row["start"] - rows[-2]["start"]
                row["method"] = "previous_pts_delta_estimate"
                estimated_final = True
            if row["duration"] <= 0:
                raise AVError("Decoded clip frame has no positive extent")
            methods[row["method"]] += 1
    else:
        for row in rows:
            if row["duration"] <= 0:
                raise AVError("Decoded audio frame has no positive sample extent")
            methods[row["method"]] += 1

    windows = []
    max_adjustment = 0.0
    for row in rows:
        start, end = row["start"], row["start"] + row["duration"]
        if windows and abs(windows[-1][1] - start) <= tolerance:
            max_adjustment = max(max_adjustment, abs(windows[-1][1] - start))
            windows[-1][1] = end
        else:
            if windows and start < windows[-1][1] - tolerance:
                raise AVError("Overlapping clip timestamps need explicit normalization")
            windows.append([start, end])
    extent = {"method_counts": dict(sorted(methods.items())),
              "final_frame_extent_estimated": estimated_final,
              "estimated_intervals_absolute_seconds": [[r["start"], r["start"]+r["duration"]] for r in rows if r["method"] == "previous_pts_delta_estimate"],
              "extent_confidence": "ESTIMATED_TAIL" if estimated_final else "SOURCE_DERIVED",
              "selected_stream_endpoint_seconds": endpoint,
              "policy": "explicit duration, then next decoded PTS, then verified stream endpoint, then previous PTS delta estimate for only the final frame"}
    return windows, tolerance, max_adjustment, extent


def clip_av(input, output, *, start, end, pad=0.0, video_stream=None, audio_stream=None):
    source = probe_source(input)
    video = select_stream(source, "video", video_stream)
    audio = select_stream(source, "audio", audio_stream, required=False)
    from .visual import _geometry
    _geometry(video, 0)
    rotation = video.get("tags", {}).get("rotate", 0)
    if finite(rotation, "rotation") % 360:
        raise AVError("Rotated sources require explicit normalization before review clipping")
    if any(finite(x.get("rotation", 0), "rotation") % 360 for x in video.get("side_data_list", [])):
        raise AVError("Rotated sources require explicit normalization before review clipping")
    a, b = interval(start, end, source["duration_seconds"])
    padding = finite(pad, "padding", 0)
    if b - a + 2 * padding > 120:
        raise AVError("Review clips are limited to 120 seconds including requested padding")
    begin = max(0.0, a - padding)
    finish = min(source["duration_seconds"], b + padding) if source["duration_seconds"] is not None else b + padding
    absolute_begin = begin + source["origin_seconds"]
    absolute_end = finish + source["origin_seconds"]
    with output_transaction(output, inputs=[source["path"]]) as out:
        destination = out / "clip.mkv"
        filters = [f"[0:{video['index']}]trim=start={absolute_begin:.9f}:end={absolute_end:.9f},settb=expr=1/1000000,"
                   f"setpts=PTS-({absolute_begin:.9f})/TB[v]"]
        if audio:
            filters.append(f"[0:{audio['index']}]atrim=start={absolute_begin:.9f}:end={absolute_end:.9f},"
                           f"asetpts=PTS-({absolute_begin:.9f})/TB[a]")
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts", "-noautorotate", "-i", source["path"],
               "-filter_complex", ";".join(filters), "-map", "[v]", "-c:v", "ffv1", "-level", "3",
               "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000"]
        if audio:
            cmd += ["-map", "[a]", "-c:a", "pcm_f64le"]
        cmd += [str(destination)]
        run(cmd, timeout=300)
        derivative = probe_source(destination)
        mappings = []
        for kind, parent in (("video", video), ("audio", audio)):
            if parent is None:
                continue
            selected = select_stream(derivative, kind)
            windows, tolerance, adjustment, extent = _windows(derivative, selected)
            segments = [{"parent_start_seconds": x + begin,
                         "parent_end_seconds": y + begin,
                         "derivative_start_seconds": x - derivative["origin_seconds"],
                         "derivative_end_seconds": y - derivative["origin_seconds"]} for x, y in windows]
            mappings.append({"parent_source_sha256": source["sha256"], "parent_stream_index": parent["index"],
                             "derivative_stream_index": selected["index"],
                             "modality": "motion" if kind == "video" else "audio",
                             "artifact_path": "clip.mkv", "artifact_sha256": sha256(destination),
                             "segments": segments, "timestamp_tolerance_seconds": tolerance,
                             "maximum_timestamp_adjustment_seconds": adjustment,
                             "extent_derivation": extent,
                             "timing_basis": timing_basis(parent, selected),
                             "estimated_parent_intervals_seconds": [[x+begin, y+begin] for x,y in extent["estimated_intervals_absolute_seconds"]]})
        data = {"schema": "ave.clip.v1", "artifact_path": "clip.mkv", "artifact_sha256": sha256(destination),
                "requested_interval_seconds": [a, b], "padded_interval_seconds": [begin, finish],
                "derivative_origin_seconds": derivative["origin_seconds"],
                "review_mappings": mappings, "review_status": "NOT_REVIEWED",
                "codecs": {"video": "ffv1", "audio": "pcm_f64le" if audio else None},
                "scope": "Review derivative. Selected source video frames and decoded audio are re-encoded; not an encoded-packet copy.",
                "boundary_policy": "Video frame extents can cross a requested endpoint. Extents use explicit decoded duration when available, otherwise adjacent PTS/verified stream endpoint, with a final-frame previous-delta estimate explicitly marked as such."}
        write_json(out / "clip.json", data)
        result = finish_run(out, "clip-av", [source], {"start": a, "end": b, "pad": padding},
                            {"result_file": "clip.json", "review_mappings": mappings})
    return result
