"""Optional external Chromaprint executable; local bounded fingerprints only."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess

from ..common import AVError, sha256
from ..event_contracts import canonical_digest


def doctor(executable=None):
    path = shutil.which(executable or "fpcalc")
    if not path:
        return {"available": False, "path": None, "version": None, "license": "See externally installed Chromaprint distribution"}
    try:
        r = subprocess.run([path, "-version"], capture_output=True, timeout=10, text=True, encoding="utf-8", errors="replace")
        version = (r.stdout or r.stderr).strip()[:256] if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        version = None
    return {"available": bool(version), "path": str(Path(path).resolve()), "version": version,
            "executable_sha256": sha256(path), "license": "Chromaprint; not distributed by this package"}


def parse_fpcalc(raw, expected_duration):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024 * 1024:
        raise AVError("Chromaprint output exceeds its bounded result size")
    try:
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("Duplicate Chromaprint field")
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise AVError("Malformed Chromaprint JSON") from None
    if not isinstance(value, dict) or set(value) - {"duration", "fingerprint"} or set(value) != {"duration", "fingerprint"}:
        raise AVError("Malformed Chromaprint fields")
    duration, fp = value["duration"], value["fingerprint"]
    if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0 or
        abs(duration - expected_duration) > 1.05 or not isinstance(fp, str) or not 8 <= len(fp) <= 200000 or
        any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-=" for c in fp)):
        raise AVError("Chromaprint fingerprint or duration differs from the bounded witness")
    return {"duration_seconds": duration, "fingerprint": fp, "fingerprint_sha256": canonical_digest(fp),
            "expected_duration_seconds": expected_duration, "duration_quantization_tolerance_seconds": 1.05}


def calculate(wav, duration, executable=None):
    if not 0 < duration <= 120:
        raise AVError("Chromaprint source witness must be explicitly bounded to 120 seconds")
    state = doctor(executable)
    if not state["available"]:
        raise AVError("Optional Chromaprint fpcalc is unavailable; install it separately or use local metadata/reuse")
    command = [state["path"], "-json", "-length", str(math.ceil(duration)), str(Path(wav).resolve())]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=90)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise AVError("Chromaprint execution failed; no identity conclusion") from None
    if result.returncode:
        raise AVError("Chromaprint execution failed; no identity conclusion")
    if sha256(state["path"]) != state["executable_sha256"]:
        raise AVError("Chromaprint executable changed during fingerprint computation")
    return {**parse_fpcalc(result.stdout, duration), "implementation": state,
            "command": command, "witness_sha256": sha256(wav), "authority": "LOCAL_FINGERPRINT_ONLY"}
