"""Shared safety, source identity, clock, execution and manifest primitives."""
from __future__ import annotations

import contextlib
import contextvars
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from . import __version__


class AVError(RuntimeError):
    pass


_journal = contextvars.ContextVar("command_journal", default=None)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def finite(value, name="value", minimum=None):
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise AVError(f"{name} must be a finite number") from exc
    if not math.isfinite(x) or (minimum is not None and x < minimum):
        raise AVError(f"Invalid {name}: {value!r}")
    return x


def parse_time(value):
    if value is None:
        return None
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise AVError(f"Invalid timecode: {value!r}")
        nums = [finite(x, "time component", 0) for x in parts]
        if any(x >= 60 for x in nums[1:]):
            raise AVError(f"Invalid timecode: {value!r}")
        return sum(x * 60 ** i for i, x in enumerate(reversed(nums)))
    return finite(value, "time", 0)


def interval(start, end, duration=None):
    a = parse_time(start) if start is not None else 0.0
    b = parse_time(end) if end is not None else duration
    if b is None:
        raise AVError("An end time is required when duration is unavailable")
    b = finite(b, "end", 0)
    if b <= a:
        raise AVError("Require start < end")
    if duration is not None and b > duration + 0.001:
        raise AVError("Requested interval exceeds the source timeline")
    return a, b


def run(argv, *, check=True, timeout=180, binary=False, stdout_file=None):
    args = [str(x) for x in argv]
    if not args or not shutil.which(args[0]):
        raise AVError(f"Executable not found: {args[0] if args else '(empty command)'}")
    kwargs = {"stdin": subprocess.DEVNULL, "stderr": subprocess.PIPE, "timeout": timeout}
    if not binary:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    stream = None
    try:
        if stdout_file is not None:
            stream = Path(stdout_file).open("xb")
        p = subprocess.run(args, stdout=stream or subprocess.PIPE, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AVError(f"Command could not complete: {args[0]}: {exc}") from exc
    finally:
        if stream is not None:
            stream.close()
    log = _journal.get()
    if log is not None:
        stderr = p.stderr.decode("utf-8", "replace") if isinstance(p.stderr, bytes) else p.stderr
        stdout = p.stdout if isinstance(p.stdout, str) else None
        log.append({"argv": args, "returncode": p.returncode, "stderr": stderr,
                    "stdout": stdout, "stdout_file": str(stdout_file) if stdout_file else None})
    if check and p.returncode:
        error = p.stderr.decode("utf-8", "replace") if isinstance(p.stderr, bytes) else p.stderr
        raise AVError(f"Command failed ({p.returncode}): {args[0]}\n{error[-4000:]}")
    return p


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        return json.load(f, parse_constant=lambda s: (_ for _ in ()).throw(AVError(f"Nonfinite JSON: {s}")))


def write_csv(path, rows, fields):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def file_record(path, kind="file"):
    p = Path(path).resolve()
    if not p.is_file():
        raise AVError(f"Input file does not exist: {p}")
    digest = sha256(p)
    return {"schema": "ave.file.v1", "kind": kind, "path": str(p), "sha256": digest,
            "size_bytes": p.stat().st_size, "duration_seconds": None, "origin_seconds": 0.0,
            "streams": [], "probe": {}}


def probe_source(path):
    from fractions import Fraction
    import tempfile

    p = Path(path).resolve()
    if not p.is_file():
        raise AVError(f"Source file does not exist: {p}")
    before = p.stat()
    digest = sha256(p)
    probe_command = ["ffprobe", "-v", "error", "-show_format", "-show_streams",
                     "-show_chapters", "-of", "json", str(p)]
    raw = json.loads(run(probe_command).stdout)
    after = p.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha256(p) != digest:
        raise AVError("Source changed during probe")
    fmt = raw.get("format", {})
    reported_duration = finite(fmt["duration"], "format duration", 0) if fmt.get("duration") not in (None, "N/A") else None
    reported_start = finite(fmt["start_time"], "format start") if fmt.get("start_time") not in (None, "N/A") else None
    names = set(fmt.get("format_name", "").split(","))
    # MOV/MP4 stream duration_ts describes the declared presentation interval,
    # including container edits. Intrinsic lossless-audio sample counts also
    # have a defined zero-origin interpretation. Other container durations are
    # not guessed to mean either a span or an absolute endpoint.
    presentation_container = bool(names & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
    intrinsic_sample_clock = bool(names & {"wav", "flac", "aiff", "au"})
    temporal = [s for s in raw.get("streams", []) if s.get("codec_type") in {"audio", "video", "subtitle"}
                and not (s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic"))]
    bounds, pending, uncertainty = [], {}, []
    for stream in temporal:
        index = stream["index"]
        try:
            tb = Fraction(stream["time_base"])
            if tb <= 0:
                raise ValueError("nonpositive time base")
        except (KeyError, ValueError, ZeroDivisionError):
            bounds.append({"stream_index": index, "codec_type": stream["codec_type"], "basis": "UNAVAILABLE",
                           "start_seconds": None, "end_seconds": None, "issues": ["missing valid stream time base"]})
            continue
        start_ticks = stream.get("start_pts")
        duration_ticks = stream.get("duration_ts")
        use_declared = presentation_container or (intrinsic_sample_clock and stream["codec_type"] == "audio")
        if use_declared and duration_ticks not in (None, "N/A") and (start_ticks not in (None, "N/A") or intrinsic_sample_clock):
            start_value = int(start_ticks) if start_ticks not in (None, "N/A") else 0
            duration_value = int(duration_ticks)
            if duration_value >= 0:
                bounds.append({"stream_index": index, "codec_type": stream["codec_type"],
                               "basis": "container_stream_presentation_interval" if presentation_container else "intrinsic_audio_sample_count",
                               "start_pts": start_value, "duration_ticks": duration_value, "time_base": str(tb),
                               "start_seconds": float(start_value * tb), "end_seconds": float((start_value + duration_value) * tb),
                               "issues": []})
                continue
        pending[index] = {"stream_index": index, "codec_type": stream["codec_type"], "basis": "explicit_packet_pts_plus_duration",
                          "time_base": str(tb), "start_seconds": None, "end_seconds": None,
                          "packet_count": 0, "issues": []}
    packet_scan = None
    if pending:
        packet_command = ["ffprobe", "-v", "error", "-show_packets", "-show_entries",
                          "packet=stream_index,pts,duration:packet_side_data=", "-of", "compact=p=0:nk=0", str(p)]
        # Bound memory and command-journal size: aggregate the packet timeline
        # from a temporary file rather than capturing the whole packet table.
        with tempfile.TemporaryDirectory(prefix="ave-clock-") as temporary:
            temporary_path = Path(temporary).resolve()
            if temporary_path.parent != Path(tempfile.gettempdir()).resolve():
                raise AVError("Unexpected packet-probe temporary directory")
            packet_file = temporary_path / "packets.txt"
            run(packet_command, stdout_file=packet_file)
            with packet_file.open(encoding="utf-8", errors="strict") as packet_rows:
                for line in packet_rows:
                    fields = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
                    if "stream_index" not in fields:
                        continue
                    index = int(fields["stream_index"])
                    if index not in pending:
                        continue
                    item = pending[index]
                    item["packet_count"] += 1
                    if fields.get("pts") in (None, "N/A") or fields.get("duration") in (None, "N/A"):
                        if "packet lacks PTS or duration" not in item["issues"]:
                            item["issues"].append("packet lacks PTS or duration")
                        continue
                    pts, ticks = int(fields["pts"]), int(fields["duration"])
                    if ticks <= 0:
                        if "packet lacks positive duration" not in item["issues"]:
                            item["issues"].append("packet lacks positive duration")
                        continue
                    tb = Fraction(item["time_base"])
                    begin, stop = float(pts * tb), float((pts + ticks) * tb)
                    item["start_seconds"] = begin if item["start_seconds"] is None else min(item["start_seconds"], begin)
                    item["end_seconds"] = stop if item["end_seconds"] is None else max(item["end_seconds"], stop)
            packet_scan = {"command": packet_command, "table_sha256": sha256(packet_file),
                           "table_size_bytes": packet_file.stat().st_size,
                           "method": "streamed original packet PTS plus positive packet duration, aggregated per temporal stream"}
        for item in pending.values():
            if item["packet_count"] == 0:
                item["basis"] = "empty_packet_stream"
            if item["issues"]:
                item["observed_partial_end_seconds"] = item["end_seconds"]
                item["end_seconds"] = None
            bounds.append(item)
        uncertainty.append("Packet presentation bounds do not establish decoder trim, codec padding, continuous coverage or AV synchronization.")
    if reported_start is not None:
        origin, origin_basis = reported_start, "format.start_time"
    else:
        known_starts = [b["start_seconds"] for b in bounds if b["start_seconds"] is not None]
        if known_starts:
            origin, origin_basis = min(known_starts), "minimum_known_stream_presentation_start"
        else:
            origin, origin_basis = 0.0, "explicit_fallback_zero_no_known_presentation_start"
            uncertainty.append("No presentation start was available; zero origin is an explicit convention.")
    active = [b for b in bounds if b["basis"] != "empty_packet_stream"]
    if active and all(b["end_seconds"] is not None for b in active):
        duration = max(0.0, max(b["end_seconds"] for b in active) - origin)
        duration_basis = "maximum_stream_presentation_end_minus_source_origin"
    elif bounds and not active:
        duration, duration_basis = 0.0, "no_temporal_packets"
    else:
        duration, duration_basis = None, "UNAVAILABLE_INCOMPLETE_STREAM_BOUNDS"
        uncertainty.append("Canonical duration is unavailable because at least one temporal stream lacks a complete endpoint; supply a bounded interval.")
    if any(b["basis"] == "container_stream_presentation_interval" for b in bounds):
        uncertainty.append("Declared container stream intervals are used as presentation bounds; decoded sample padding may differ.")
    for bound in bounds:
        bound["canonical_start_seconds"] = None if bound["start_seconds"] is None else bound["start_seconds"] - origin
        bound["canonical_end_seconds"] = None if bound["end_seconds"] is None else bound["end_seconds"] - origin
    final_stat = p.stat()
    if (before.st_size, before.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns) or sha256(p) != digest:
        raise AVError("Source changed during timeline probing")
    return {"schema": "ave.source.v1", "path": str(p), "sha256": digest,
            "size_bytes": after.st_size, "duration_seconds": duration,
            "format_duration_seconds": reported_duration, "format_start_time_seconds": reported_start,
            "duration_basis": duration_basis, "duration_uncertainty": uncertainty,
            "stream_timeline_bounds": sorted(bounds, key=lambda b: b["stream_index"]),
            "packet_bounds_scan": packet_scan, "origin_basis": origin_basis,
            "origin_seconds": origin, "streams": raw.get("streams", []), "probe": raw,
            "probe_command": probe_command}


def select_stream(source, kind, index=None, required=True):
    streams = [s for s in source.get("streams", []) if s.get("codec_type") == kind
               and not (kind == "video" and s.get("disposition", {}).get("attached_pic"))]
    if index is not None:
        if type(index) is not int or index < 0:
            raise AVError("Stream index must be a nonnegative integer")
        streams = [s for s in streams if s.get("index") == index]
    if not streams:
        if required or index is not None:
            raise AVError(f"No matching {kind} stream (indices are absolute ffprobe indices)")
        return None
    if len(streams) != 1:
        raise AVError(f"Multiple {kind} streams; choose an explicit absolute stream index")
    return streams[0]


def verify_source(source):
    if not Path(source["path"]).is_file() or sha256(source["path"]) != source["sha256"]:
        raise AVError("Source changed after admission")


def safe_member(root, relative):
    base = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or ":" in str(relative) or "\\" in str(relative):
        raise AVError(f"Unsafe artifact path: {relative!r}")
    p = (base / rel).resolve()
    if not p.is_relative_to(base):
        raise AVError("Artifact path escapes its bundle")
    return p


@contextlib.contextmanager
def output_transaction(output, inputs=()):
    target = Path(output).resolve()
    for item in inputs:
        src = Path(item).resolve()
        if target == src or src.is_relative_to(target) or (src.is_dir() and target.is_relative_to(src)):
            raise AVError("Output must not replace, contain or be created inside an input")
    if target.exists():
        raise AVError(f"Output already exists; use a new run directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ("." + target.name + ".staging-" + uuid.uuid4().hex)
    stage.mkdir()
    token = _journal.set([])
    try:
        yield stage
        if target.exists():
            raise AVError("Output appeared during processing; refusing replacement")
        stage.rename(target)
    except BaseException as exc:
        if stage.exists():
            try:
                write_json(stage / "FAILED.json", {"status": "FAILED_NOT_PUBLISHED", "error": str(exc),
                                                    "created_at": utc_now()})
            except (OSError, ValueError):
                pass
        raise
    finally:
        _journal.reset(token)


def environment():
    packages = {}
    for name in ("Pillow", "numpy", "soundfile", "librosa"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    programs = {}
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        programs[name] = {"path": executable, "version": None}
        if executable:
            programs[name]["version"] = run([name, "-version"], timeout=20).stdout.splitlines()[0]
    code_root = Path(__file__).parent
    code_hashes = {p.relative_to(code_root).as_posix(): sha256(p) for p in sorted(code_root.rglob("*.py"))}
    return {"tool_version": __version__, "python": sys.version, "platform": platform.platform(),
            "programs": programs, "packages": packages,
            "implementation_sha256": code_hashes,
            "perceptual_capabilities": {"audio": "NOT_VERIFIED", "motion": "NOT_VERIFIED", "images": "NOT_VERIFIED"}}


def finish_run(stage, operation, sources, parameters=None, metadata=None):
    out = Path(stage)
    for source in sources:
        verify_source(source)
    env = environment()
    write_json(out / "commands.json", _journal.get() or [])
    artifacts = []
    for p in sorted(out.rglob("*")):
        if p.is_symlink():
            raise AVError("Symlink artifacts are not allowed")
        if p.is_file():
            artifacts.append({"path": p.relative_to(out).as_posix(), "sha256": sha256(p), "size_bytes": p.stat().st_size})
    manifest = {"schema": "ave.run.v1", "operation": operation, "created_at": utc_now(),
                "status": "GENERATED_NOT_REVIEWED", "sources": sources,
                "parameters": parameters or {}, "metadata": metadata or {},
                "environment": env, "artifacts": artifacts}
    write_json(out / "run.json", manifest)
    return manifest
