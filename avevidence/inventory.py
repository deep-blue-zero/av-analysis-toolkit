"""Explicit source admission and portable run verification.

Hashes bind bytes, not source authenticity or perceptual review. Bundle checking
does not require another person's original absolute media paths to exist.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
import re

from .common import (AVError, finish_run, output_transaction, probe_source,
                     read_json, safe_member, select_stream, sha256, utc_now,
                     write_json)

MODALITIES = {"audio", "motion", "visual_stills"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER|REPLACE(?:_[A-Z0-9]+)*|YOUR_[A-Z0-9_]+)\b|<[^>]+>", re.I)


def text_value(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AVError(f"{name} must be a nonempty trimmed string")
    if _PLACEHOLDER.search(value):
        raise AVError(f"{name} contains an unfinished placeholder")
    return value


def identifier(value, name="identifier"):
    text_value(value, name)
    if not _ID.fullmatch(value):
        raise AVError(f"Invalid {name}")
    return value


def digest_value(value, name="SHA-256"):
    if not isinstance(value, str) or not _HASH.fullmatch(value) or len(set(value)) == 1:
        raise AVError(f"{name} must be a completed lowercase SHA-256 digest")
    return value


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise AVError(f"{name} must be an integer >= {minimum}")
    return value


def number(value, name, minimum=0):
    import math
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise AVError(f"{name} must be a finite JSON number >= {minimum}")
    return float(value)


def date_value(value, name="date"):
    text_value(value, name)
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", value):
        raise AVError(f"{name} must be an ISO date-time with an explicit timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AVError(f"Invalid {name}") from exc
    if parsed.utcoffset() is None:
        raise AVError(f"{name} requires a timezone")
    return parsed


def intervals(value, duration=None, name="intervals_seconds"):
    if not isinstance(value, list) or not value:
        raise AVError(f"{name} must be a nonempty list of [start, end] pairs")
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise AVError(f"Malformed {name} pair")
        a, b = [number(x, name) for x in pair]
        if a >= b or (duration is not None and b > duration + 1e-6):
            raise AVError(f"Invalid or out-of-bounds {name}")
        result.append([a, b])
    return result


def points(value, duration=None, name="points_seconds"):
    if not isinstance(value, list) or not value:
        raise AVError(f"{name} must be a nonempty list")
    result = [number(x, name) for x in value]
    if len(set(result)) != len(result):
        raise AVError(f"Duplicate {name}")
    if duration is not None and any(x >= duration for x in result):
        raise AVError(f"Out-of-bounds {name}")
    return sorted(result)


def _config(path):
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list) or not data["sources"]:
        raise AVError("Inventory config requires a nonempty sources list")
    allowed = {"schema", "sources", "required_logical_source_ids"}
    if set(data) - allowed:
        raise AVError(f"Unknown inventory config fields: {sorted(set(data) - allowed)}")
    if data.get("schema", "ave.inventory.config.v1") != "ave.inventory.config.v1":
        raise AVError("Unsupported inventory config schema")
    return data


def inventory(config, output):
    """Admit every configured file; fail transactionally on omissions/ambiguity."""
    config = Path(config).resolve()
    data = _config(config)
    rows, logical = [], defaultdict(list)
    seen = set()
    allowed = {"logical_source_id", "materialization_id", "path", "selected_streams",
               "required_modalities", "required_intervals_seconds", "required_points_seconds",
               "preferred", "evidence_weight_group", "expected_sha256", "title", "notes"}
    for row in data["sources"]:
        if not isinstance(row, dict) or set(row) - allowed:
            raise AVError("Invalid/unknown materialization config fields")
        lid = identifier(row.get("logical_source_id"), "logical_source_id")
        mid = identifier(row.get("materialization_id"), "materialization_id")
        if mid in seen:
            raise AVError(f"Duplicate materialization_id: {mid}")
        seen.add(mid)
        path = Path(text_value(row.get("path"), "source path"))
        path = (config.parent / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise AVError(f"Configured source is missing: {mid}: {path}")
        modalities = row.get("required_modalities")
        if not isinstance(modalities, list) or not modalities or not all(isinstance(x, str) for x in modalities) or len(set(modalities)) != len(modalities) or set(modalities) - MODALITIES:
            raise AVError("Each source requires distinct explicit required_modalities")
        selected = row.get("selected_streams")
        if not isinstance(selected, dict) or set(selected) - {"audio", "video"}:
            raise AVError("selected_streams must declare absolute audio/video indices")
        for kind, index in selected.items():
            integer(index, f"selected {kind} index")
        needed = {"audio" if x == "audio" else "video" for x in modalities}
        if not needed <= set(selected):
            raise AVError("A required modality has no explicitly selected stream")
        if "preferred" in row and type(row["preferred"]) is not bool:
            raise AVError("preferred must be a JSON boolean")
        if "expected_sha256" in row:
            digest_value(row["expected_sha256"], "expected_sha256")
        for key in ("title", "notes", "evidence_weight_group"):
            if key in row:
                text_value(row[key], key)
        if "evidence_weight_group" in row:
            identifier(row["evidence_weight_group"], "evidence_weight_group")
        item = dict(row, path=str(path), required_modalities=sorted(modalities))
        logical[lid].append(item)
        rows.append(item)
    required = data.get("required_logical_source_ids", sorted(logical))
    if not isinstance(required, list):
        raise AVError("required_logical_source_ids must be a distinct list")
    for x in required:
        identifier(x, "required_logical_source_id")
    if len(set(required)) != len(required):
        raise AVError("required_logical_source_ids must be a distinct list")
    if set(required) != set(logical):
        raise AVError("Configured logical source set does not match required_logical_source_ids")
    for lid, group in logical.items():
        if len({tuple(x["required_modalities"]) for x in group}) != 1:
            raise AVError(f"Conflicting required modalities for {lid}")
        for x in group:
            x["preferred"] = x.get("preferred", len(group) == 1)
        if sum(x["preferred"] for x in group) != 1:
            raise AVError(f"Exactly one preferred materialization is required for {lid}")

    with output_transaction(output, [config] + [r["path"] for r in rows]) as stage:
        sources = []
        for row in rows:
            source = probe_source(row["path"])
            if row.get("expected_sha256", source["sha256"]) != source["sha256"]:
                raise AVError(f"Expected source hash mismatch: {row['materialization_id']}")
            duration = number(source.get("duration_seconds"), "source duration")
            if duration <= 0:
                raise AVError("A positive source duration is required for inventory coverage")
            for kind, index in row["selected_streams"].items():
                select_stream(source, kind, index)
            source.update({k: v for k, v in row.items() if k not in ("path", "expected_sha256")})
            req_intervals = row.get("required_intervals_seconds", {})
            if not isinstance(req_intervals, dict) or set(req_intervals) - (set(row["required_modalities"]) - {"visual_stills"}):
                raise AVError("required_intervals_seconds may only name required audio/motion modalities")
            source["required_intervals_seconds"] = {
                m: intervals(req_intervals.get(m, [[0, duration]]), duration, f"required {m} intervals")
                for m in row["required_modalities"] if m != "visual_stills"}
            if "visual_stills" in row["required_modalities"]:
                source["required_points_seconds"] = points(row.get("required_points_seconds"), duration, "required_points_seconds")
            elif "required_points_seconds" in row:
                raise AVError("required_points_seconds requires the visual_stills modality")
            sources.append(source)

        # Connected components join same logical source, declared relationship,
        # and identical bytes. Copies cannot increase independent evidence weight.
        parent = {x["materialization_id"]: x["materialization_id"] for x in sources}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        def join(a, b):
            parent[find(b)] = find(a)
        for key in ("logical_source_id", "sha256", "evidence_weight_group"):
            buckets = defaultdict(list)
            for x in sources:
                if key in x:
                    buckets[x[key]].append(x["materialization_id"])
            for group in buckets.values():
                for mid in group[1:]:
                    join(group[0], mid)
        components = defaultdict(list)
        for x in sources:
            components[find(x["materialization_id"])].append(x)
        weights = []
        for i, group in enumerate(sorted(components.values(), key=lambda g: min(x["materialization_id"] for x in g)), 1):
            gid = f"evidence-{i:04}"
            for x in group:
                x["computed_evidence_weight_group"] = gid
            weights.append({"group_id": gid, "materialization_ids": sorted(x["materialization_id"] for x in group),
                            "logical_source_ids": sorted({x["logical_source_id"] for x in group}),
                            "maximum_independent_evidence_units": 1})
        byte_groups = defaultdict(list)
        for x in sources:
            byte_groups[x["sha256"]].append(x["materialization_id"])
        admitted = {"schema": "ave.inventory.v1", "created_at": utc_now(),
                    "status": "ADMITTED_CONFIG_COMPLETE_NOT_REVIEWED",
                    "completeness_scope": "Exactly the declared config; no external corpus completeness claim",
                    "required_logical_source_ids": sorted(required), "sources": sources,
                    "logical_sources": [{"logical_source_id": lid,
                        "materialization_ids": sorted(x["materialization_id"] for x in sources if x["logical_source_id"] == lid),
                        "preferred_materialization_id": next(x["materialization_id"] for x in sources if x["logical_source_id"] == lid and x["preferred"]),
                        "required_modalities": logical[lid][0]["required_modalities"]} for lid in sorted(logical)],
                    "byte_duplicate_groups": [{"sha256": h, "materialization_ids": ids} for h, ids in sorted(byte_groups.items()) if len(ids) > 1],
                    "evidence_weight_groups": weights,
                    "trust_boundary": "Identity and relationships are declared; hashes establish byte equality, not authenticity or perception."}
        write_json(stage / "inventory.json", admitted)
        return finish_run(stage, "inventory", sources,
                          {"config_sha256": sha256(config)}, {"result_file": "inventory.json"})


def _artifact_path(root, relative):
    text_value(relative, "artifact path")
    if PurePosixPath(relative).as_posix() != relative or relative in (".", "run.json"):
        raise AVError("Artifact paths must be canonical relative file paths and exclude the owning run.json")
    return safe_member(root, relative)


def _validate_source_record(source):
    if not isinstance(source, dict):
        raise AVError("Malformed run source")
    digest_value(source.get("sha256"), "source sha256")
    integer(source.get("size_bytes"), "source size_bytes")
    text_value(source.get("path"), "source path")
    if "streams" in source:
        if not isinstance(source["streams"], list):
            raise AVError("Source streams must be a list")
        seen_indices = set()
        for stream in source["streams"]:
            if not isinstance(stream, dict):
                raise AVError("Malformed source stream")
            index = integer(stream.get("index"), "source stream index")
            if index in seen_indices:
                raise AVError("Duplicate source stream index")
            seen_indices.add(index)
            text_value(stream.get("codec_type"), "stream codec_type")
    for key in ("logical_source_id", "materialization_id"):
        if key in source:
            identifier(source[key], key)
    if "selected_streams" in source:
        if not isinstance(source["selected_streams"], dict):
            raise AVError("selected_streams must be an object")
        for kind, index in source["selected_streams"].items():
            if kind not in ("audio", "video"):
                raise AVError("Invalid selected stream kind")
            integer(index, "selected stream index")
            select_stream(source, kind, index)


def _resolve_source_for_verification(source, root):
    """Use exact internal bytes when a published/moved run lost its stage path."""
    recorded = Path(source["path"])
    recorded = recorded if recorded.is_absolute() else root / recorded
    if recorded.is_file():
        if recorded.stat().st_size == source["size_bytes"] and sha256(recorded) == source["sha256"]:
            return recorded
        raise AVError("Original source verification failed: recorded file changed")
    for ancestor in (root, *root.parents):
        candidate_manifest = ancestor / "run.json"
        if not candidate_manifest.is_file():
            continue
        data = read_json(candidate_manifest)
        if not isinstance(data, dict) or data.get("schema") != "ave.run.v1":
            continue
        for member in data.get("artifacts", []):
            if not isinstance(member, dict) or member.get("sha256") != source["sha256"] or member.get("size_bytes") != source["size_bytes"]:
                continue
            candidate = safe_member(ancestor, member.get("path"))
            if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size == source["size_bytes"] and sha256(candidate) == source["sha256"]:
                return candidate
    raise AVError("Original source verification failed: no original or exact hash-bound internal copy")


def verify_run(path, verify_sources=False):
    """Verify artifact identity/membership recursively; no perception assertion."""
    if type(verify_sources) is not bool:
        raise AVError("verify_sources must be a boolean")
    supplied = Path(path)
    manifest_path = supplied / "run.json" if supplied.is_dir() else supplied
    if manifest_path.name != "run.json" or not manifest_path.is_file() or manifest_path.is_symlink():
        raise AVError("Expected a regular run.json or run directory")
    root = manifest_path.parent.resolve()
    data = read_json(manifest_path)
    if not isinstance(data, dict) or data.get("schema") != "ave.run.v1":
        raise AVError("Unsupported run manifest")
    date_value(data.get("created_at"), "run created_at")
    if data.get("status") != "GENERATED_NOT_REVIEWED":
        raise AVError("Run status must retain GENERATED_NOT_REVIEWED")
    text_value(data.get("operation"), "operation")
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise AVError("Run sources must be a list")
    for source in sources:
        _validate_source_record(source)
        if verify_sources:
            _resolve_source_for_verification(source, root)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        raise AVError("Run artifacts must be a list")
    declared = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise AVError("Malformed artifact record")
        relative = artifact.get("path")
        p = _artifact_path(root, relative)
        if relative in declared:
            raise AVError("Duplicate artifact member")
        declared.add(relative)
        digest_value(artifact.get("sha256"), "artifact sha256")
        integer(artifact.get("size_bytes"), "artifact size_bytes")
        if not p.is_file() or p.is_symlink() or p.stat().st_size != artifact["size_bytes"] or sha256(p) != artifact["sha256"]:
            raise AVError(f"Artifact verification failed: {relative}")
    actual = set()
    for p in root.rglob("*"):
        if p.is_symlink():
            raise AVError("Symlink bundle members are not allowed")
        if p.is_file() and p != manifest_path.resolve():
            actual.add(p.relative_to(root).as_posix())
    if actual != declared:
        raise AVError(f"Artifact membership mismatch; missing={sorted(declared-actual)}, unlisted={sorted(actual-declared)}")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise AVError("Run metadata must be an object")
    result_file = metadata.get("result_file")
    if result_file is not None and result_file not in declared:
        raise AVError("metadata.result_file does not reference a listed artifact")
    mappings = []
    if "review_mapping" in metadata:
        if not isinstance(metadata["review_mapping"], dict):
            raise AVError("review_mapping must be an object")
        mappings.append(metadata["review_mapping"])
    if "review_mappings" in metadata:
        if not isinstance(metadata["review_mappings"], list):
            raise AVError("review_mappings must be a list")
        mappings.extend(metadata["review_mappings"])
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise AVError("Malformed review mapping")
        parent_hash = digest_value(mapping.get("parent_source_sha256"), "mapping parent source hash")
        stream_index = integer(mapping.get("parent_stream_index"), "mapping parent stream index")
        matching = [x for x in sources if x["sha256"] == parent_hash]
        direct = matching and any(any(s.get("index") == stream_index for s in x.get("streams", [])) for x in matching)
        if not direct:
            link = mapping.get("parent_frame_run")
            # A contact sheet depends on a frame-run manifest, not on original
            # media bytes. The declared hash must bind that indirection; review
            # validation separately resolves/checks the parent's point graph.
            indirect = (mapping.get("kind") == "contact_sheet_points" and isinstance(link, dict)
                        and link == metadata.get("parent_frame_run")
                        and any(x["sha256"] == link.get("run_manifest_sha256") for x in sources))
            if not indirect:
                raise AVError("Review mapping refers to an absent parent source/stream")
            digest_value(link.get("run_manifest_sha256"), "parent frame manifest hash")
            text_value(link.get("run_manifest_path"), "parent frame manifest path")
        members = [mapping] if "artifact_path" in mapping else mapping.get("frames", mapping.get("sheets", []))
        if not isinstance(members, list):
            raise AVError("Malformed mapping members")
        for member in members:
            if not isinstance(member, dict) or member.get("artifact_path") not in declared:
                raise AVError("Review mapping artifact is not a manifest member")
            expected = next(a for a in artifacts if a["path"] == member["artifact_path"])
            if member.get("artifact_sha256") != expected["sha256"]:
                raise AVError("Review mapping artifact hash mismatch")
    children = []
    for relative in sorted(declared):
        if PurePosixPath(relative).name == "run.json":
            children.append(verify_run(root / relative, verify_sources=verify_sources))
    return {"schema": "ave.verification.v1", "structural_validity": "VALID",
            "manifest_sha256": sha256(manifest_path), "artifact_count": len(artifacts),
            "source_count": len(sources), "original_sources_verified": verify_sources,
            "nested_runs": children, "perception_truth": "NOT_ESTABLISHED_BY_VALIDATION",
            "trust_boundary": "Portable hashes validate recorded bytes and membership. Authenticity, declared capabilities, presentation and actual perception remain external trust boundaries."}
