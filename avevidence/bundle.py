"""Compose independently verifiable evidence runs into an episode bundle."""
from __future__ import annotations

from pathlib import Path

from .common import (AVError, file_record, finish_run, output_transaction, probe_source,
                     select_stream, write_json)


def build_bundle(input, output, *, subtitles=None, subtitle_stream=None, audio_stream=None,
                 video_stream=None, interval=2.0, width=1280, shots=False, features=False,
                 audio_storage="practical"):
    from .audio import extract_audio, measure_audio, cue_features
    from .inventory import inventory
    from .subtitles import parse_subtitles
    from .timeline import align_timeline
    from .visual import build_frame_index, extract_frames, contact_sheets

    if audio_storage not in {"practical", "forensic", "verified-flac"}:
        raise AVError("audio_storage must be practical, forensic or verified-flac")

    source = probe_source(input)
    video = select_stream(source, "video", video_stream, required=False)
    audio = select_stream(source, "audio", audio_stream, required=False)
    if not video and not audio:
        raise AVError("Bundle source has no selected audio/video")
    if subtitles and subtitle_stream is not None:
        raise AVError("Choose external subtitles or an embedded subtitle stream, not both")
    dependencies = [source]
    if subtitles:
        dependencies.append(file_record(subtitles, "subtitle"))
    if features and (not audio or not (subtitles or subtitle_stream is not None)):
        raise AVError("Cue features require selected audio and explicitly bound subtitles")
    with output_transaction(output, [s["path"] for s in dependencies]) as out:
        selected = {}
        if video: selected["video"] = video["index"]
        if audio: selected["audio"] = audio["index"]
        required = (["audio"] if audio else []) + (["motion"] if video else [])
        config = {"sources": [{"logical_source_id": "source_" + source["sha256"][:16],
                               "materialization_id": "sha256_" + source["sha256"], "path": source["path"],
                               "selected_streams": selected, "required_modalities": required,
                               "preferred": True}]}
        write_json(out / "sources.json", config)
        inventory(out / "sources.json", out / "inventory")
        children = ["inventory"]
        if audio:
            extract_audio(input, out / "audio", stream_index=audio["index"], storage_profile=audio_storage)
            measure_audio(input, out / "metrics", stream_index=audio["index"])
            children += ["audio", "metrics"]
        if video:
            build_frame_index(input, out / "frame_index", stream_index=video["index"])
            children.append("frame_index")
            extract_frames(input, out / "frames", mode="interval", interval=interval, width=width,
                           stream_index=video["index"], frame_index_run=out / "frame_index")
            contact_sheets(out / "frames", out / "contacts")
            children += ["frames", "contacts"]
            if shots:
                extract_frames(input, out / "shots", mode="shots", width=width,
                               stream_index=video["index"], frame_index_run=out / "frame_index")
                children.append("shots")
        caption = None
        if subtitles:
            caption = parse_subtitles(subtitles, out / "subtitles", source_media=input)
        elif subtitle_stream is not None:
            caption = parse_subtitles(input, out / "subtitles", stream_index=subtitle_stream, media=True)
        if caption:
            children.append("subtitles")
            if features:
                csv_file = caption.get("metadata", {}).get("csv_file", "dialogue.csv")
                cue_features(out / "audio", out / "subtitles" / csv_file, out / "features")
                children.append("features")
            if video:
                align_timeline(out / "subtitles", out / "frames", out / "timeline",
                               audio_run=out / "features" if features else None)
                children.append("timeline")
        data = {"schema": "ave.bundle.v1", "source_sha256": source["sha256"], "selected_streams": selected,
                "children": children, "inventory_run": "inventory", "review_status": "NOT_REVIEWED",
                "scope": "Prepared evidence and navigation. No actual image, motion or listening review is asserted."}
        write_json(out / "bundle.json", data)
        text = "# AV evidence bundle\n\nStatus: **GENERATED — NOT REVIEWED**.\n\n"
        text += "Every child run includes source identity, commands, method parameters and artifact hashes.\n\n"
        text += "| Component | Manifest |\n|---|---|\n"
        for child in children:
            text += f"| {child} | [{child}/run.json]({child}/run.json) |\n"
        text += "\nUse actual original-PTS frame locators, and distinguish requested time from decoded time. "
        text += "Audio measurements describe a signal; they do not establish direct listening or speaker intention. "
        text += "Complete real review and presentation records separately, then validate them against the inventory.\n"
        (out / "INDEX.md").write_text(text, encoding="utf-8")
        result = finish_run(out, "bundle", dependencies, {"interval": interval, "width": width,
                            "shots": shots, "features": features, "audio_storage": audio_storage}, {"result_file": "bundle.json",
                            "child_runs": children, "inventory_run": "inventory"})
    return result
