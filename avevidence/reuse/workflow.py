"""Additive reuse scan/compare/graph/audit workflows over immutable evidence runs."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys
import time

import numpy as np
from scipy.io.wavfile import write as write_wav

from ..common import (AVError, file_record, finite, finish_run, output_transaction, probe_source,
                      read_json, select_stream, sha256, write_json)
from ..inventory import verify_run
from ..performance_cache import cache_get, digest, versions
from . import SCHEMA
from .correlate import compare
from .fingerprint import Index, descriptors
from .graph import connect, ingest, ledger, snapshot, default_decision, correspondence_key
from .media import extract, implementation, occurrence, prepare, proxy
from .periodicity import periodicity
from .policy import audit
from .report import render


def config_rows(path):
    path = Path(path).resolve()
    if path.is_file():
        value = read_json(path)
        if not isinstance(value, dict) or value.get("schema") != "ave.reuse.config.v1":
            raise AVError("Require ave.reuse.config.v1 configuration")
        rows = value.get("occurrences", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("source"), str) for row in rows):
            raise AVError("occurrences must be objects with a source file path")
        for row in rows:
            row["source"] = str((path.parent / row["source"]).resolve())
        return rows, [file_record(path, "reuse_config")], value
    if not path.is_dir():
        raise AVError("Expected a reuse configuration or performance-run directory")
    rows, deps = [], []
    for report in sorted(path.rglob("performance.json")):
        if not (report.parent / "run.json").is_file(): continue
        verify_run(report.parent)
        data = read_json(report)
        deps.append(file_record(report.parent / "run.json", "performance_run"))
        for cue in data["cues"]:
            rows.append({"id": str(report.parent.name)+":"+cue["cue_id"], "source": data["source"]["path"],
                         "source_sha256": data["source"]["sha256"], "audio_stream": data["audio_stream_index"],
                         "start_s": cue["start_s"], "end_s": cue["end_s"], "speaker": cue.get("speaker"),
                         "vocal_status": "candidate", "vocal_basis": "supplied caption, not verified listening",
                         "unit_kind": "performance", "context": "performance_run_cue"})
    return rows, deps, {"schema": "ave.reuse.config.v1", "import": "verified_performance_runs"}


class Media:
    def __init__(self, cache, tolerance_ms=None):
        self.cache, self.tolerance_ms = cache, tolerance_ms
        self.sources, self.audio, self.cache_hits = {}, {}, []

    def source(self, path, stream_index=None, expected=None):
        path = str(Path(path).resolve())
        if path not in self.sources:
            self.sources[path] = probe_source(path)
        source = self.sources[path]
        if expected is not None and source["sha256"] != expected:
            raise AVError("Source hash differs from configured/indexed occurrence")
        stream = select_stream(source, "audio", stream_index)
        key = (source["sha256"], stream["index"])
        if key not in self.audio:
            decoded, hit = prepare(source, stream, self.cache, self.tolerance_ms)
            self.audio[key] = decoded
            self.cache_hits.append(hit)
        return source, stream, self.audio[key]

    def data(self, o):
        _, _, decoded = self.source(o["source_path"], o["audio_stream"], o["source_sha256"])
        data, part = extract(decoded, o["source_start_s"], o["source_end_s"])
        if part["input_sample_start"] != o["sample_start"] or part["input_sample_end"] != o["sample_end"]:
            raise AVError("Cached occurrence sample coordinates changed")
        return data


def span(o, start, end, channel):
    rate = o["sample_rate_hz"]
    first = max(0, round(start*rate))
    last = min(o["sample_end"]-o["sample_start"], round(end*rate))
    if last <= first:
        raise AVError("Match has no positive native-sample coverage")
    return {"occurrence_id": o["occurrence_id"], "start_s": first/rate, "end_s": last/rate,
            "source_start_s": o["source_start_s"]+first/rate, "source_end_s": o["source_start_s"]+last/rate,
            "input_sample_start": o["sample_start"]+first, "input_sample_end": o["sample_start"]+last,
            "source_sha256": o["source_sha256"], "audio_stream": o["audio_stream"],
            "sample_rate_hz": rate, "matching_channel": channel}


def match_record(a, b, row):
    out = dict(row)
    out["a"] = span(a, out.pop("query_start_s"), out.pop("query_end_s"), out.pop("channel_a"))
    out["b"] = span(b, out.pop("target_start_s"), out.pop("target_end_s"), out.pop("channel_b"))
    out["mapping_schema"] = "ave.reuse.correspondence.v1"
    out["historical_direction"] = "unknown"
    out["codec_observations"] = {"a": a["codec_observed"], "b": b["codec_observed"], "reencoding_history": "unknown"}
    transformations = []
    for side, o in (("a", a), ("b", b)):
        s = out[side]
        if s["start_s"] > .002 or s["end_s"] < o["source_end_s"]-o["source_start_s"]-.002:
            transformations.append("partial_reuse")
    gain = out["metrics"].get("fitted_gain_b_over_a")
    if gain is not None and abs(abs(gain)-1) > .015: transformations.append("gain_changed")
    if gain is not None and gain < 0: transformations.append("polarity_inverted")
    if abs(out["duration_ratio_b_over_a"]-1) > .005: transformations.append("time_stretched")
    if abs(out["pitch_shift_semitones_b_minus_a"]) > .05: transformations.append("pitch_shifted")
    if {"time_stretched", "pitch_shifted"}.issubset(transformations):
        if abs(out["pitch_shift_semitones_b_minus_a"]+12*np.log2(out["duration_ratio_b_over_a"])) < .08:
            transformations.append("speed_changed")
    out["transformations"] = sorted(set(transformations))
    out["calibrated_probability"] = None
    out["automatic_identity_decision"] = default_decision(out, a, b)
    out["automatic_identity_policy_sha256"] = sha256(Path(__file__).parent / "graph.py")
    out["correspondence_id"] = correspondence_key(out)
    out["match_id"] = "match-"+digest(out)[:24]
    return out


def read_baseline(path):
    if path is None: return None
    path = Path(path)
    if path.suffix.lower() in {".csv", ".tsv"}:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise AVError("Parquet baseline input requires optional pyarrow; JSON/CSV/TSV need no additional package") from exc
        return pq.read_table(path).to_pylist()
    data = read_json(path)
    return data if isinstance(data, list) else data["occurrences"]


def run_scan(config, output, *, cache_dir, database, transforms=False, self_scan=False,
             compare_ids=None, max_candidates=20, max_window_s=300., min_fragment_s=.24,
             timestamp_tolerance_ms=None, preview_count=6):
    started = time.perf_counter()
    config, output, cache_dir, database = map(lambda x: Path(x).resolve(), (config, output, cache_dir, database))
    for storage in (cache_dir, database):
        if storage == output or storage.is_relative_to(output):
            raise AVError("Mutable caches and ledger must be outside immutable output runs")
    if not 1 <= max_candidates <= 1000 or not 0 <= preview_count <= 50:
        raise AVError("Invalid candidate or preview limit")
    max_window_s = finite(max_window_s, "max window", .12)
    min_fragment_s = finite(min_fragment_s, "minimum fragment", .12)
    if timestamp_tolerance_ms is not None:
        timestamp_tolerance_ms = finite(timestamp_tolerance_ms, "timestamp tolerance", 0)
        if timestamp_tolerance_ms > 10: raise AVError("Timestamp tolerance is limited to 10 ms")
    rows, dependencies, configuration = config_rows(config)
    if not rows or len(rows) > 100000:
        raise AVError("Require 1–100000 configured occurrences")
    if database.exists():
        old, _, _, _ = ledger(database)
    else:
        old = {}
    media = Media(cache_dir, timestamp_tolerance_ms)
    all_occ, current, labels, periods, fingerprint_hits = dict(old), [], {}, {}, 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    index = Index(cache_dir / "reuse-fingerprints.sqlite")
    descriptor_map = {}
    impl = implementation()
    try:
        for number, row in enumerate(rows):
            source, stream, decoded = media.source(row["source"], row.get("audio_stream"), row.get("source_sha256"))
            start = finite(row.get("start_s", max(0., decoded["segments"][0]["source_start_seconds"])), "start", 0)
            end = finite(row.get("end_s", decoded["segments"][-1]["source_end_seconds"]), "end", 0)
            if end <= start or end-start > max_window_s:
                raise AVError("Require a positive bounded window; segment long recordings or explicitly increase --max-window-s")
            if row.get("role", "both") not in {"both", "reference", "target"}:
                raise AVError("Occurrence role must be both, reference or target")
            x, part = extract(decoded, start, end)
            o = occurrence(source, stream, decoded, row, x, part)
            identity = o["occurrence_id"]
            if identity in current:
                raise AVError("Duplicate source/sample window in configuration; use one occurrence with its aliases outside this ledger")
            if o["label"]:
                if o["label"] in labels: raise AVError("Duplicate occurrence label")
                labels[o["label"]] = identity
            current.append(identity)
            all_occ[identity] = o
            px = proxy(x, o["sample_rate_hz"])
            key = {"pcm": o["pcm_sha256"], "rate": o["sample_rate_hz"], "implementation": impl["fingerprint.py"],
                   "proxy_implementation": impl["media.py"], "versions": versions("numpy", "scipy")}
            def build_desc(stage):
                ds = descriptors(px)
                np.savez(stage / "features.npz", times=np.array([d[0] for d in ds]), channels=np.array([d[1] for d in ds]),
                         vectors=np.array([d[2] for d in ds], dtype=np.float32).reshape(-1, 24))
            folder, hit, key_hash = cache_get(cache_dir, "reuse-features", key, build_desc)
            with np.load(folder / "features.npz", allow_pickle=False) as values:
                desc = list(zip(values["times"].tolist(), values["channels"].tolist(), values["vectors"]))
            fingerprint_hits += int(hit)
            descriptor_map[identity] = desc
            index.add(identity, desc, key_hash)
            periods[identity] = periodicity(px)
            if number % 25 == 0:
                print(f"Reuse: prepared {number+1}/{len(rows)} source windows", file=sys.stderr, flush=True)
            del x, px
        # A ledger may outlive its search cache. Rebuild missing/stale candidate
        # fingerprints from their bound sources instead of silently omitting it.
        for k, o in old.items():
            if k in current: continue
            key = {"pcm": o["pcm_sha256"], "rate": o["sample_rate_hz"], "implementation": impl["fingerprint.py"],
                   "proxy_implementation": impl["media.py"], "versions": versions("numpy", "scipy")}
            existing = index.db.execute("SELECT key FROM indexed WHERE id=?", (k,)).fetchone()
            if existing is None or existing[0] != digest(key):
                x = proxy(media.data(o), o["sample_rate_hz"])
                index.add(k, descriptors(x), digest(key))
                del x
        pairs, truncated = set(), []
        if compare_ids is not None:
            selected = [labels.get(k, k) for k in compare_ids]
            if any(k not in all_occ for k in selected):
                raise AVError("Compare requires two known occurrence IDs or configuration labels")
            pairs.add(tuple(selected))
        else:
            for k in current:
                role = all_occ[k]["role"]
                allowed = {oid for oid, o in all_occ.items() if
                           (role == "both" or role != o.get("role"))}
                candidates, capped = index.candidates(descriptor_map[k], allowed-{k}, max_candidates)
                if capped: truncated.append(k)
                for target in candidates:
                    if role == "reference": pairs.add((k, target))
                    elif all_occ[target]["role"] == "reference": pairs.add((target, k))
                    else: pairs.add(tuple(sorted([k, target])))
                if self_scan and len(descriptor_map[k]) > 2: pairs.add((k, k))
        matches, comparisons, pair_cache_hits = [], [], 0
        for number, (ka, kb) in enumerate(sorted(pairs)):
            a, b = all_occ[ka], all_occ[kb]
            key = {"a": {k:a[k] for k in ("pcm_sha256", "sample_rate_hz", "channels")},
                   "b": {k:b[k] for k in ("pcm_sha256", "sample_rate_hz", "channels")},
                   "self": ka == kb, "transforms": transforms, "min_fragment_s": min_fragment_s,
                   "implementation": {k: impl[k] for k in ("correlate.py", "spectrogram.py", "media.py")},
                   "versions": versions("numpy", "scipy")}
            def build_match(stage):
                x, y = media.data(a), media.data(b)
                found, stats = compare(x, y, a["sample_rate_hz"], b["sample_rate_hz"],
                                       transforms=transforms, self_compare=ka == kb, min_fragment_s=min_fragment_s)
                write_json(stage / "comparison.json", {"matches": found, "search": stats})
            folder, hit, _ = cache_get(cache_dir, "reuse-comparisons", key, build_match)
            pair_cache_hits += int(hit)
            data = read_json(folder / "comparison.json")
            matches.extend(match_record(a, b, m) for m in data["matches"])
            comparisons.append({"a": ka, "b": kb, "match_count": len(data["matches"]), **data["search"]})
            if number % 5 == 0:
                print(f"Reuse: compared {number+1}/{len(pairs)} candidate pairs", file=sys.stderr, flush=True)
        index.close()
        index = None
        # Publish a self-contained ledger snapshot inside the immutable run.
        # The mutable index is updated only after the run has passed verification.
        with output_transaction(output, [config]) as stage:
            temporary_db = stage / "ledger.sqlite"
            if database.exists():
                source_db = connect(database)
                target_db = connect(temporary_db)
                try: source_db.backup(target_db)
                finally: source_db.close(); target_db.close()
            evidence_id = digest({"occurrences": [all_occ[k] for k in current], "matches": matches, "implementation": impl})
            ingest(temporary_db, [all_occ[k] for k in current], matches, evidence_id)
            graph = snapshot(temporary_db)
            policy = audit(graph)
            result = {"schema": SCHEMA, "occurrences": [all_occ[k] for k in current], "matches": matches,
                "periodicity": periods, "configuration": configuration,
                "search": {"comparisons": comparisons, "candidate_limit": max_candidates,
                           "retrieval_truncated_occurrences": truncated, "retrieval": "indexed spectral-shape candidate lookup",
                           "recall": "Not exhaustive; missing matches remain unresolved. Bounded transform search is optional.",
                           "transforms_enabled": transforms, "self_scan_enabled": self_scan,
                           "fingerprint_cache_hits": fingerprint_hits, "comparison_cache_hits": pair_cache_hits,
                           "native_cache": media.cache_hits, "elapsed_s": time.perf_counter()-started},
                "implementation": impl, "perceptual_review": "NOT_PERFORMED", "previews": {}, "evidence_id": evidence_id}
            for i, m in enumerate(sorted(matches, key=lambda e: -e["metrics"].get("native_ncc", e["metrics"].get("spectral_temporal_ncc", 0)))[:preview_count]):
                clips = {}
                (stage / "audio").mkdir(exist_ok=True)
                for side in ("a", "b"):
                    s = m[side]
                    o = all_occ[s["occurrence_id"]]
                    data = media.data(o)
                    first, last = s["input_sample_start"]-o["sample_start"], s["input_sample_end"]-o["sample_start"]
                    raw = data[first:last]
                    playable = np.rint(np.clip(raw, -1, 32767/32768)*32768).astype(np.int16)
                    name = f"audio/match-{i+1:02d}-{side}.wav"
                    write_wav(stage / name, o["sample_rate_hz"], playable)
                    clips[side] = name
                result["previews"][m["match_id"]] = clips
            write_json(stage / "reuse.json", result)
            write_json(stage / "graph.json", graph)
            write_json(stage / "audit.json", policy)
            write_json(stage / "baseline-contributions.json", policy["baseline_contributions_by_feature"])
            render(stage / "reuse.html", result, graph, policy)
            manifest = finish_run(stage, "reuse-compare" if compare_ids else "reuse-scan",
                list(media.sources.values())+dependencies, parameters={"min_fragment_s": min_fragment_s, "max_window_s": max_window_s},
                metadata={"result_file": "reuse.json", "match_count": len(matches), "graph_revision": graph["revision"],
                          "mapping_schema": "ave.reuse.correspondence.v1", "score_is_probability": False})
        verify_run(output)
        ingest(database, [all_occ[k] for k in current], matches, evidence_id)
        return manifest
    finally:
        if index is not None: index.close()


def export(database, output, *, revision=None, speaker=None, baseline=None, kind="graph"):
    graph = snapshot(database, revision, speaker)
    policy = audit(graph, read_baseline(baseline))
    dependencies = [file_record(database, "reuse_ledger")]
    if baseline: dependencies.append(file_record(baseline, "existing_baseline"))
    with output_transaction(output, [database]+([baseline] if baseline else [])) as stage:
        write_json(stage / "graph.json", graph)
        write_json(stage / "audit.json", policy)
        write_json(stage / "baseline-contributions.json", policy["baseline_contributions_by_feature"])
        result = {"schema": SCHEMA, "search": {"scope": "Previously recorded evidence; no new detection", "revision": graph["revision"]}, "previews": {}}
        write_json(stage / "reuse.json", result)
        render(stage / "reuse.html", result, graph, policy)
        manifest = finish_run(stage, "reuse-"+kind, dependencies,
            metadata={"result_file": "audit.json" if kind == "audit" else "graph.json", "graph_revision": graph["revision"]})
    return manifest
