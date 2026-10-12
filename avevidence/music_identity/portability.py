"""Explicit portable projection and verified logical-source rebinding."""
from __future__ import annotations

import copy
from pathlib import Path
import re

from ..common import AVError, file_record, finish_run, output_transaction, probe_source, select_stream, sha256, write_json
from ..event_contracts import canonical_digest, read_event_json, span
from .contracts import source_scope, validate_result
from .workflow import load_music_run

# A public projection fails closed instead of guessing which private strings to redact.
PRIVATE = re.compile(r"(?:[A-Za-z]:[\\/]|(?:^|[\s\"'])/(?!/)\S+|\\\\|data:(?:audio|video|image)|audio_base64|input_audio|(?:sk-|sess-)[A-Za-z0-9_-]{12,})", re.I)


def assert_portable(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if any(s in key.casefold() for s in ("api_key", "credential", "authorization_header", "base64", "input_audio", "audio_data")):
                raise AVError("Portable music artifact contains a prohibited private field")
            assert_portable(item)
    elif isinstance(value, list):
        for item in value:
            assert_portable(item)
    elif isinstance(value, str):
        if PRIVATE.search(value) or len(value) > 8192:
            raise AVError("Portable music artifact contains a private path, media payload or secret-like value")
    return value


def portable_projection(raw):
    result = copy.deepcopy(raw)
    result["privacy"] = {"default": raw["privacy"]["default"], "portable_source": True,
        "external_lookup_authorization_separate_from_media_upload": True}
    def aliases(value):
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                return {"alias": "artifact-" + value["sha256"][:24], "sha256": value["sha256"]}
            return {k: aliases(v) for k, v in value.items()}
        if isinstance(value, list):
            return [aliases(v) for v in value]
        return value
    result = aliases(result)
    assert_portable(result)
    validate_result(result)
    return result


def export_portable(input, output):
    folder, raw = load_music_run(input)
    public = portable_projection(raw)
    with output_transaction(output, [folder]) as stage:
        write_json(stage / "music-identity.json", public)
        alias_record = {"schema": "ave.music-identity.aliases.v1", "source": public["source"],
            "private_source_path": "OMITTED", "original_identity_sha256": sha256(folder / "music-identity.json"),
            "portable_identity_sha256": sha256(stage / "music-identity.json"), "portable_content_id": canonical_digest(public),
            "replay_status": "SOURCE_REBINDING_REQUIRED", "evidence_artifacts": [e["artifact"] for e in public["evidence"]],
            "scope": "Portable metadata and attribution only. Source media and raw private evidence are not included."}
        assert_portable(alias_record)
        write_json(stage / "aliases.json", alias_record)
        # A special portable receipt deliberately omits generic run/environment/command paths.
        receipt = {"schema": "ave.music-identity.export.v1", "operation": "music-export", "source": public["source"],
            "artifacts": [{"path": name, "sha256": sha256(stage / name), "bytes": (stage / name).stat().st_size}
                for name in ("music-identity.json", "aliases.json")], "network_activity": {"external_requests": 0, "media_uploads": 0, "paid_calls": 0}}
        write_json(stage / "portable-receipt.json", receipt)
    return receipt


def verify_portable(folder):
    folder = Path(folder).resolve()
    receipt = read_event_json(folder / "portable-receipt.json")
    if receipt.get("schema") != "ave.music-identity.export.v1":
        raise AVError("Unsupported portable music receipt")
    assert_portable(receipt)
    if {r["path"] for r in receipt.get("artifacts", [])} != {"music-identity.json", "aliases.json"}:
        raise AVError("Portable receipt membership differs")
    for r in receipt["artifacts"]:
        path = folder / r["path"]
        if not path.is_file() or path.is_symlink() or path.stat().st_size != r["bytes"] or sha256(path) != r["sha256"]:
            raise AVError("Portable music artifact changed")
    result, aliases = read_event_json(folder / "music-identity.json"), read_event_json(folder / "aliases.json")
    assert_portable(result)
    validate_result(result)
    if aliases.get("source") != result["source"] or aliases.get("portable_identity_sha256") != sha256(folder / "music-identity.json") or aliases.get("portable_content_id") != canonical_digest(result):
        raise AVError("Portable source/identity alias differs")
    return result, aliases


def rebind(input, source, output, *, evidence_bindings=None):
    folder = Path(input).resolve()
    result, aliases = verify_portable(folder)
    admitted = probe_source(source)
    if admitted["sha256"] != result["source"]["sha256"]:
        raise AVError("Rebound source bytes differ from the portable hash")
    select_stream(admitted, "audio", result["source"]["audio_stream"])
    span(result["source"]["interval_seconds"], [0., admitted["duration_seconds"]])
    bindings = read_event_json(evidence_bindings) if evidence_bindings else {}
    if not isinstance(bindings, dict):
        raise AVError("Evidence bindings must map logical artifact aliases to local paths")
    dependencies = [admitted, file_record(folder / "music-identity.json")]
    with output_transaction(output, [folder, source]) as stage:
        result = copy.deepcopy(result)
        execution = {"source_path": admitted["path"], "source": result["source"], "rebound_from": aliases["portable_identity_sha256"]}
        for e in result["evidence"]:
            artifact = e["artifact"]
            path = Path(bindings.get(artifact["alias"], ""))
            if not path.is_file() or path.is_symlink() or sha256(path) != artifact["sha256"]:
                raise AVError("Adjudication replay requires explicit hash-verified evidence-artifact rebinding")
            import shutil
            name = "evidence/" + artifact["alias"] + path.suffix.lower()
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            e["artifact"] = {"path": name, "sha256": artifact["sha256"]}
            dependencies.append(file_record(path))
        result["parent"] = {"schema": "ave.music-identity.export.v1", "sha256": aliases["portable_identity_sha256"], "relationship": "HASH_VERIFIED_REBINDING"}
        write_json(stage / "execution" / "manifest.json", execution)
        write_json(stage / "music-identity.json", result)
        return finish_run(stage, "music-rebind", dependencies, metadata={"result_file": "music-identity.json", "source_bytes_verified": True})


def plan(input, output, *, padding_seconds=5., available_start=0., available_end=None):
    folder, result = load_music_run(input)
    from ..inventory import number
    padding = number(padding_seconds, "plan padding")
    if padding > 30:
        raise AVError("Plan padding is bounded to 30 seconds")
    a, b = result["source"]["interval_seconds"]
    # Without a verified full source binding, do not invent availability beyond the admitted interval.
    manifest = folder / "execution" / "manifest.json"
    duration = b
    if manifest.is_file():
        private = read_event_json(manifest)
        if private.get("source_path") and Path(private["source_path"]).is_file():
            probe = probe_source(private["source_path"])
            if probe["sha256"] != result["source"]["sha256"]:
                raise AVError("Music query planner source bytes changed")
            duration = probe["duration_seconds"]
    end_bound = duration if available_end is None else number(available_end, "available end")
    start_bound = number(available_start, "available start")
    span([start_bound, end_bound], [0., duration])
    if a < start_bound or b > end_bound:
        raise AVError("Available planning bounds must contain the original query")
    proposed = [[max(start_bound, a - padding), min(end_bound, b + padding)]]
    proposed += [[max(start_bound, a - padding), a], [b, min(end_bound, b + padding)]]
    requests = [{"source": {**result["source"], "interval_seconds": bounds}, "status": "PLANNED_NOT_SUBMITTED",
                 "authorization_required": True, "selection_basis": "Longer or neighboring candidate; cleanliness requires inspection"}
                for bounds in proposed if bounds[1] > bounds[0] and bounds[1] - bounds[0] <= 120]
    report = {"schema": "ave.music-identity.plan.v1", "original_source": result["source"], "requests": requests,
        "comparison_policy": "Compare several authorized intervals; source separation is not automatic",
        "future_separated_witness_requirements": ["parent_source_hash", "selected_stream", "source_interval", "tool_and_version", "parameters", "derived_hash", "artifact_inspection", "mixed_soundtrack_primary"],
        "api_submission_authorized": False, "network_requests": 0}
    with output_transaction(output, [folder]) as stage:
        write_json(stage / "music-plan.json", report)
        return finish_run(stage, "music-plan", [file_record(folder / "music-identity.json")], metadata={"result_file": "music-plan.json"})
