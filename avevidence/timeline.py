"""Verified-source navigation joins; proximity never proves an audiovisual claim."""
from __future__ import annotations

import bisect
import math

from .common import AVError, finite, finish_run, output_transaction, select_stream, write_csv, write_json
from .visual import CLOCK, load_verified_run


def _same_number(a, b):
    return math.isclose(finite(a), finite(b), rel_tol=0, abs_tol=1e-6)


def align_timeline(subtitle_run, frame_run, output, *, audio_run=None, max_distance=2.0):
    max_distance = finite(max_distance, "maximum frame distance", 0)
    with output_transaction(output, inputs=[subtitle_run, frame_run] + ([audio_run] if audio_run else [])) as stage:
        subroot, submanifest, subtitles, subfiles, subrecord = load_verified_run(subtitle_run, {"parse_subtitles"})
        frameroot, framemanifest, frames, framefiles, framerecord = load_verified_run(frame_run, {"extract_frames"})
        source_hash = subtitles.get("parent_source_sha256")
        if not source_hash or source_hash != frames.get("parent_source_sha256"):
            raise AVError("Subtitle/frame source IDs are unbound or differ; bind external captions to the exact media first")
        if subtitles.get("clock") != CLOCK or frames.get("clock") != CLOCK:
            raise AVError("Subtitle/frame clocks are not the supported original-PTS source clock")
        if not _same_number(subtitles.get("origin_seconds"), frames.get("origin_seconds")):
            raise AVError("Subtitle/frame source origins differ")
        source = next((s for s in framemanifest["sources"] if s["sha256"] == source_hash), None)
        if source is None:
            raise AVError("Frame run lacks the declared media source record")
        select_stream(source, "video", frames.get("parent_stream_index"))
        points = sorted(frames.get("rows", []), key=lambda row: finite(row.get("source_seconds")))
        if not points:
            raise AVError("Cannot align against an empty frame run")
        for point in points:
            if point.get("source_sha256") != source_hash or point.get("stream_index") != frames["parent_stream_index"]:
                raise AVError("Frame row/source binding differs")
            if point.get("path") not in framefiles or framefiles[point["path"]]["sha256"] != point.get("sha256"):
                raise AVError("Frame row does not name a verified artifact")
        cues = subtitles.get("rows", [])
        cue_ids = [str(c["cue_index"]) for c in cues]
        if not cues or len(set(cue_ids)) != len(cue_ids):
            raise AVError("Subtitle cues are empty or have duplicate IDs")
        csv_name = submanifest["metadata"].get("csv_file")
        if csv_name not in subfiles:
            raise AVError("Subtitle dialogue CSV is not a verified artifact")
        features = {}
        records = [subrecord, framerecord]
        audio_stream = None
        if audio_run:
            audroot, audmanifest, audio, audfiles, audrecord = load_verified_run(audio_run, {"cue_features"})
            records.append(audrecord)
            if (audio.get("parent_source_sha256") != source_hash or audio.get("clock") != CLOCK
                    or not _same_number(audio.get("origin_seconds"), frames["origin_seconds"])):
                raise AVError("Audio feature source/clock differs from subtitle/frame binding")
            if audio.get("dialogue_clock_status") != "VERIFIED_SOURCE_BINDING":
                raise AVError("Unbound operator cue times cannot enter a verified timeline join")
            if audio.get("dialogue_csv_sha256") != subfiles[csv_name]["sha256"]:
                raise AVError("Audio features were computed from a different subtitle CSV")
            audio_stream = audio.get("parent_stream_index")
            select_stream(source, "audio", audio_stream)
            by_id = {str(c["cue_index"]): c for c in cues}
            for row in audio.get("rows", []):
                key = str(row.get("cue_index"))
                if key in features or key not in by_id:
                    raise AVError("Duplicate or unknown cue in feature join")
                cue = by_id[key]
                start = row.get("source_start_seconds", row.get("start_seconds"))
                end = row.get("source_end_seconds", row.get("end_seconds"))
                if not _same_number(start, cue["start_seconds"]) or not _same_number(end, cue["end_seconds"]):
                    raise AVError("Feature cue interval differs from the caption interval")
                features[key] = row
        times = [finite(p["source_seconds"]) for p in points]
        rows = []
        for cue in cues:
            a = finite(cue["start_seconds"], "cue start", 0)
            b = finite(cue["end_seconds"], "cue end", 0)
            if b <= a:
                raise AVError("Invalid subtitle cue interval")
            mid = (a + b) / 2
            pos = bisect.bisect_left(times, mid)
            candidates = [i for i in (pos-1, pos) if 0 <= i < len(points)]
            index = min(candidates, key=lambda i: (abs(times[i]-mid), times[i], i))
            nearest = points[index]
            delta = times[index] - mid
            if mid < times[0] or mid > times[-1]:
                proximity = "OUTSIDE_FRAME_POINT_RANGE"
            elif abs(delta) > max_distance:
                proximity = "DISTANT"
            else:
                proximity = "NEARBY"
            feature = features.get(str(cue["cue_index"]))
            rows.append({"cue_index": cue["cue_index"], "start_seconds": a, "end_seconds": b,
                         "name": cue.get("name", ""), "text_raw": cue.get("text_raw", ""), "text_plain": cue.get("text_plain", ""),
                         "frame_id": nearest["frame_id"], "frame_path": nearest["path"], "frame_sha256": nearest["sha256"],
                         "frame_source_seconds": times[index], "frame_delta_from_midpoint_seconds": delta,
                         "frame_distance_seconds": abs(delta), "proximity_status": proximity,
                         "frame_inside_cue": a <= times[index] < b,
                         "audio_feature_status": "NOT_PROVIDED" if feature is None else feature.get("status", "UNKNOWN"),
                         "audio_features": feature, "status": "GENERATED_NOT_REVIEWED"})
        data = {"schema": "ave.timeline.v1", "parent_source_sha256": source_hash,
                "clock": CLOCK, "origin_seconds": frames["origin_seconds"], "video_stream_index": frames["parent_stream_index"],
                "audio_stream_index": audio_stream, "subtitle_binding_method": subtitles.get("binding_method"),
                "subtitle_csv_sha256": subfiles[csv_name]["sha256"], "max_distance_seconds": max_distance,
                "input_runs": [{"path": r["path"], "sha256": r["sha256"]} for r in records],
                "rows": rows, "limitations": ["Source IDs and declared clocks are checked; external subtitle synchronization remains operator-declared.",
                                               "Nearest frames are navigation only. Proximity, still points and numerical features are not perceptual review."]}
        write_json(stage / "timeline.json", data)
        write_csv(stage / "timeline.csv", rows, ["cue_index", "start_seconds", "end_seconds", "name", "text_plain",
                                                 "frame_id", "frame_path", "frame_sha256", "frame_source_seconds",
                                                 "frame_delta_from_midpoint_seconds", "frame_distance_seconds", "proximity_status",
                                                 "frame_inside_cue", "audio_feature_status", "status"])
        return finish_run(stage, "align_timeline", records, {"max_distance_seconds": max_distance},
                          {"result_file": "timeline.json", "csv_file": "timeline.csv", "cue_count": len(rows),
                           "parent_source_sha256": source_hash, "clock": CLOCK})
