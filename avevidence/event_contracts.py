"""Strict, small contracts shared by bounded dramatic-event operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .common import AVError
from .inventory import identifier, number, text_value

CLOCK = "original_pts_minus_source_origin"
CHANNELS = {"TXT", "VIS", "TVIS", "AM", "AO_STAGE1", "AO_STAGE2", "HL", "VO", "AVO", "INF", "INT"}
PROPOSITIONS = {"wording", "delivery", "nonverbal_vocal", "music_structure", "performance_music",
                "soundscape", "av_sync", "motion", "contact", "event_order", "visual_fact",
                "sound_level", "emotion", "speaker_identity", "speech_timing", "interpretation"}
STATES = {"REPORTED", "PROVISIONAL", "CONFIRMED", "SUPPORTED", "CONTRADICTED", "RECHECK", "OPEN"}
TEMPORAL_STATES = {"STATIC_CONFIRMED", "TEMPORAL_SAMPLED", "FRAME_SEQUENCE_CONFIRMED",
                   "FRAME_COMPLETE_CONFIRMED", "VIDEO_OBSERVER_REPORTED", "OPEN"}


def object_fields(value, allowed, required=(), name="record"):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise AVError(f"Malformed or unknown {name} fields")
    return value


def array(value, name, maximum=1000, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise AVError(f"{name} needs {minimum}..{maximum} entries")
    return value


def strings(value, name, maximum=1000, minimum=0):
    rows = array(value, name, maximum, minimum)
    for item in rows:
        text_value(item, name)
    if len(set(rows)) != len(rows):
        raise AVError(f"Duplicate {name}")
    return rows


def choice(value, allowed, name):
    if not isinstance(value, str) or value not in allowed:
        raise AVError(f"Unknown {name}")
    return value


def span(value, bounds=None, name="interval"):
    if not isinstance(value, list) or len(value) != 2:
        raise AVError(f"{name} requires [start,end]")
    a, b = [number(item, name) for item in value]
    if a >= b or bounds is not None and (a < bounds[0] - 1e-9 or b > bounds[1] + 1e-9):
        raise AVError(f"Invalid or out-of-bounds {name}")
    return [a, b]


def read_event_json(path, max_bytes=8 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise AVError("Event JSON exceeds its bounded input budget")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise AVError("Event input must be strict UTF-8 JSON") from exc


def canonical_digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise AVError("Cannot identify a nonfinite or non-JSON event record") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def node_ids(observations, claims):
    result = {}
    for kind, items in (("observation", observations), ("claim", claims)):
        for item in items:
            if not isinstance(item, dict):
                raise AVError("Graph nodes must be objects")
            key = identifier(item.get("id"), "node ID")
            if key in result:
                raise AVError("Observation and claim IDs must be globally unique")
            result[key] = {"type": kind, "record": item}
    return result
