"""Auditory evidence admission and explicit before/after claim accounting."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, write_json
from .inventory import text_value, verify_run
from .mapping import missing_intervals
from .observer_requests import source_observations, validate_comparison_response


def validate_model_observation(path):
    """Validate both raw JSON and its projection; return actually observed spans."""
    from .auditory_observer import validate_response
    from .analysis_preflight import capability_receipt
    path = Path(path).resolve()
    obs = read_json(path)
    if (not isinstance(obs, dict) or obs.get("schema") != "ave.auditory-observation.v1"
        or obs.get("validation_status") != "VALID" or obs.get("capability_status") != "PROBE_PASSED"
        or obs.get("observer_type") != "model_audio" or obs.get("backend_identity", {}).get("execution_mode") == "TEST_DOUBLE"):
        raise AVError("Observation is not a validated perceptual model witness")
    span = obs.get("source_interval_seconds")
    if not isinstance(span, list) or len(span) != 2:
        raise AVError("Observation has no native source interval")
    duration = obs.get("duration_seconds", span[1]-span[0])
    parsed = validate_response(json.dumps(obs.get("parsed"), allow_nan=False), duration)
    if not parsed["observations"]:
        raise AVError("An all-abstention response provides no affirmative auditory observation")
    proof_files = []
    projection = obs.get("comparison_projection")
    if projection:
        if not isinstance(projection, dict) or projection.get("parent_file") != "observation.json":
            raise AVError("Invalid comparison projection")
        parent_path = path.parent/"observation.json"
        if sha256(parent_path) != projection.get("parent_sha256"):
            raise AVError("Comparison parent bytes changed")
        parent = read_json(parent_path)
        if (parent.get("schema") != "ave.auditory-comparison.v1" or parent.get("validation_status") != "VALID"
            or parent.get("backend_identity") != obs.get("backend_identity") or parent.get("observer_type") != "model_audio"
            or parent.get("capability_status") != "PROBE_PASSED" or parent.get("raw_response") != obs.get("raw_response")):
            raise AVError("Comparison projection has no valid matching parent")
        raw_parsed = validate_comparison_response(parent["raw_response"], parent["clips"])
        if raw_parsed != parent.get("parsed"):
            raise AVError("Comparison parsed response differs from retained raw JSON")
        matching = [r["observation"] for r in raw_parsed["clips"] if r["label"] == projection.get("label")]
        source_clip = [c for c in parent["clips"] if c["label"] == projection.get("label")]
        if matching != [parsed] or len(source_clip) != 1 or any(source_clip[0].get(k) != obs.get(k) for k in
                ("clip_sha256", "source_sha256", "stream_index", "source_interval_seconds", "duration_seconds")):
            raise AVError("Atomic observation differs from its independently timed comparison clip")
        proof_files.append(file_record(parent_path, "auditory_comparison_proof"))
    elif not isinstance(obs.get("raw_response"), str) or validate_response(obs["raw_response"], duration) != parsed:
        raise AVError("Parsed auditory observation differs from retained raw JSON")
    backend_identity = obs.get("backend_identity", {})
    if backend_identity.get("route_type") == "hosted":
        # Reuse exactly the admission checks applied before submission. The
        # embedded receipt travels with the witness and is not a fresh probe.
        receipt = obs.get("capability_receipt")
        if not isinstance(receipt, dict) or capability_receipt(receipt).get("backend_identity") != backend_identity:
            raise AVError("Hosted witness has no matching capability provenance")
        from .observer_execution import validate_hosted_capability
        validate_hosted_capability(receipt, backend_identity)
        provider = obs.get("provider_receipt")
        if (not isinstance(provider, dict) or provider.get("execution_mode") != "HOSTED_LIVE"
            or provider.get("status") != "REQUEST_ACCEPTED"
            or provider.get("authorization") != {"provider": "openai", "remote_media_authorized": True}
            or not any(c.get("clip_sha256") == obs.get("clip_sha256") and c.get("source_sha256") == obs.get("source_sha256")
                       and c.get("stream_index") == obs.get("stream_index") and c.get("duration_seconds") == duration
                       for c in provider.get("submitted_audio", []))):
            raise AVError("Hosted witness was not bound to an authorized successful audio request")
        if provider.get("returned_model") != receipt.get("returned_model"):
            raise AVError("Hosted model identity changed after its capability probe")
    mapping = obs.get("clip_to_source_mapping")
    if mapping:
        if (mapping.get("parent_source_sha256") != obs.get("source_sha256")
            or mapping.get("parent_stream_index") != obs.get("stream_index")
            or mapping.get("artifact_sha256") != obs.get("clip_sha256") or len(mapping.get("segments", [])) != 1):
            raise AVError("Auditory source map disagrees with its observation")
        seg = mapping["segments"][0]
        if seg["derivative_start_seconds"] != 0 or abs(seg["derivative_end_seconds"]-duration) > 1e-8:
            raise AVError("Auditory source map does not cover the independently timed clip")
        mapped = source_observations(parsed, {"review_mapping": mapping, "source_sha256": obs["source_sha256"], "stream_index": obs["stream_index"]})
        if obs.get("source_observations") != mapped:
            raise AVError("Recorded source observations disagree with their native clock projection")
    elif backend_identity.get("route_type") == "hosted":
        raise AVError("Hosted auditory witness requires a native source-clock map")
    else:
        mapped = [{"source_start_s": span[0]+r["start_s"], "source_end_s": span[0]+r["end_s"]} for r in parsed["observations"]]
    return obs, [[r["source_start_s"], r["source_end_s"]] for r in mapped], proof_files


def _overlap(covered, locator):
    return any(max(a,c) < min(b,d) for a,b in covered for c,d in locator["intervals_seconds"])


def claim_delta(claims_file, observation_runs, output, *, decisions=None, claim_ids=None, prior_readings=None):
    from .evidence_claims import audit_claims, validate_claims
    config = Path(claims_file).resolve()
    data = read_json(config)
    before, _, deps = validate_claims(data, config.parent)
    deps.append(file_record(config, "frozen_prior_claims"))
    rows = {r["id"]: r for r in before}
    selected = set(claim_ids) if claim_ids else set(rows)
    if selected-set(rows):
        raise AVError("Claim-delta selection references an absent claim")
    # Rebase existing evidence into the new standalone config before any edits.
    updated = copy.deepcopy(data)
    for e in updated.get("evidence", []):
        p = Path(e["artifact"]["path"])
        e["artifact"]["path"] = str(p.resolve() if p.is_absolute() else (config.parent/p).resolve())
    decisions_by_id = {}
    if decisions:
        value = read_json(decisions)
        if not isinstance(value, dict) or set(value) != {"decisions"} or not isinstance(value["decisions"], list):
            raise AVError("Require an explicit coordinator decisions array")
        for decision in value["decisions"]:
            allowed = {"claim_id", "disposition", "reviewer", "reason", "deterministic_agreement", "analytical_consequence", "adjudication"}
            if not isinstance(decision, dict) or set(decision)-allowed or decision.get("claim_id") not in selected or decision["claim_id"] in decisions_by_id:
                raise AVError("Invalid or duplicate coordinator claim decision")
            if decision.get("disposition") not in {"SUPPORT", "CONSTRAIN", "CONTRADICT", "FAIL_TO_DISCRIMINATE"}:
                raise AVError("Unknown auditory claim disposition")
            for name in ("reviewer", "reason", "analytical_consequence"):
                text_value(decision.get(name), name)
            if decision.get("deterministic_agreement") not in {"AGREES", "DISAGREES", "NOT_COMPARABLE", "NOT_REVIEWED"}:
                raise AVError("Coordinator must separately record agreement with deterministic evidence")
            decisions_by_id[decision["claim_id"]] = decision
        deps.append(file_record(decisions, "coordinator_decisions"))
    folders = [Path(p).resolve() for p in observation_runs]
    readings = [Path(p).resolve() for p in (prior_readings or [])]
    inputs = [config]+folders+readings+([decisions] if decisions else [])
    affected, diagnostics = {}, {}
    with output_transaction(output, inputs) as stage:
        shutil.copyfile(config, stage/"before-claims.json")
        frozen = []
        for i, path in enumerate(readings):
            if path.stat().st_size > 8*1024*1024:
                raise AVError("Prior reading freeze is bounded to 8 MiB per file")
            name = "prior-reading-%02d" % i + path.suffix
            shutil.copyfile(path, stage/name)
            record = file_record(path, "frozen_prior_reading"); deps.append(record)
            frozen.append({"artifact": name, "sha256": record["sha256"]})
        new_evidence = []
        for folder in folders:
            verify_run(folder)
            manifest = read_json(folder/"run.json")
            if manifest.get("operation") != "observer-observe":
                raise AVError("Claim delta requires a completed observation run")
            names = manifest.get("metadata", {}).get("atomic_observation_files", ["observation.json"])
            if not isinstance(names, list) or len(names) > 4:
                raise AVError("Unbounded atomic observation list")
            deps.append(file_record(folder/"run.json", "auditory_observation_run"))
            # Keep comparison parents beside their child projections. Relative
            # paths in the revised claim config survive moving this delta run.
            portable_relative = "auditory-witnesses/" + sha256(folder/"run.json")[:24]
            portable_folder = stage/portable_relative
            if not portable_folder.exists():
                portable_folder.mkdir(parents=True)
                shutil.copyfile(folder/"run.json", portable_folder/"run.json")
                for artifact in manifest["artifacts"]:
                    from .common import safe_member
                    original = safe_member(folder, artifact["path"])
                    destination = safe_member(portable_folder, artifact["path"])
                    if original.stat().st_size > 8*1024*1024:
                        raise AVError("Portable auditory receipt exceeds the record-only 8 MiB file budget")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, destination)
            root_path = folder/"observation.json"
            root_obs = read_json(root_path)
            root_clips = root_obs.get("clips") or [root_obs]
            def record_diagnostic(reason, clips):
                for cid in selected:
                    if any(c.get("source_sha256") == loc["source_sha256"]
                           and c.get("stream_index") == loc.get("stream_index", c.get("stream_index"))
                           and _overlap([c["source_interval_seconds"]], loc)
                           for c in clips for loc in rows[cid]["locators"]):
                        affected.setdefault(cid, [])
                        diagnostics.setdefault(cid, []).append({"artifact": str(root_path), "sha256": sha256(root_path),
                            "execution_status": root_obs.get("execution_status"), "reason": reason, "admitted_as_auditory_evidence": False})
            if not names:
                record_diagnostic("No valid atomic observation was returned", root_clips)
                deps.append(file_record(root_path, "auditory_failed_response"))
            for name in names:
                if name not in {"observation.json", "clip-A-observation.json", "clip-B-observation.json", "clip-C-observation.json", "clip-D-observation.json"}:
                    raise AVError("Unsafe auditory observation artifact name")
                path = folder/name
                try:
                    obs, covered, proofs = validate_model_observation(path)
                except (AVError, ValueError, TypeError, KeyError) as exc:
                    candidate = read_json(path)
                    record_diagnostic(str(exc), [candidate])
                    deps.append(file_record(path, "auditory_unadmitted_response"))
                    continue
                record = file_record(path, "auditory_observation"); deps.append(record); deps += proofs
                eid = "AO-" + record["sha256"][:24]
                applicable = []
                for cid in selected:
                    locs = [l for l in rows[cid]["locators"] if l["source_sha256"] == obs["source_sha256"]
                            and l.get("stream_index", obs.get("stream_index")) == obs.get("stream_index")]
                    # A broad claim may request a longer interval, but an
                    # observation cannot be extended beyond its timed coverage.
                    if locs and any(_overlap(covered, l) for l in locs):
                        applicable.append(cid)
                if not applicable:
                    continue
                evidence = {"id": eid, "layer": "auditory_observation", "modality": "audio", "observer_type": "model_audio",
                    "locator": {"source_sha256": obs["source_sha256"], "clock": "original_pts_minus_source_origin",
                                "stream_index": obs["stream_index"], "intervals_seconds": covered},
                    "statement": " | ".join(r["description"] for r in obs["parsed"]["observations"]),
                    "reviewer": obs["backend_identity"].get("provider", "model") + "/" + obs["backend_identity"]["model_revision"],
                    "inspection_status": "REVIEW_DECLARED", "quality_flags": [], "artifact": {"path": portable_relative+"/"+name, "sha256": record["sha256"]},
                    "scope": "Attributed model witness; disposition and interpretive significance require separate adjudication"}
                if eid not in {e["id"] for e in updated.get("evidence", [])+new_evidence}:
                    new_evidence.append(evidence)
                for cid in applicable:
                    affected.setdefault(cid, []).append(eid)
        if not affected:
            raise AVError("No source-bound observer result overlaps the selected claims; no claim delta fabricated")
        if set(decisions_by_id)-set(affected):
            raise AVError("Coordinator decision has no source-matched auditory witness")
        updated.setdefault("evidence", []).extend(new_evidence)
        for c in updated["claims"]:
            cid = c["id"]
            if cid not in affected:
                continue
            c["pending_auditory_evidence_ids"] = sorted(set(c.get("pending_auditory_evidence_ids", [])+affected[cid]))
            decision = decisions_by_id.get(cid)
            # The default attaches an available witness without inventing its
            # relationship to the claim or silently promoting the verdict.
            if not decision:
                c["adjudication"] = c.get("adjudication") or {"status": rows[cid]["status"], "reviewer": "Toolkit witness attachment",
                    "reason": "Prior verdict retained; new auditory witness awaits coordinator adjudication"}
                continue
            disposition = decision["disposition"]
            if disposition in {"SUPPORT", "CONTRADICT"}:
                if not affected[cid]:
                    raise AVError("Support or contradiction requires an affirmative admitted auditory witness, not a failure or abstention")
                target = "evidence_ids" if disposition == "SUPPORT" else "counterevidence_ids"
                other = "counterevidence_ids" if target == "evidence_ids" else "evidence_ids"
                c[target] = sorted(set(c.get(target, [])+affected[cid]))
                c[other] = [eid for eid in c.get(other, []) if eid not in affected[cid]]
                if disposition == "SUPPORT":
                    c["quality_flags"] = [f for f in c.get("quality_flags", []) if f not in {"AUDITORY_OBSERVER_UNAVAILABLE", "UNVALIDATED_AUDITORY_ROUTE"}]
            adjudication = decision.get("adjudication")
            if adjudication:
                if not isinstance(adjudication, dict) or set(adjudication) != {"status", "reviewer", "reason"}:
                    raise AVError("Explicit adjudication needs status, reviewer and reason")
                c["adjudication"] = adjudication
            else:
                status = "OPEN" if disposition in {"CONSTRAIN", "CONTRADICT"} else rows[cid]["status"]
                c["adjudication"] = {"status": status, "reviewer": decision["reviewer"], "reason": decision["reason"]}
        after, _, _ = validate_claims(updated, stage)
        final_rows = {c["id"]: c for c in after}
        delta = []
        for cid, ids in sorted(affected.items()):
            old, new = rows[cid], final_rows[cid]
            decision = decisions_by_id.get(cid, {})
            delta.append({"claim_id": cid, "claim": old["statement"], "before_status": old["status"],
                "before_reason": (old.get("adjudication") or {}).get("reason"), "before_evidence_ids": old.get("evidence_ids", []),
                "new_auditory_evidence_ids": sorted(set(ids)), "unadmitted_results": diagnostics.get(cid, []),
                "disposition": decision.get("disposition", "AWAITING_ADJUDICATION" if ids else "FAIL_TO_DISCRIMINATE"),
                "deterministic_agreement": decision.get("deterministic_agreement", "NOT_REVIEWED"),
                "analytical_consequence": decision.get("analytical_consequence", "A source-bound auditory witness is available; its consequence has not been adjudicated"),
                "after_status": new["status"], "remaining_uncertainty": {"quality_flags": new["quality_flags"],
                    "blocking_flags": new["blocking_flags"], "missing_modalities": new["missing_modalities"],
                    "alternatives": new["alternatives"], "observer_confidence": "Uncalibrated reviewer judgment"},
                "coordinator_decision": decision or None})
        write_json(stage/"claims-with-auditory-witnesses.json", updated)
        audit_claims(stage/"claims-with-auditory-witnesses.json", stage/"claim-audit")
        report = {"schema": "ave.auditory-claim-delta.v1", "prior_claims_sha256": sha256(config), "frozen_prior_readings": frozen,
                  "claims": delta, "automatic_verdict_promotion": False, "scope": "Claim consequences are explicit coordinator decisions, not model-response side effects"}
        write_json(stage/"AUDITORY_CLAIM_DELTA.json", report)
        lines = ["# Auditory claim delta", "", "Prior claims and optional readings are frozen; a returned answer does not by itself establish a claim.", ""]
        for row in delta:
            lines += ["## " + row["claim_id"], "", row["claim"], "",
                "Before: " + row["before_status"] + ". After: " + row["after_status"] + ".",
                "New auditory evidence: " + ", ".join(row["new_auditory_evidence_ids"]) + ".",
                "Disposition: " + row["disposition"] + ". Deterministic agreement: " + row["deterministic_agreement"] + ".",
                row["analytical_consequence"], "", "Remaining uncertainty: " + json.dumps(row["remaining_uncertainty"], ensure_ascii=False), ""]
        (stage/"AUDITORY_CLAIM_DELTA.md").write_text("\n".join(lines), encoding="utf-8")
        return finish_run(stage, "observer-claim-delta", deps, metadata={"result_file": "AUDITORY_CLAIM_DELTA.json",
            "view_file": "AUDITORY_CLAIM_DELTA.md", "affected_claims": len(delta), "automatic_verdict_promotion": False})
