"""Structural review validation with an explicit external perception boundary.

Receipts and reviewer declarations are inputs to an audit. Neither this module
nor a generated artifact can certify that a person or model perceived media.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import shutil

from .mapping import (union_intervals, missing_intervals as _missing,
                      map_review_intervals, estimated_overlap)

from .common import (AVError, file_record, finish_run, output_transaction,
                     read_json, safe_member, sha256, write_json)
from .inventory import (MODALITIES, date_value, digest_value, identifier, integer,
                        intervals, number, points, text_value, verify_run)

MECHANISMS = {"audio": "audio_playback", "motion": "video_playback", "visual_stills": "image_view"}
TRUST = ("Validation checks identity, declarations, receipts and interval arithmetic only. "
         "Capability and presentation receipts are externally trusted declarations; "
         "their presence and hashes do not prove authenticity, actual perception, "
         "attention, comprehension or the truth of an observation.")


class _Dependencies(set):
    """Retain the hashes observed at first read, rather than blessing later edits."""
    def __init__(self, paths=()):
        super().__init__(); self.hashes = {}
        for path in paths:
            self.add(path)

    def add(self, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise AVError(f"Missing validation dependency: {path}")
        digest = sha256(path)
        if path in self.hashes and self.hashes[path] != digest:
            raise AVError("A dependency changed during validation")
        self.hashes[path] = digest
        super().add(path)

    def records(self):
        result = []
        for path in sorted(self, key=str):
            record = file_record(path)
            if record["sha256"] != self.hashes[path]:
                raise AVError("A dependency changed during validation")
            result.append(record)
        return result


def _object(value, required, allowed, name):
    if not isinstance(value, dict) or not set(required) <= set(value):
        raise AVError(f"{name} is missing required fields")
    if set(value) - set(allowed):
        raise AVError(f"Unknown {name} fields: {sorted(set(value)-set(allowed))}")
    return value


def _reviewer(value):
    _object(value, {"id", "type", "name"}, {"id", "type", "name"}, "reviewer")
    identifier(value["id"], "reviewer id")
    if value["type"] not in ("human", "model"):
        raise AVError("Reviewer type must be human or model")
    text_value(value["name"], "reviewer name")
    return value


def _modality(value):
    if not isinstance(value, str) or value not in MODALITIES:
        raise AVError("Modality must separately name audio, motion or visual_stills")
    return value


def _date(value, name):
    d = date_value(value, name)
    if d > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise AVError(f"{name} is in the future")
    return d


def _input_file(base, value, name):
    text_value(value, name)
    p = Path(value)
    p = p.resolve() if p.is_absolute() else (base / p).resolve()
    if not p.is_file():
        raise AVError(f"Missing {name}: {p}")
    return p


def _manifest_reference(base, value, expected, dependencies):
    """Resolve moved sibling runs by recorded hash inside an ancestor bundle."""
    digest_value(expected, "referenced manifest hash")
    text_value(value, "referenced manifest path")
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (base / path).resolve()
    if path.is_file():
        if sha256(path) != expected:
            raise AVError("Referenced run manifest hash mismatch")
        return path
    search_roots = {base.resolve()} | {p.parent for p in dependencies if p.name == "run.json"}
    candidates = set()
    for initial in search_roots:
        for root in (initial, *initial.parents):
            bundle = root / "run.json"
            if not bundle.is_file():
                continue
            manifest = read_json(bundle)
            if not isinstance(manifest, dict):
                continue
            if sha256(bundle) == expected:
                candidates.add(bundle)
            for member in manifest.get("artifacts", []):
                if isinstance(member, dict) and member.get("sha256") == expected and str(member.get("path", "")).endswith("/run.json"):
                    candidate = safe_member(root, member["path"])
                    if candidate.is_file() and sha256(candidate) == expected:
                        candidates.add(candidate)
    if not candidates:
        raise AVError("Referenced run manifest is unavailable; include its hash-matched run in the bundle")
    # Identical manifest bytes copied twice are interchangeable for identity;
    # choose a complete verified copy, never a same-name substitute.
    for candidate in sorted(candidates, key=str):
        try:
            verify_run(candidate)
        except AVError:
            continue
        return candidate
    raise AVError("No complete hash-matched referenced run is available")


def _receipt(value, base, dependencies):
    _object(value, {"kind", "path", "sha256"}, {"kind", "path", "sha256"}, "receipt")
    if value["kind"] not in ("tool_receipt", "human_attestation"):
        raise AVError("Receipt kind must be tool_receipt or human_attestation")
    digest_value(value["sha256"], "receipt sha256")
    path = _input_file(base, value["path"], "receipt file")
    if sha256(path) != value["sha256"]:
        raise AVError("Receipt hash mismatch")
    if not path.stat().st_size:
        raise AVError("A receipt must not be empty")
    dependencies.add(path)
    return value


def _points_subset(wanted, available):
    return all(any(abs(x-y) <= 1e-6 for y in available) for x in wanted)


def _coverage_fields(record, modality, duration):
    if modality == "visual_stills":
        if "intervals_seconds" in record:
            raise AVError("Still images cannot declare temporal coverage intervals")
        return points(record.get("points_seconds"), duration)
    if "points_seconds" in record:
        raise AVError("Audio/motion coverage requires explicit intervals, not point samples")
    return intervals(record.get("intervals_seconds"), duration)


def _load_inventory(path, dependencies):
    supplied = Path(path).resolve()
    manifest_path = supplied / "run.json" if supplied.is_dir() else supplied if supplied.name == "run.json" else supplied.parent / "run.json"
    verify_run(manifest_path)
    dependencies.add(manifest_path)
    manifest = read_json(manifest_path)
    if manifest.get("operation") != "inventory":
        raise AVError("Reviews require an inventory run (select its child directory in a bundle)")
    result = manifest.get("metadata", {}).get("result_file")
    inventory_path = safe_member(manifest_path.parent, result)
    if supplied.is_file() and supplied.name != "run.json" and supplied != inventory_path:
        raise AVError("Specified inventory is not the run's verified result artifact")
    data = read_json(inventory_path)
    dependencies.add(inventory_path)
    if data.get("schema") != "ave.inventory.v1" or not data.get("sources") or not data.get("logical_sources"):
        raise AVError("Malformed admitted inventory")
    if data["sources"] != manifest["sources"]:
        raise AVError("Inventory source records differ from the run admission")
    sources = {}
    for source in data["sources"]:
        mid = identifier(source.get("materialization_id"), "materialization_id")
        if mid in sources:
            raise AVError("Duplicate inventory materialization_id")
        if source not in manifest["sources"]:
            raise AVError("Inventory source record differs from run admission")
        sources[mid] = source
    logical_ids = set()
    for logical in data["logical_sources"]:
        if not isinstance(logical, dict):
            raise AVError("Malformed logical source declaration")
        lid = identifier(logical.get("logical_source_id"), "logical_source_id")
        if lid in logical_ids:
            raise AVError("Duplicate logical source id")
        logical_ids.add(lid)
        preferred = sources.get(logical.get("preferred_materialization_id"))
        if not preferred or preferred["logical_source_id"] != lid or not preferred.get("preferred"):
            raise AVError("Invalid preferred materialization reference")
        required = logical.get("required_modalities")
        if not isinstance(required, list) or not required or not all(isinstance(x, str) for x in required) or set(required) - MODALITIES or required != preferred["required_modalities"]:
            raise AVError("Invalid logical source modality requirements")
        group = [x for x in sources.values() if x["logical_source_id"] == lid]
        if sorted(logical.get("materialization_ids", [])) != sorted(x["materialization_id"] for x in group) or sum(x.get("preferred") is True for x in group) != 1:
            raise AVError("Logical materialization membership or preference is inconsistent")
    if logical_ids != set(data.get("required_logical_source_ids", [])) or logical_ids != {x["logical_source_id"] for x in sources.values()}:
        raise AVError("Inventory completeness declarations disagree")
    return data, sources


def _source_for(record, sources):
    mid = identifier(record.get("materialization_id"), "materialization_id")
    lid = identifier(record.get("logical_source_id"), "logical_source_id")
    source = sources.get(mid)
    if not source or source["logical_source_id"] != lid:
        raise AVError("Review source/materialization pair is not admitted")
    if digest_value(record.get("source_sha256"), "review source hash") != source["sha256"]:
        raise AVError("Review source hash does not match the selected materialization")
    modality = _modality(record.get("modality"))
    kind = "audio" if modality == "audio" else "video"
    index = integer(record.get("stream_index"), "review stream_index")
    if source.get("selected_streams", {}).get(kind) != index:
        raise AVError("Review stream does not exactly match the admitted selected stream")
    return source


def _evidence_identity(evidence):
    return {k: v for k, v in evidence.items() if k not in ("derivative_intervals_seconds", "frame_ids")}


def _mappings(manifest):
    metadata = manifest.get("metadata", {})
    result = []
    if "review_mapping" in metadata:
        result.append(metadata["review_mapping"])
    if "review_mappings" in metadata:
        if not isinstance(metadata["review_mappings"], list):
            raise AVError("review_mappings must be a list")
        result.extend(metadata["review_mappings"])
    if not all(isinstance(x, dict) for x in result):
        raise AVError("Malformed derivative review mapping")
    return result


def _verified_derivative(evidence, base, dependencies):
    expected = digest_value(evidence.get("run_manifest_sha256"), "derivative run manifest hash")
    path = _manifest_reference(base, evidence.get("run_manifest_path"), expected, dependencies)
    verify_run(path)
    dependencies.add(path)
    manifest = read_json(path)
    relative = text_value(evidence.get("artifact_path"), "derivative artifact path")
    digest = digest_value(evidence.get("artifact_sha256"), "derivative artifact hash")
    if not any(a["path"] == relative and a["sha256"] == digest for a in manifest["artifacts"]):
        raise AVError("Derivative artifact/hash is absent from the verified manifest")
    dependencies.add(safe_member(path.parent, relative))
    return path, manifest


def _still_points(mapping, evidence, run_path, manifest, dependencies):
    kind = mapping.get("kind")
    result_file = manifest.get("metadata", {}).get("result_file")
    if not result_file:
        raise AVError("Still mapping requires a verified result file")
    result = read_json(safe_member(run_path.parent, result_file))
    dependencies.add(safe_member(run_path.parent, result_file))
    requested_ids = evidence.get("frame_ids")
    if not isinstance(requested_ids, list) or not requested_ids or len(set(requested_ids)) != len(requested_ids):
        raise AVError("Visual derivative evidence requires distinct frame_ids")
    for x in requested_ids:
        identifier(x, "frame_id")
    if kind == "still_points":
        if manifest.get("operation") != "extract_frames":
            raise AVError("Still review mapping requires an extract_frames run")
        frames = result.get("rows", []) if isinstance(result, dict) else []
        if not isinstance(frames, list) or not all(isinstance(x, dict) for x in frames):
            raise AVError("Malformed frame rows")
        if len({x.get("frame_id") for x in frames}) != len(frames):
            raise AVError("Duplicate frame IDs in the verified result")
        mapped_frames = mapping.get("frames", [])
        if not isinstance(mapped_frames, list) or not all(isinstance(x, dict) for x in mapped_frames) or len({x.get("frame_id") for x in mapped_frames}) != len(mapped_frames):
            raise AVError("Malformed or duplicate frame mapping IDs")
        points_by_id = {}
        for entry in mapped_frames:
            row = next((x for x in frames if x.get("frame_id") == entry.get("frame_id")), None)
            if not row or any(row.get(k) != v for k, v in {
                "path": entry.get("artifact_path"), "sha256": entry.get("artifact_sha256"),
                "source_sha256": mapping.get("parent_source_sha256"),
                "stream_index": mapping.get("parent_stream_index"),
                "source_seconds": entry.get("source_seconds")}.items()):
                raise AVError("Frame point mapping disagrees with verified frame rows")
            if entry.get("artifact_path") == evidence["artifact_path"] and entry.get("artifact_sha256") == evidence["artifact_sha256"]:
                points_by_id[entry["frame_id"]] = number(entry["source_seconds"], "frame source_seconds")
        if not set(requested_ids) <= set(points_by_id):
            raise AVError("Frame IDs are not represented by the selected image artifact")
        return [points_by_id[x] for x in requested_ids]
    if kind == "contact_sheet_points":
        if manifest.get("operation") != "contact_sheets":
            raise AVError("Contact-sheet point mapping requires a contact_sheets run")
        sheet = next((x for x in mapping.get("sheets", []) if x.get("artifact_path") == evidence["artifact_path"] and x.get("artifact_sha256") == evidence["artifact_sha256"]), None)
        rows = result.get("sheets", []) if isinstance(result, dict) else []
        row = next((x for x in rows if x.get("path") == evidence["artifact_path"]), None)
        if not sheet or not row or row.get("sha256") != evidence["artifact_sha256"] or row.get("frame_ids") != sheet.get("frame_ids"):
            raise AVError("Contact-sheet mapping disagrees with its verified result")
        if not set(requested_ids) <= set(sheet["frame_ids"]):
            raise AVError("Review names a frame absent from its contact sheet")
        parent = mapping.get("parent_frame_run")
        if parent != manifest.get("metadata", {}).get("parent_frame_run") or not isinstance(parent, dict):
            raise AVError("Contact-sheet parent run references disagree")
        parent_path = _manifest_reference(run_path.parent, parent.get("run_manifest_path"), parent.get("run_manifest_sha256"), dependencies)
        verify_run(parent_path)
        dependencies.add(parent_path)
        parent_manifest = read_json(parent_path)
        maps = [x for x in _mappings(parent_manifest) if x.get("kind") == "still_points" and x.get("parent_source_sha256") == mapping["parent_source_sha256"] and x.get("parent_stream_index") == mapping["parent_stream_index"]]
        if len(maps) != 1:
            raise AVError("Contact sheet has no unique matching parent point mapping")
        points_by_id = {}
        for frame in maps[0].get("frames", []):
            if frame.get("frame_id") in requested_ids:
                frame_evidence = {"artifact_path": frame["artifact_path"], "artifact_sha256": frame["artifact_sha256"], "frame_ids": [frame["frame_id"]]}
                points_by_id[frame["frame_id"]] = _still_points(maps[0], frame_evidence, parent_path, parent_manifest, dependencies)[0]
                dependencies.add(safe_member(parent_path.parent, frame["artifact_path"]))
        if set(points_by_id) != set(requested_ids):
            raise AVError("Contact sheet references missing parent frames")
        return [points_by_id[x] for x in requested_ids]
    raise AVError("The selected derivative is not a still-image presentation")


def _evidence_coverage(record, base, dependencies, source):
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        raise AVError("Missing evidence object")
    modality = record["modality"]
    claimed = _coverage_fields(record, modality, source["duration_seconds"])
    if evidence.get("kind") == "source":
        if set(evidence) != {"kind"}:
            raise AVError("Direct-source evidence accepts only kind; source/stream/time are explicit record fields")
        return claimed
    if evidence.get("kind") != "derivative":
        raise AVError("Metrics, ASR and generated summaries cannot stand in for audiovisual presentation")
    allowed = {"kind", "run_manifest_path", "run_manifest_sha256", "artifact_path", "artifact_sha256", "derivative_intervals_seconds", "derivative_stream_index", "frame_ids"}
    _object(evidence, {"kind", "run_manifest_path", "run_manifest_sha256", "artifact_path", "artifact_sha256"}, allowed, "derivative evidence")
    run_path, manifest = _verified_derivative(evidence, base, dependencies)
    mappings = [x for x in _mappings(manifest) if x.get("parent_source_sha256") == source["sha256"] and x.get("parent_stream_index") == record["stream_index"]]
    if modality == "visual_stills":
        if "derivative_intervals_seconds" in evidence or "derivative_stream_index" in evidence:
            raise AVError("Still-image evidence has points, not playback intervals/streams")
        mappings = [x for x in mappings if x.get("kind") in ("still_points", "contact_sheet_points")]
        if len(mappings) != 1:
            raise AVError("No unique matching still-image point mapping")
        mapped = _still_points(mappings[0], evidence, run_path, manifest, dependencies)
        if not _points_subset(claimed, mapped) or not _points_subset(mapped, claimed):
            raise AVError("Claimed source points differ from the presented derivative frames")
        return claimed
    if "frame_ids" in evidence:
        raise AVError("Still-frame IDs cannot count as listening or motion playback")
    permitted_operations = {"audio": {"extract_audio", "clip_audio", "clip-av", "admit_external_clip"},
                            "motion": {"clip-av", "admit_external_clip"}}
    if manifest.get("operation") not in permitted_operations[modality]:
        raise AVError("Only media-producing audio or AV clip runs can declare playback coverage; metrics/ASR cannot")
    index = integer(evidence.get("derivative_stream_index"), "derivative_stream_index")
    mappings = [x for x in mappings if x.get("modality") == modality and x.get("derivative_stream_index") == index and x.get("artifact_path") == evidence["artifact_path"] and x.get("artifact_sha256") == evidence["artifact_sha256"]]
    if len(mappings) != 1:
        raise AVError("No unique derivative mapping for exact parent/derivative streams and modality")
    mapping = mappings[0]
    if mapping.get("kind") in ("still_points", "contact_sheet_points") or not mapping.get("segments"):
        raise AVError("Point or feature artifacts cannot provide playback interval coverage")
    requested = intervals(evidence.get("derivative_intervals_seconds"), name="derivative intervals")
    parent_stream = next((s for s in source.get("streams", []) if s.get("index") == record["stream_index"]), None)
    derivative_stream = None
    if mapping.get("timing_basis"):
        # Re-probe the actual derivative; the allowance is not a caller-chosen epsilon.
        from .common import probe_source, select_stream
        derivative_source = probe_source(safe_member(run_path.parent, evidence["artifact_path"]))
        derivative_stream = select_stream(derivative_source, "audio" if modality == "audio" else "video", index)
    result = map_review_intervals(mapping, requested, claimed, source["duration_seconds"],
                                  parent_stream, derivative_stream)
    estimated = estimated_overlap(mapping, claimed)
    if estimated and "review_id" in record:
        if not hasattr(dependencies, "extent_qualifications"):
            dependencies.extent_qualifications = []
        dependencies.extent_qualifications.append({"review_id": record["review_id"],
            "materialization_id": record["materialization_id"], "modality": modality,
            "estimated_intervals_seconds": estimated,
            "basis": "ESTIMATED_FINAL_FRAME_EXTENT", "exact_extent_established": False})
    return result



def _records(path):
    try:
        if path.suffix.lower() == ".jsonl":
            records = []
            for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if line.strip():
                    records.append(json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(AVError(f"Nonfinite JSON at line {n}"))))
        else:
            records = read_json(path)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise AVError(f"Invalid review record JSON: {exc}") from exc
    if not isinstance(records, list):
        raise AVError("Review input must be a JSON array or JSONL records")
    return records


def validate_reviews(records, inventory_or_run, capabilities, output):
    """Validate declarations and report union coverage; raise AVError if invalid."""
    record_path, cap_path = Path(records).resolve(), Path(capabilities).resolve()
    dependencies = _Dependencies([record_path, cap_path])
    inventory_data, sources = _load_inventory(inventory_or_run, dependencies)
    declarations = read_json(cap_path)
    _object(declarations, {"schema", "capabilities", "presentations"}, {"schema", "capabilities", "presentations"}, "capabilities document")
    if declarations["schema"] != "ave.capabilities.v1" or not isinstance(declarations["capabilities"], list) or not isinstance(declarations["presentations"], list):
        raise AVError("Malformed capabilities document")
    caps, presented = {}, {}
    cap_fields = {"capability_id", "reviewer", "modality", "mechanism", "verified_at", "receipt"}
    for cap in declarations["capabilities"]:
        _object(cap, cap_fields, cap_fields, "capability")
        cid = identifier(cap["capability_id"], "capability_id")
        if cid in caps:
            raise AVError("Duplicate capability_id")
        _reviewer(cap["reviewer"]); modality = _modality(cap["modality"])
        if cap["mechanism"] != MECHANISMS[modality]:
            raise AVError("Capability mechanism does not present the declared modality")
        _date(cap["verified_at"], "capability verified_at")
        _receipt(cap["receipt"], cap_path.parent, dependencies)
        if cap["reviewer"]["type"] == "model" and cap["receipt"]["kind"] != "tool_receipt":
            raise AVError("Model capabilities require a tool presentation receipt, not a human substitute")
        caps[cid] = cap
    presentation_fields = {"presentation_id", "capability_id", "reviewer_id", "reviewer_type", "modality", "mechanism", "presented_at", "logical_source_id", "materialization_id", "source_sha256", "stream_index", "evidence", "receipt"}
    for item in declarations["presentations"]:
        _object(item, presentation_fields, presentation_fields | {"intervals_seconds", "points_seconds"}, "presentation")
        pid = identifier(item["presentation_id"], "presentation_id")
        if pid in presented:
            raise AVError("Duplicate presentation_id")
        cap = caps.get(identifier(item["capability_id"], "capability_id"))
        if not cap or item["reviewer_id"] != cap["reviewer"]["id"] or item["reviewer_type"] != cap["reviewer"]["type"] or item["modality"] != cap["modality"] or item["mechanism"] != cap["mechanism"]:
            raise AVError("Presentation is not bound to the declared reviewer/capability/modality/mechanism")
        if _date(item["presented_at"], "presentation presented_at") < date_value(cap["verified_at"]):
            raise AVError("Presentation predates capability verification")
        _receipt(item["receipt"], cap_path.parent, dependencies)
        if item["reviewer_type"] == "model" and item["receipt"]["kind"] != "tool_receipt":
            raise AVError("A model review requires its own tool presentation receipt")
        source = _source_for(item, sources)
        coverage = _evidence_coverage(item, cap_path.parent, dependencies, source)
        presented[pid] = (item, coverage)
    records_data = _records(record_path)
    review_fields = {"schema", "review_id", "reviewer", "reviewed_at", "logical_source_id", "materialization_id", "source_sha256", "stream_index", "modality", "capability_id", "presentation_id", "evidence", "observation"}
    seen, coverage = set(), defaultdict(list)
    for record in records_data:
        _object(record, review_fields, review_fields | {"intervals_seconds", "points_seconds"}, "review record")
        if record["schema"] != "ave.review.v1":
            raise AVError("Unsupported review schema")
        rid = identifier(record["review_id"], "review_id")
        if rid in seen:
            raise AVError("Duplicate review_id")
        seen.add(rid)
        reviewer = _reviewer(record["reviewer"])
        text_value(record["observation"], "observation")
        reviewed_at = _date(record["reviewed_at"], "review reviewed_at")
        source = _source_for(record, sources)
        cap = caps.get(identifier(record["capability_id"], "capability_id"))
        presentation = presented.get(identifier(record["presentation_id"], "presentation_id"))
        if not cap or cap["reviewer"] != reviewer or cap["modality"] != record["modality"] or not presentation:
            raise AVError("Review lacks matching capability and presentation references")
        item, presentation_coverage = presentation
        for key in ("capability_id", "logical_source_id", "materialization_id", "source_sha256", "stream_index", "modality"):
            if item[key] != record[key]:
                raise AVError(f"Review/presentation mismatch: {key}")
        if _evidence_identity(item["evidence"]) != _evidence_identity(record["evidence"]):
            raise AVError("Review and presentation identify different evidence artifacts")
        if reviewed_at < date_value(item["presented_at"]):
            raise AVError("Review predates presentation")
        declared = _evidence_coverage(record, record_path.parent, dependencies, source)
        if record["modality"] == "visual_stills":
            if not _points_subset(declared, presentation_coverage):
                raise AVError("Review points were not included in the presentation declaration")
        elif _missing(declared, presentation_coverage):
            raise AVError("Review intervals exceed the declared presentation")
        coverage[(record["materialization_id"], record["modality"])].extend(declared)
    results, all_full = [], True
    for logical in inventory_data["logical_sources"]:
        preferred = sources[logical["preferred_materialization_id"]]
        modal_results = []
        for modality in logical["required_modalities"]:
            declared = coverage[(preferred["materialization_id"], modality)]
            if modality == "visual_stills":
                required = points(preferred.get("required_points_seconds"), preferred["duration_seconds"])
                reviewed = sorted(set(declared))
                missing = [x for x in required if not _points_subset([x], reviewed)]
                item = {"modality": modality, "required_points_seconds": required, "declared_points_seconds": reviewed, "missing_points_seconds": missing,
                        "coverage_meaning": "Only the explicitly required still points; no continuous motion or audio coverage"}
            else:
                required = intervals(preferred.get("required_intervals_seconds", {}).get(modality), preferred["duration_seconds"])
                reviewed = union_intervals(declared); missing = _missing(required, reviewed)
                item = {"modality": modality, "required_intervals_seconds": union_intervals(required), "declared_union_intervals_seconds": reviewed, "missing_intervals_seconds": missing,
                        "declared_seconds_within_requirements": sum(b-a for a, b in union_intervals(required)) - sum(b-a for a, b in missing)}
            item["full_declared_coverage"] = not missing
            all_full = all_full and not missing
            modal_results.append(item)
        results.append({"logical_source_id": logical["logical_source_id"], "preferred_materialization_id": preferred["materialization_id"],
                        "modalities": modal_results, "full_required_declared_coverage": all(x["full_declared_coverage"] for x in modal_results)})
    qualifications = getattr(dependencies, "extent_qualifications", [])
    exact_full = bool(all_full) and not qualifications
    report = {"schema": "ave.review.validation.v1", "structural_valid": True,
              "full_required_coverage": bool(all_full), "status": ("FULL_DECLARED_COVERAGE_WITH_ESTIMATED_EXTENT" if all_full and qualifications else
                         "FULL_DECLARED_COVERAGE" if all_full else "PARTIAL_DECLARED_COVERAGE"),
              "full_required_exact_extent_coverage": exact_full, "extent_qualifications": qualifications,
              "perception_truth": "NOT_ESTABLISHED_BY_VALIDATION", "trust_boundary": TRUST,
              "review_count": len(records_data), "capability_count": len(caps), "presentation_count": len(presented),
              "coverage": results, "other_materialization_policy": "Alternate materialization reviews are retained but do not fill preferred-materialization gaps without an explicit parent mapping.",
              "reviews": records_data, "capability_declarations": declarations,
              "inventory_sha256": sha256(next(x for x in dependencies if x.name == "inventory.json"))}
    with output_transaction(output, dependencies) as stage:
        import copy
        portable = copy.deepcopy(declarations)
        portable_records = copy.deepcopy(records_data)
        references, inventory_relative = _archive_run_closure(stage, dependencies)
        _rebase_evidence(portable["presentations"], references)
        _rebase_evidence(portable_records, references)
        archived = []
        for i, item in enumerate(portable["capabilities"] + portable["presentations"]):
            receipt = item["receipt"]
            p = _input_file(cap_path.parent, receipt["path"], "receipt")
            relative = f"receipts/{i:04}{p.suffix or '.bin'}"
            target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(p.read_bytes())
            if sha256(target) != receipt["sha256"]:
                raise AVError("Receipt changed while archiving")
            archived.append({"original_path": receipt["path"], "artifact_path": relative, "sha256": receipt["sha256"], "kind": receipt["kind"]})
            receipt["path"] = relative
        report["receipt_artifacts"] = archived
        report["portable_declarations"] = {"reviews": "reviews.json", "capabilities": "capabilities.json",
                                             "inventory": inventory_relative,
                                             "dependency_runs": references,
                                             "note": "Receipts and immutable run closure are archived; declaration paths are rebased, assertions unchanged. Source bytes remain external for opt-in source re-verification."}
        write_json(stage / "reviews.original.json", records_data)
        write_json(stage / "capabilities.original.json", declarations)
        write_json(stage / "reviews.json", portable_records)
        write_json(stage / "capabilities.json", portable)
        write_json(stage / "review_validation.json", report)
        return finish_run(stage, "validate_reviews", dependencies.records(),
                          {"records_sha256": sha256(record_path), "capabilities_sha256": sha256(cap_path)},
                          {"result_file": "review_validation.json", "structural_valid": True,
                           "full_required_coverage": bool(all_full),
                           "full_required_exact_extent_coverage": exact_full,
                           "extent_qualifications": qualifications,
                           "perception_truth": "NOT_ESTABLISHED_BY_VALIDATION"})


def _archive_run_closure(stage, dependencies):
    """Copy immutable, fully verified runs without rebasing their internal bytes.

    Parent-frame manifests discovered during validation are dependencies too.
    Ancestor bundle hash lookup resolves their old paths after relocation.
    """
    references = {}
    inventory_relative = None
    for path in sorted((p for p in dependencies if p.name == "run.json"), key=str):
        expected = dependencies.hashes[path]
        if sha256(path) != expected:
            raise AVError("Run changed before portable archiving")
        relative = f"dependencies/{expected}/run.json"
        destination = stage / relative
        if expected not in references:
            verify_run(path)
            shutil.copytree(path.parent, destination.parent)
            if sha256(destination) != expected:
                raise AVError("Archived manifest identity changed")
            verify_run(destination)
            references[expected] = relative
        if read_json(path).get("operation") == "inventory":
            inventory_relative = relative
    if inventory_relative is None:
        raise AVError("Portable review closure lacks its admitted inventory")
    return references, inventory_relative


def _rebase_evidence(records, references):
    for item in records:
        evidence = item.get("evidence", {})
        if evidence.get("kind") == "derivative":
            digest = evidence["run_manifest_sha256"]
            if digest not in references:
                raise AVError("Portable review closure lacks a derivative manifest")
            evidence["run_manifest_path"] = references[digest]


def write_review_templates(output):
    """Generate conspicuously incomplete declarations, never review evidence."""
    reviewer = {"id": "REPLACE_REVIEWER_ID", "type": "human", "name": "REPLACE_REVIEWER_NAME"}
    receipt = {"kind": "human_attestation", "path": "REPLACE_RECEIPT_PATH", "sha256": "REPLACE_SHA256"}
    evidence = {"kind": "source"}
    cap = {"capability_id": "REPLACE_CAPABILITY_ID", "reviewer": reviewer, "modality": "audio", "mechanism": "audio_playback", "verified_at": "REPLACE_TIMEZONE_DATE", "receipt": receipt}
    presentation = {"presentation_id": "REPLACE_PRESENTATION_ID", "capability_id": cap["capability_id"], "reviewer_id": reviewer["id"], "reviewer_type": reviewer["type"], "modality": "audio", "mechanism": "audio_playback", "presented_at": "REPLACE_TIMEZONE_DATE", "logical_source_id": "REPLACE_LOGICAL_SOURCE_ID", "materialization_id": "REPLACE_MATERIALIZATION_ID", "source_sha256": "REPLACE_SHA256", "stream_index": 0, "intervals_seconds": [[0, 1]], "evidence": evidence, "receipt": receipt}
    record = {"schema": "ave.review.v1", "review_id": "REPLACE_REVIEW_ID", "reviewer": reviewer, "reviewed_at": "REPLACE_TIMEZONE_DATE", "logical_source_id": presentation["logical_source_id"], "materialization_id": presentation["materialization_id"], "source_sha256": presentation["source_sha256"], "stream_index": 0, "modality": "audio", "capability_id": cap["capability_id"], "presentation_id": presentation["presentation_id"], "intervals_seconds": [[0, 1]], "evidence": evidence, "observation": "REPLACE_WITH_YOUR_ACTUAL_OBSERVATION"}
    with output_transaction(output) as stage:
        write_json(stage / "capabilities.json", {"schema": "ave.capabilities.v1", "capabilities": [cap], "presentations": [presentation]})
        write_json(stage / "reviews.json", [record])
        return finish_run(stage, "review_templates", [], metadata={"result_file": "reviews.json", "template_status": "PLACEHOLDERS_MUST_BE_COMPLETED_AFTER_ACTUAL_REVIEW", "perception_truth": "NOT_ESTABLISHED_BY_VALIDATION"})
