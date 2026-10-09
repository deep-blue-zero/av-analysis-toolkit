"""Hash-bound dramatic events. Preparation is not perceptual adjudication."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re

from .common import AVError, file_record, finish_run, output_transaction, sha256, write_json
from .event_contracts import (CHANNELS, CLOCK, PROPOSITIONS, STATES, TEMPORAL_STATES, array,
                              canonical_digest, choice, object_fields, read_event_json, span, strings)
from .inventory import digest_value, identifier, integer, number, text_value
from .observation_dependencies import PROPAGATING, validate_graph

SCHEMA = "ave.scene-packet.v1"
MAX_EVENT_SECONDS = 600.


def packet_identity(packet):
    """Logical identity survives explicit hash-checked physical relocation."""
    value = copy.deepcopy(packet)
    value.pop("packet_id", None)
    value["source"].pop("path", None)
    for rows in value["evidence"].values():
        for ref in rows:
            ref["artifact"].pop("path", None)
            if ref.get("review_artifact"):
                ref["review_artifact"].pop("path", None)
    return "scene-" + canonical_digest(value)


def _artifact(value, name):
    object_fields(value, {"path", "sha256"}, {"path", "sha256"}, name)
    text_value(value["path"], name+" path")
    digest_value(value["sha256"], name+" digest")


def resolve_artifact(artifact, base, relocations=None):
    """No filename search, fallback to changed files, or unverified remapping."""
    _artifact(artifact, "artifact")
    path = Path(artifact["path"])
    path = path if path.is_absolute() else Path(base)/path
    if path.is_symlink():
        raise AVError("Evidence artifact cannot be a symbolic link")
    path = path.resolve()
    if path.exists():
        if path.is_symlink() or not path.is_file() or sha256(path) != artifact["sha256"]:
            raise AVError("Evidence artifact changed or is not a regular file")
        return path
    mapping = relocations or {}
    candidate = mapping.get(artifact["sha256"])
    if candidate is None:
        raise AVError("Evidence artifact missing; supply an explicit digest-to-path relocation")
    text_value(candidate, "relocated artifact path")
    target = Path(candidate)
    target = (target if target.is_absolute() else Path(base)/target).resolve()
    if target.is_symlink() or not target.is_file() or sha256(target) != artifact["sha256"]:
        raise AVError("Explicit evidence relocation failed its digest check")
    return target


def _locator(value, source, bounds):
    object_fields(value, {"source_sha256", "clock", "stream_index", "intervals_seconds", "points_seconds"},
                  {"source_sha256", "clock"}, "scene evidence locator")
    if value["source_sha256"] != source["sha256"] or value["clock"] != CLOCK:
        raise AVError("Scene evidence source or clock differs")
    if "stream_index" in value:
        integer(value["stream_index"], "absolute stream index")
    intervals = value.get("intervals_seconds", [])
    points = value.get("points_seconds", [])
    array(intervals, "evidence intervals", 100)
    array(points, "evidence points", 1000)
    if not intervals and not points:
        raise AVError("Scene evidence needs an interval or a source-frame point")
    for item in intervals:
        span(item, bounds, "evidence interval")
    for item in points:
        moment = number(item, "evidence point")
        if not bounds[0] <= moment < bounds[1]:
            raise AVError("Evidence point is outside the half-open scene interval")


def validate_packet(packet):
    required = {"schema", "scene_id", "source", "interval_seconds", "narrative_boundary", "question",
                "evidence", "observations", "claims", "dependencies", "conflicts", "adjudication", "open_questions"}
    object_fields(packet, required | {"packet_id", "holistic_analysis"}, required, "scene packet")
    if packet["schema"] != SCHEMA:
        raise AVError("Unsupported scene packet schema")
    identifier(packet["scene_id"], "scene ID")
    source = packet["source"]
    object_fields(source, {"sha256", "clock", "path", "duration_seconds", "audio_stream", "video_stream"},
                  {"sha256", "clock"}, "event source")
    digest_value(source["sha256"], "event source digest")
    if source["clock"] != CLOCK:
        raise AVError("Scene packet requires the original source clock")
    if "path" in source:
        text_value(source["path"], "source path")
    for key in ("audio_stream", "video_stream"):
        if key in source:
            integer(source[key], key)
    bounds = span(packet["interval_seconds"])
    if bounds[1]-bounds[0] > MAX_EVENT_SECONDS:
        raise AVError("Targeted scene exceeds 600 seconds; divide it at declared narrative boundaries")
    if "duration_seconds" in source and bounds[1] > number(source["duration_seconds"], "source duration")+1e-9:
        raise AVError("Scene interval exceeds declared source duration")
    boundary = packet["narrative_boundary"]
    object_fields(boundary, {"basis", "description"}, {"basis", "description"}, "narrative boundary")
    for key in ("basis", "description"):
        text_value(boundary[key], "boundary "+key)
    text_value(packet["question"], "dramatic event question")
    object_fields(packet["evidence"], CHANNELS, name="evidence channels")
    evidence = {}
    for channel, refs in packet["evidence"].items():
        for ref in array(refs, channel+" evidence", 1000):
            object_fields(ref, {"id", "artifact", "locator", "record_pointer", "authority", "origin_ids",
                          "shared_assumptions", "context_evidence_ids", "stage1_evidence_id", "review_artifact", "independence_declaration"},
                          {"id", "artifact", "locator"}, "evidence reference")
            eid = identifier(ref["id"], "evidence ID")
            if eid in evidence:
                raise AVError("Evidence IDs must be unique across channels")
            _artifact(ref["artifact"], "raw evidence")
            _locator(ref["locator"], source, bounds)
            selected_key = "audio_stream" if channel in {"AM", "AO_STAGE1", "AO_STAGE2", "HL", "MID"} else "video_stream" if channel in {"VIS", "TVIS", "VO"} else None
            if selected_key and selected_key in source and ref["locator"].get("stream_index") != source[selected_key]:
                raise AVError("Evidence locator differs from the scene's explicitly selected stream")
            if channel in {"AO_STAGE1", "AO_STAGE2", "HL", "AM", "TVIS", "VO", "AVO", "MID"} and "stream_index" not in ref["locator"]:
                raise AVError("Temporal/audio evidence needs its selected absolute stream")
            if "record_pointer" in ref:
                text_value(ref["record_pointer"], "record pointer")
                if not ref["record_pointer"].startswith("/"):
                    raise AVError("Record pointer must be an RFC 6901 JSON pointer")
                if re.search(r"~(?![01])",ref["record_pointer"]):
                    raise AVError("Invalid RFC 6901 pointer escape")
            if "authority" in ref:
                choice(ref["authority"], {"canonical_text", "best_available_text", "generated", "measurement", "observer_report", "human_declaration", "inference", "interpretation"}, "evidence authority")
            for key in ("origin_ids", "shared_assumptions", "context_evidence_ids"):
                strings(ref.get(key, []), key, 100)
            if "review_artifact" in ref:
                _artifact(ref["review_artifact"], "separate review")
            if "independence_declaration" in ref:
                declaration = ref["independence_declaration"]
                object_fields(declaration, {"reviewer", "basis", "upstream_evidence_ids"},
                              {"reviewer", "basis", "upstream_evidence_ids"}, "independent preparation declaration")
                text_value(declaration["reviewer"], "independence reviewer")
                text_value(declaration["basis"], "independence basis")
                strings(declaration["upstream_evidence_ids"], "declared upstream evidence", 100)
            if channel == "AO_STAGE2":
                identifier(ref.get("stage1_evidence_id"), "Stage 1 evidence ID")
                strings(ref.get("context_evidence_ids", []), "Stage 2 context references", 100, 1)
            evidence[eid] = dict(ref, channel=channel)
    if len(evidence) > 3000:
        raise AVError("Scene evidence inventory exceeds its bounded budget")
    for eid, ref in evidence.items():
        if set(ref.get("context_evidence_ids", []))-evidence.keys():
            raise AVError("Context references must resolve inside the scene packet")
        if ref["channel"] == "AO_STAGE2":
            parent = evidence.get(ref["stage1_evidence_id"])
            if not parent or parent["channel"] != "AO_STAGE1" or parent["locator"] != ref["locator"]:
                raise AVError("Stage 2 must retain the exact Stage 1 source/stream/interval locator")
    observations = array(packet["observations"], "observations", 1000)
    claims = array(packet["claims"], "claims", 1000)
    for kind, rows in (("observation", observations), ("claim", claims)):
        for row in rows:
            keys = {"id", "proposition", "statement", "status", "interval_seconds", "shared_assumptions"}
            keys |= {"evidence_ids", "value", "temporal_status", "evidence_roles"} if kind == "observation" else {"observation_ids", "inference", "interpretation", "alternatives", "confidence", "support_routes"}
            required_node = {"id", "proposition", "statement", "status", "evidence_ids" if kind == "observation" else "observation_ids"}
            object_fields(row, keys, required_node, kind)
            identifier(row["id"], kind+" ID")
            choice(row["proposition"], PROPOSITIONS, "proposition")
            choice(row["status"], STATES, "node status")
            text_value(row["statement"], kind+" statement")
            strings(row.get("shared_assumptions", []), "shared assumptions", 100)
            if "interval_seconds" in row:
                span(row["interval_seconds"], bounds)
            if kind == "observation":
                ids = strings(row["evidence_ids"], "observation evidence IDs", 100, 1)
                if set(ids)-evidence.keys():
                    raise AVError("Observation references missing evidence")
                if "evidence_roles" in row:
                    from .support_routes import ROLES
                    if not isinstance(row["evidence_roles"], dict) or set(row["evidence_roles"]) != set(ids):
                        raise AVError("Evidence roles must explicitly classify every attached reference")
                    for role in row["evidence_roles"].values():
                        choice(role, ROLES, "observation evidence role")
                if "temporal_status" in row:
                    choice(row["temporal_status"], TEMPORAL_STATES, "temporal status")
            else:
                strings(row["observation_ids"], "claim premises", 100, 1)
                for key in ("inference", "interpretation"):
                    if key in row:
                        text_value(row[key], key)
                strings(row.get("alternatives", []), "alternatives", 100)
                if "confidence" in row:
                    choice(row["confidence"], {"LOW", "MEDIUM", "HIGH", "OPEN"}, "claim confidence")
    nodes, _ = validate_graph(observations, claims, packet["dependencies"])
    for claim in claims:
        from .support_routes import validate_routes
        validate_routes(claim, nodes, packet["dependencies"])
        for oid in claim["observation_ids"]:
            if oid not in nodes or nodes[oid]["type"] != "observation":
                raise AVError("Claim premise must be an observation ID")
            if not any(e["from"] == oid and e["to"] == claim["id"] and e["relation"] in PROPAGATING for e in packet["dependencies"]):
                raise AVError("Each listed claim premise requires an explicit dependency edge")
            if "support_routes" not in claim and any(e["from"] == oid and e["to"] == claim["id"]
                    and e.get("role", "REQUIRED") != "REQUIRED" for e in packet["dependencies"]):
                raise AVError("Optional claim premises require declared support routes")
    conflict_ids = set()
    for row in array(packet["conflicts"], "conflicts", 100):
        object_fields(row, {"id", "proposition", "observation_ids", "question"}, {"id", "proposition", "observation_ids", "question"}, "conflict")
        cid = identifier(row["id"], "conflict ID")
        if cid in conflict_ids:
            raise AVError("Duplicate conflict ID")
        conflict_ids.add(cid)
        choice(row["proposition"], PROPOSITIONS, "disputed proposition")
        text_value(row["question"], "conflict question")
        for oid in strings(row["observation_ids"], "conflict observations", 100, 2):
            if oid not in nodes or nodes[oid]["type"] != "observation" or nodes[oid]["record"]["proposition"] != row["proposition"]:
                raise AVError("Conflict must compare existing observations of the same proposition")
    adjudication = packet["adjudication"]
    object_fields(adjudication, {"reviewer", "decisions"}, {"decisions"}, "adjudication")
    if adjudication["decisions"]:
        text_value(adjudication.get("reviewer"), "adjudicating reviewer")
    if not isinstance(adjudication["decisions"], dict):
        raise AVError("Adjudication decisions must map node IDs")
    for key, row in adjudication["decisions"].items():
        if key not in nodes:
            raise AVError("Adjudication references a missing node")
        object_fields(row, {"state", "reason"}, {"state", "reason"}, "adjudication decision")
        choice(row["state"], STATES, "adjudicated state")
        text_value(row["reason"], "adjudication reason")
    strings(packet["open_questions"], "open questions", 100)
    if "holistic_analysis" in packet:
        text_value(packet["holistic_analysis"], "integrated dramatic-event reading")
    identity = packet_identity(packet)
    if packet.get("packet_id", identity) != identity:
        raise AVError("Scene packet identity differs from its logical contents")
    return evidence, nodes


def load_scene_packet(path, *, relocations=None):
    path = Path(path).resolve()
    snapshot = file_record(path, "scene_packet_input")
    if relocations is not None:
        if not isinstance(relocations,dict) or len(relocations)>3000:
            raise AVError("Relocations must be a bounded digest-to-path mapping")
        for digest,candidate in relocations.items():
            digest_value(digest,"relocation digest")
            text_value(candidate,"relocated artifact path")
    packet = read_event_json(path)
    evidence, nodes = validate_packet(packet)
    bindings = {}
    for eid, ref in evidence.items():
        artifact = resolve_artifact(ref["artifact"], path.parent, relocations)
        bindings[eid] = {"path": str(artifact), "sha256": ref["artifact"]["sha256"], "channel": ref["channel"]}
        if ref.get("review_artifact"):
            review = resolve_artifact(ref["review_artifact"], path.parent, relocations)
            bindings[eid]["review_path"] = str(review)
            bindings[eid]["review_sha256"] = ref["review_artifact"]["sha256"]
        if ref.get("record_pointer"):
            current = read_event_json(artifact)
            try:
                for part in ref["record_pointer"][1:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(current,list):
                        if not re.fullmatch(r"0|[1-9][0-9]*",part):
                            raise AVError("RFC 6901 array index must be a nonnegative canonical integer")
                        current = current[int(part)]
                    else:
                        current = current[part]
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise AVError("Evidence record pointer does not resolve") from exc
    # Stage 2 is structurally attributed and immutably parent-bound; validity
    # is not capability qualification, independent corroboration, or truth.
    for eid, ref in evidence.items():
        if ref["channel"] == "MID":
            from .music_identity.workflow import load_music_run
            from .mapping import missing_intervals
            music_folder, music = load_music_run(Path(bindings[eid]["path"]).parent)
            src, loc = music["source"], ref["locator"]
            if (sha256(music_folder / "music-identity.json") != ref["artifact"]["sha256"] or src["sha256"] != loc["source_sha256"]
                or src["audio_stream"] != loc["stream_index"] or src["clock"] != loc["clock"]
                or missing_intervals(loc.get("intervals_seconds", []), [src["interval_seconds"]])):
                raise AVError("MID reference differs from its verified source/stream/clock/identity artifact")
        if ref["channel"] not in {"AO_STAGE1", "AO_STAGE2"}:
            continue
        raw = read_event_json(bindings[eid]["path"])
        expected_stage = 2 if ref["channel"] == "AO_STAGE2" else 1
        if not isinstance(raw, dict) or raw.get("schema") not in {"ave.auditory-observation.v1", "ave.auditory-comparison.v1"} or raw.get("stage", 1) != expected_stage:
            raise AVError("Auditory reference has an incompatible attributed record/stage")
        from .auditory_profiles import task_profile
        task_profile(raw.get("task_profile"))
        # Derived provenance tags come from actual immutable receipt fields,
        # rather than silently trusting a caller's independence assertion.
        if raw.get("backend_identity"):
            origins = list(ref.get("origin_ids", []))
            backend = raw["backend_identity"]
            provider = backend.get("provider", backend.get("backend_id"))
            origins.append("auditory-model-"+canonical_digest({
                "provider": provider, "model_revision": backend.get("model_revision")}))
            returned_model = (raw.get("provider_receipt") or {}).get("returned_model")
            if returned_model:
                origins.append("auditory-returned-model-"+canonical_digest({
                    "provider": provider, "returned_model": returned_model}))
            origins.append("auditory-model-context-"+canonical_digest({
                "backend": raw["backend_identity"],
                "clips": [(c.get("clip_sha256"), c.get("source_sha256"), c.get("stream_index"),
                           c.get("source_interval_seconds")) for c in (raw.get("clips") or [raw])],
                "context": raw.get("supplied_context")}))
            evidence[eid]["origin_ids"] = sorted(set(origins))
        clips = raw.get("clips") or [raw]
        array(clips,"ordered auditory clips",4,1)
        if any(not isinstance(c,dict) for c in clips):
            raise AVError("Each attributed auditory clip must be an object")
        for c in clips:
            digest_value(c.get("clip_sha256"),"auditory clip digest")
        expected_intervals = ref["locator"].get("intervals_seconds", [])
        if (any(c.get("source_sha256") != packet["source"]["sha256"] or c.get("stream_index") != ref["locator"]["stream_index"] for c in clips)
            or [c.get("source_interval_seconds") for c in clips] != expected_intervals):
            raise AVError("Auditory record source/stream/ordered intervals differ from the locator")
        if expected_stage == 2:
            parent_ref = evidence[ref["stage1_evidence_id"]]
            parent = read_event_json(bindings[ref["stage1_evidence_id"]]["path"])
            if not isinstance(parent,dict) or parent.get("validation_status") != "VALID":
                raise AVError("Contextual reinspection requires a valid immutable Stage 1 parent")
            parent_clips = parent.get("clips") or [parent]
            array(parent_clips,"ordered Stage 1 clips",4,1)
            if any(not isinstance(c,dict) for c in parent_clips):
                raise AVError("Stage 1 clips must be attributed objects")
            if (raw.get("stage1_parent", {}).get("observation_sha256") != parent_ref["artifact"]["sha256"]
                or [(c.get("clip_sha256"), c.get("source_sha256"), c.get("stream_index")) for c in clips]
                   != [(c.get("clip_sha256"), c.get("source_sha256"), c.get("stream_index")) for c in parent_clips]
                or raw.get("task_profile", "SPEECH_PERFORMANCE") != parent.get("task_profile", "SPEECH_PERFORMANCE")):
                raise AVError("Contextual reinspection differs from its immutable Stage 1 audio/parent/task")
            supplied = raw.get("supplied_context")
            if not isinstance(supplied, dict):
                raise AVError("Stage 2 supplied context must be an object")
            context_ids = strings(supplied.get("context_evidence_ids", []),"supplied context evidence IDs",100,1)
            if set(context_ids) != set(ref["context_evidence_ids"]):
                raise AVError("Stage 2 supplied context must explicitly bind its scene evidence IDs")
    source_replay = "NOT_REPLAYED"
    if packet["source"].get("path"):
        source_path = Path(packet["source"]["path"])
        source_path = source_path if source_path.is_absolute() else path.parent/source_path
        if source_path.exists():
            resolve_artifact({"path": str(source_path), "sha256": packet["source"]["sha256"]}, path.parent)
            source_replay = "SOURCE_BYTES_VERIFIED_NOT_PERCEPTUALLY_INSPECTED"
    verify_scene_snapshot(snapshot,bindings)
    return packet, evidence, nodes, bindings, snapshot, source_replay


def verify_scene_snapshot(snapshot,bindings):
    if sha256(snapshot["path"]) != snapshot["sha256"]:
        raise AVError("Scene input changed after admission")
    for row in bindings.values():
        if sha256(row["path"]) != row["sha256"]:
            raise AVError("Scene evidence changed after admission")
        if row.get("review_path") and sha256(row["review_path"]) != row["review_sha256"]:
            raise AVError("Scene review changed after admission")
        if row.get("qualification_proof"):
            from .inventory import verify_run
            proof = row["qualification_proof"]
            if sha256(Path(proof["path"])/"run.json") != proof["manifest_sha256"]:
                raise AVError("Scoped qualification proof changed after admission")
            verify_run(proof["path"])


def prepare_scene_packet(input, output, *, relocations=None):
    packet, _, _, bindings, snapshot, replay = load_scene_packet(input, relocations=relocations)
    deps = [snapshot]+[file_record(row["path"], "scene_evidence") for row in bindings.values()]
    deps += [file_record(row["review_path"], "separate_scene_review") for row in bindings.values() if "review_path" in row]
    inputs = [input]+[r["path"] for r in bindings.values()]+[r["review_path"] for r in bindings.values() if "review_path" in r]
    with output_transaction(output, inputs) as stage:
        # Freeze original input unchanged; the usable prepared copy explicitly
        # materializes resolved physical paths without changing logical identity.
        payload = Path(input).read_bytes()
        if hashlib.sha256(payload).hexdigest() != snapshot["sha256"]:
            raise AVError("Original scene input changed before it could be frozen")
        (stage/"input-packet.json").write_bytes(payload)
        prepared = copy.deepcopy(packet)
        if prepared["source"].get("path"):
            source_path = Path(prepared["source"]["path"])
            prepared["source"]["path"] = str((source_path if source_path.is_absolute() else Path(input).resolve().parent/source_path).resolve())
        for rows in prepared["evidence"].values():
            for ref in rows:
                ref["artifact"]["path"] = bindings[ref["id"]]["path"]
                if "review_artifact" in ref:
                    ref["review_artifact"]["path"] = bindings[ref["id"]]["review_path"]
        write_json(stage/"scene-packet.json", dict(prepared, packet_id=packet_identity(packet)))
        write_json(stage/"evidence-bindings.json", {"input_base": str(Path(input).resolve().parent), "bindings": bindings,
                    "source_replay": replay, "hashes_establish": "Byte identity, not authenticity, listening, viewing or truth"})
        (stage/"START_HERE.txt").write_text("Interpret this bounded dramatic event jointly. Use the supplied analytical method.\n"
            "Raw evidence channels stay separately attributable. Generated evidence is not reviewed evidence.\n"
            "Reconcile exact propositions by competence and authority; audit dependencies and contextual contamination.\n"
            "Record adjudication and remaining OPEN questions, then compare claims to a frozen baseline.\n", encoding="utf-8")
        verify_scene_snapshot(snapshot,bindings)
        return finish_run(stage, "scene-prepare", deps, metadata={"result_file": "scene-packet.json", "packet_id": packet_identity(packet),
            "reasoning_status": "REQUIRES_REASONING_ANALYST", "evidence_count": len(bindings), "source_replay": replay})
