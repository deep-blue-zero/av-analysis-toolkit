"""Strict text parsing and explicit subtitle-to-media source-clock binding."""
from __future__ import annotations

from fractions import Fraction
import html
import json
from pathlib import Path
import re

from .common import (AVError, file_record, finite, finish_run, output_transaction,
                     parse_time, probe_source, run, select_stream, write_csv, write_json)

CLOCK = "original_pts_minus_source_origin"
_ASS_TAG = re.compile(r"\{[^}]*\}")


def _decode(data):
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AVError("Subtitle decoding failed; use UTF-8 or BOM-marked UTF-16") from exc
    if "\x00" in text:
        raise AVError("NULs in subtitles: unsupported encoding or invalid text")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _plain_ass(value):
    return _ASS_TAG.sub("", value).replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ").strip()


def _times(start, end):
    a = parse_time(str(start).replace(",", "."))
    b = parse_time(str(end).replace(",", "."))
    if a is None or b is None or b <= a:
        raise AVError("Every subtitle cue requires finite 0 <= start < end")
    return a, b


def _ass(text, *, admit_invalid_timing=False):
    events, fields, rows = False, None, []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            events = stripped.casefold() == "[events]"
            continue
        if not events:
            continue
        if stripped.casefold().startswith("format:"):
            fields = [x.strip().casefold() for x in stripped.split(":", 1)[1].split(",")]
            if len(set(fields)) != len(fields) or not {"start", "end", "text"}.issubset(fields) or fields[-1] != "text":
                raise AVError("ASS Events Format requires unique Start, End and final Text fields")
            continue
        if not stripped.casefold().startswith("dialogue:"):
            continue
        if not fields:
            raise AVError(f"ASS dialogue before Format at line {number}")
        values = stripped.split(":", 1)[1].lstrip().split(",", len(fields)-1)
        if len(values) != len(fields):
            raise AVError(f"Malformed ASS dialogue at line {number}")
        event = dict(zip(fields, values))
        timing_error = None
        try:
            a, b = _times(event["start"], event["end"])
        except AVError as exc:
            if not admit_invalid_timing:
                raise
            a = b = None
            timing_error = str(exc)
        rows.append({"start_seconds": a, "end_seconds": b, "name": event.get("name", event.get("actor", "")),
                     "style": event.get("style", ""), "layer": event.get("layer", ""),
                     "text_raw": event["text"], "text_plain": _plain_ass(event["text"]), "source_line": number,
                     "raw_event": line, "timing_error": timing_error})
    return rows


def _srt(text):
    rows = []
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        lines = block.splitlines()
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines.pop(0)
        if len(lines) < 2:
            raise AVError("Malformed or empty SRT cue")
        match = re.fullmatch(r"\s*(\d+:\d{2}:\d{2}[,.]\d+)\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d+)(?:\s+.*)?", lines[0])
        if not match:
            raise AVError("Malformed SRT timing line")
        a, b = _times(*match.groups())
        raw = "\n".join(lines[1:])
        rows.append({"start_seconds": a, "end_seconds": b, "name": "", "style": "", "layer": "",
                     "text_raw": raw, "text_plain": html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()})
    return rows


def _packet_bytes(text):
    chunks = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^[0-9a-fA-F]+:\s(.*)$", line)
        if not match:
            raise AVError("Unexpected ffprobe packet hex format")
        chunks.append(match.group(1).split("  ", 1)[0].replace(" ", ""))
    try:
        return bytes.fromhex("".join(chunks))
    except ValueError as exc:
        raise AVError("Could not decode subtitle packet bytes") from exc


def _embedded(source, stream, stage):
    codec = stream.get("codec_name")
    if codec not in {"ass", "ssa", "subrip", "text"}:
        raise AVError(f"Embedded subtitle codec {codec!r} is not supported without a verified text/timing conversion; image OCR is not automatic")
    result = run(["ffprobe", "-v", "error", "-select_streams", str(stream["index"]),
                  "-show_packets", "-show_data", "-show_entries", "packet=pts,duration,data", "-of", "json", source["path"]])
    raw = json.loads(result.stdout)
    write_json(stage / "subtitle_packets.json", raw)
    try:
        tb = Fraction(stream["time_base"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise AVError("Subtitle time base unavailable") from exc
    rows = []
    for packet in raw.get("packets", []):
        if "pts" not in packet or int(packet.get("duration", 0)) <= 0:
            raise AVError("Subtitle packet needs original PTS and positive duration")
        pts, duration = int(packet["pts"]), int(packet["duration"])
        text = _decode(_packet_bytes(packet.get("data", "")))
        if codec in {"ass", "ssa"}:
            # Matroska ASS packets: ReadOrder,Layer,Style,Name,MarginL,R,V,Effect,Text.
            # Other serialization is refused rather than guessed.
            if "matroska" not in source["probe"].get("format", {}).get("format_name", ""):
                raise AVError("ASS packet parsing currently requires Matroska serialization")
            parts = text.split(",", 8)
            if len(parts) != 9 or not parts[0].isdigit():
                raise AVError("Unexpected ASS packet serialization")
            body, name, style, layer = parts[8], parts[3], parts[2], parts[1]
            plain = _plain_ass(body)
        else:
            body, name, style, layer = text, "", "", ""
            plain = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        rows.append({"start_seconds": float(pts * tb) - source["origin_seconds"],
                     "end_seconds": float((pts + duration) * tb) - source["origin_seconds"],
                     "name": name, "style": style, "layer": layer, "text_raw": body, "text_plain": plain,
                     "source_pts": pts, "source_time_base": str(tb), "source_duration_ticks": duration})
    return rows


def parse_subtitles(input, output, *, stream_index=None, media=False, source_media=None, offset=0.0,
                    normalize=False, exclude_cue_ids=()):
    """Parse unbound text, or explicitly bind text times with canonical_time=text_time+offset."""
    offset = finite(offset, "subtitle offset")
    if media and (source_media is not None or offset != 0):
        raise AVError("Embedded captions already use original source PTS; external binding options are incompatible")
    if not media and stream_index is not None:
        raise AVError("stream_index applies only to embedded media subtitles")
    if exclude_cue_ids and not normalize:
        raise AVError("Explicit cue exclusions require --normalize and an exclusion ledger")
    if any(type(x) is not int or x < 1 for x in exclude_cue_ids):
        raise AVError("Excluded cue IDs must be original positive event indices")
    inputs = [input] + ([source_media] if source_media else [])
    with output_transaction(output, inputs=inputs) as stage:
        if media:
            source = probe_source(input)
            stream = select_stream(source, "subtitle", stream_index)
            sources = [source]
            rows = _embedded(source, stream, stage)
            subtitle_index = stream["index"]
            binding = "embedded_original_packet_pts"
        else:
            text_record = file_record(input, kind="subtitle_text")
            text = _decode(Path(input).read_bytes())
            suffix = Path(input).suffix.lower()
            if suffix in {".ass", ".ssa"}:
                rows = _ass(text, admit_invalid_timing=normalize)
            elif suffix == ".srt":
                rows = _srt(text)
            else:
                raise AVError("External subtitles must be ASS/SSA/SRT")
            source = probe_source(source_media) if source_media else None
            sources = [text_record] + ([source] if source else [])
            subtitle_index = None
            binding = "operator_declared_external_offset" if source else "UNBOUND"
            for row in rows:
                row["subtitle_start_seconds"] = row["start_seconds"]
                row["subtitle_end_seconds"] = row["end_seconds"]
                if row["start_seconds"] is not None:
                    row["start_seconds"] += offset
                    row["end_seconds"] += offset
        if not rows or len(rows) > 100000:
            raise AVError("Subtitle parse produced no cues or exceeds the bounded cue limit")
        clock = CLOCK if source else "unbound_subtitle_clock"
        source_hash = source["sha256"] if source else None
        origin = source["origin_seconds"] if source else None
        admitted, ledger = [], []
        if set(exclude_cue_ids) - set(range(1, len(rows) + 1)):
            raise AVError("Excluded cue ID is not present in the original subtitle events")
        for i, row in enumerate(rows, 1):
            a, b = row["start_seconds"], row["end_seconds"]
            invalid_time = a is None or b is None or a < -1e-9 or b <= a
            blank = not row["text_plain"].strip()
            category = ("malformed_timing" if invalid_time else "blank_placeholder" if blank
                        else "operator_semantic_exclusion" if i in exclude_cue_ids else None)
            if normalize and category:
                ledger.append({"original_event_id": i, "category": category, "action": "EXCLUDED_FROM_NAVIGATION",
                               "original": dict(row)})
                continue
            if invalid_time or not row["text_raw"].strip():
                raise AVError("Subtitle cue is empty or outside the nonnegative source clock")
            if source and source["duration_seconds"] is not None and b > source["duration_seconds"] + 0.001:
                raise AVError("Subtitle cue exceeds admitted source duration")
            row.update(cue_index=i, duration_seconds=b-a, parent_source_sha256=source_hash, clock=clock,
                       origin_seconds=origin, boundary_origin="subtitle_boundary", status="GENERATED_NOT_REVIEWED")
            if normalize:
                from .performance_alignment import speech_text
                row["caption_kind"] = speech_text(row["text_plain"])[2]
                ledger.append({"original_event_id": i, "category": row["caption_kind"], "action": "RETAINED",
                               "original": dict(row)})
            admitted.append(row)
        raw_event_count = len(rows)
        rows = admitted
        if not rows:
            raise AVError("No navigation cues remain after normalization")
        if normalize:
            if not media:
                (stage / ("original" + Path(input).suffix.lower())).write_bytes(Path(input).read_bytes())
            write_json(stage / "normalization.json", {"schema": "ave.subtitle-normalization.v1",
                "raw_event_count": raw_event_count, "retained_count": len(rows),
                "excluded_count": raw_event_count - len(rows), "events": ledger,
                "source_text_sha256": sources[0]["sha256"],
                "policy": "Original events and indices preserved; no invented speech, translation or phonetic timing"})
        data = {"schema": "ave.subtitles.v1", "parent_source_sha256": source_hash,
                "parent_stream_index": subtitle_index, "clock": clock, "origin_seconds": origin,
                "offset_seconds": offset, "binding_method": binding, "rows": rows,
                "normalization_ledger": "normalization.json" if normalize else None,
                "limitations": ["External timing binding is an operator declaration, not automatic synchronization verification.",
                                "Subtitle wording, speaker labels and cue windows do not establish the audible performance."]}
        write_json(stage / "subtitles.json", data)
        write_csv(stage / "dialogue.csv", rows, ["cue_index", "start_seconds", "end_seconds", "duration_seconds",
                                                 "name", "style", "text_raw", "text_plain", "parent_source_sha256",
                                                 "clock", "origin_seconds", "status"])
        return finish_run(stage, "parse_subtitles", sources,
                          {"media": media, "stream_index": subtitle_index, "offset_seconds": offset,
                           "source_media": str(Path(source_media).resolve()) if source_media else None},
                          {"result_file": "subtitles.json", "csv_file": "dialogue.csv", "cue_count": len(rows),
                           "parent_source_sha256": source_hash, "clock": clock})
