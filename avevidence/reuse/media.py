"""Native PCM witnesses and explicitly separate search representations."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from ..audio import _decode, _selection
from ..common import AVError, read_json, run, sha256, write_json
from ..performance_cache import cache_get, code_identity, digest, versions


def implementation():
    root = Path(__file__).parent
    return {p.name: sha256(p) for p in sorted(root.glob("*.py"))}


def prepare(source, stream, cache_dir, tolerance_ms=None):
    key = {"schema": "ave.reuse.native.v1", "source": source["sha256"],
           "stream": stream["index"], "tolerance_ms": tolerance_ms,
           "ffmpeg": run(["ffmpeg", "-version"]).stdout.splitlines()[0],
           "decoder": code_identity("audio.py", "common.py"),
           "implementation": sha256(__file__), "versions": versions("numpy", "scipy")}

    def build(stage):
        decoded = _decode(source, stream, stage, "native",
                          timestamp_tolerance_seconds=None if tolerance_ms is None else tolerance_ms / 1000)
        decoded["raw_path"] = "native.f64le"
        write_json(stage / "audio.json", decoded)

    path, hit, cache_key = cache_get(cache_dir, "reuse-native", key, build)
    data = read_json(path / "audio.json")
    data["raw_path"] = path / data["raw_path"]
    return data, {"cache_key": cache_key, "hit": hit}


def extract(decoded, start, end):
    selection = _selection(decoded, start, end)
    if selection["status"] != "COMPLETE" or len(selection["parts"]) != 1:
        raise AVError("Reuse windows must have complete, contiguous source coverage; split windows at clock gaps")
    part = selection["parts"][0]
    a, b = part["input_sample_start"], part["input_sample_end"]
    mm = np.memmap(decoded["raw_path"], dtype="<f8", mode="r",
                   shape=(decoded["sample_frames"], decoded["channels"]))
    try:
        x = np.array(mm[a:b])
    finally:
        del mm
    if not np.isfinite(x).all():
        raise AVError("Nonfinite audio cannot enter reuse analysis")
    return x, part


def proxy(x, rate, target_rate=16000):
    """Per-channel retrieval proxy; never the basis of an EXACT native claim."""
    if rate != target_rate:
        factor = math.gcd(int(rate), target_rate)
        x = resample_poly(x, target_rate // factor, int(rate) // factor, axis=0)
    return np.asarray(x, dtype=np.float32)


def occurrence(source, stream, decoded, row, samples, part):
    identity = {"source_sha256": source["sha256"], "audio_stream": stream["index"],
                "materialization_id": str(row.get("media_id", source["path"])),
                "sample_start": part["input_sample_start"], "sample_end": part["input_sample_end"],
                "clock": decoded["clock"], "sample_rate_hz": decoded["sample_rate_hz"]}
    quality = row.get("quality", "unknown")
    if not isinstance(quality, str) or quality not in QUALITY:
        raise AVError(f"Unknown source quality: {quality}")
    vocal = row.get("vocal_status", "unknown")
    if not isinstance(vocal, str) or vocal not in {"unknown", "candidate", "verified_voice", "verified_nonvocal"}:
        raise AVError("Invalid vocal_status")
    if vocal.startswith("verified_") and not str(row.get("vocal_basis", "")).strip():
        raise AVError("Verified vocal attribution requires a supplied vocal_basis")
    kind = row.get("unit_kind", "window")
    if not isinstance(kind, str) or kind not in {"performance", "fragment", "window", "composite"}:
        raise AVError("unit_kind must be performance, fragment, window or composite")
    if row.get("speaker") is not None and not isinstance(row["speaker"], str):
        raise AVError("speaker must be a string or null")
    exclusions = row.get("measurement_exclusions", [])
    if not isinstance(exclusions, list) or any(x not in ("pitch", "level", "timbre", "timing") for x in exclusions):
        raise AVError("Invalid measurement_exclusions")
    return {"occurrence_id": "occ-" + digest(identity)[:24], **identity,
            "source_path": source["path"], "source_start_s": part["source_start_seconds"],
            "source_end_s": part["source_end_seconds"], "channels": decoded["channels"],
            "channel_layout": decoded["channel_layout"],
            "pcm_sha256": __import__("hashlib").sha256(samples.astype("<f8").tobytes()).hexdigest(),
            "label": str(row.get("id", row.get("label", ""))), "speaker": row.get("speaker"),
            "quality": quality, "vocal_status": vocal, "vocal_basis": row.get("vocal_basis"),
            "unit_kind": kind, "role": row.get("role", "both"),
            "context": row.get("context", "unspecified"),
            "measurement_exclusions": row.get("measurement_exclusions", []),
            "codec_observed": stream.get("codec_name"),
            "timestamp_tolerance_s": decoded["timestamp_quantization_tolerance_seconds"],
            "max_timestamp_adjustment_s": decoded["max_timestamp_adjustment_seconds"],
            "attribution_authority": "supplied_metadata_not_detector",
            "performance_identity_scope": "declared_unit" if kind == "performance" else "recorded_fragment_or_window"}


QUALITY = {"isolated_voice_asset": 0, "clean_scene": 1, "clean_derived": 2,
           "mixed_scene": 3, "edited": 4, "unknown": 5}
