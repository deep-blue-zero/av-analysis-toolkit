"""One performance source, explicit formal sections and separately timed lyrics.

This prepares source-bound musical questions; it neither hears the music nor
automatically cuts a performance into an arbitrary series of API payloads.
"""
from pathlib import Path

from .common import AVError, file_record, finish_run, interval, output_transaction, probe_source, read_json, select_stream, write_json
from .inventory import digest_value, identifier, text_value
from .mapping import missing_intervals


def performance_sections(config, output):
    config = Path(config).resolve()
    value = read_json(config)
    allowed = {"schema", "source_path", "source_sha256", "audio_stream", "video_stream",
               "performance_interval_seconds", "sections", "lyrics", "intercuts"}
    if (not isinstance(value, dict) or set(value)-allowed
        or value.get("schema") != "ave.performance-sections.input.v1"):
        raise AVError("Performance sections require a bounded source configuration")
    text_value(value.get("source_path"), "performance source path")
    path = Path(value["source_path"])
    path = path.resolve() if path.is_absolute() else (config.parent/path).resolve()
    digest_value(value.get("source_sha256"))
    source = probe_source(path)
    if source["sha256"] != value["source_sha256"]:
        raise AVError("Performance source hash mismatch")
    audio = select_stream(source, "audio", value.get("audio_stream"))
    video = select_stream(source, "video", value.get("video_stream"), required=False)
    span = value.get("performance_interval_seconds")
    if not isinstance(span, list) or len(span) != 2:
        raise AVError("Declare the complete performance interval explicitly")
    a, b = interval(*span, source["duration_seconds"])
    sections = value.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 100:
        raise AVError("Declare 1..100 meaningful musical/formal sections")
    prepared, seen, previous_end = [], set(), a
    for section in sections:
        keys = {"id", "label", "interval_seconds", "boundary_origin", "question", "lyric_ids", "intercut_ids"}
        if not isinstance(section, dict) or set(section)-keys:
            raise AVError("Unknown formal-section fields")
        sid = identifier(section.get("id"), "section ID")
        if sid in seen:
            raise AVError("Duplicate formal-section ID")
        seen.add(sid)
        text_value(section.get("label"), "formal-section label")
        text_value(section.get("question"), "formal-section question")
        bounds = section.get("interval_seconds")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise AVError("Formal section needs explicit source bounds")
        lo, hi = interval(*bounds)
        if lo < previous_end or lo < a or hi > b:
            raise AVError("Formal sections must be ordered, nonoverlapping and within the performance")
        previous_end = hi
        if (not isinstance(section.get("boundary_origin"), str)
            or section["boundary_origin"] not in {"manual_scene_boundary", "musical_review_declared", "subtitle_boundary", "estimated_boundary"}):
            raise AVError("Formal-section boundaries need their actual authority")
        for name in ("lyric_ids", "intercut_ids"):
            ids = section.get(name, [])
            if not isinstance(ids, list) or len(ids) > 100 or not all(isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
                raise AVError("Section references must be unique bounded IDs")
            for item in ids:
                identifier(item, name)
        prepared.append({**section, "source_sha256": source["sha256"], "audio_stream_index": audio["index"],
            "task_profile": "PERFORMANCE_MUSIC", "duration_seconds": hi-lo,
            "observer_preparation_status": "COHERENT_SECTION_OPT_IN_REQUIRED" if hi-lo <= 120 else "FORMAL_REFINEMENT_REQUIRED",
            "observer_submitted": False, "interpretation": None})
    refs, dependencies = {}, [source, file_record(config)]
    for group in ("lyrics", "intercuts"):
        rows = value.get(group, [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise AVError("Performance evidence references must be bounded")
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"id", "artifact", "interval_seconds", "boundary_origin"}:
                raise AVError("Performance references need ID, hash-bound artifact, source interval and boundary authority")
            rid = identifier(row["id"], "performance evidence ID")
            if rid in refs:
                raise AVError("Duplicate performance evidence ID")
            artifact = row["artifact"]
            if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
                raise AVError("Performance evidence artifact needs path and SHA-256")
            digest_value(artifact["sha256"])
            text_value(artifact["path"], "performance evidence path")
            p = Path(artifact["path"])
            p = p.resolve() if p.is_absolute() else (config.parent/p).resolve()
            record = file_record(p, "performance_"+group)
            if record["sha256"] != artifact["sha256"]:
                raise AVError("Performance evidence artifact hash mismatch")
            bounds = row["interval_seconds"]
            if not isinstance(bounds, list) or len(bounds) != 2 or missing_intervals([interval(*bounds)], [[a,b]]):
                raise AVError("Performance evidence lies outside the source performance")
            if (not isinstance(row["boundary_origin"], str)
                or row["boundary_origin"] not in {"subtitle_boundary", "forced_aligned_candidate", "manual_verified", "forced_aligned_verified", "manual_scene_boundary", "estimated_boundary"}):
                raise AVError("Performance evidence needs explicit timing authority")
            dependencies.append(record)
            refs[rid] = {**row, "group": group, "source_sha256": source["sha256"], "artifact": {"path": str(p), "sha256": record["sha256"]}}
    for section in prepared:
        for group, name in (("lyrics", "lyric_ids"), ("intercuts", "intercut_ids")):
            for rid in section.get(name, []):
                if rid not in refs or refs[rid]["group"] != group:
                    raise AVError("Formal section references absent or incorrectly typed performance evidence")
    with output_transaction(output, [path, config]) as stage:
        report = {"schema": "ave.performance-sections.v1", "source": source,
            "performance_interval_seconds": [a,b], "audio_stream_index": audio["index"],
            "video_stream_index": video["index"] if video else None, "sections": prepared,
            "evidence_references": list(refs.values()),
            "unassigned_intervals_seconds": missing_intervals([[a,b]], [s["interval_seconds"] for s in prepared]),
            "source_media_copied": False, "paid_calls": 0,
            "scope": "One complete performance source with operator-supplied formal boundaries; lyric display timing is not automatically phonetic alignment"}
        write_json(stage/"performance-sections.json", report)
        return finish_run(stage, "performance-sections", dependencies, metadata={"result_file": "performance-sections.json"})
