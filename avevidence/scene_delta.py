"""Frozen before/after claim changes, with explicit analyst dispositions."""
from __future__ import annotations

from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, sha256, write_json
from .cross_modal import reconcile_data
from .event_contracts import choice, object_fields, read_event_json, strings
from .inventory import text_value
from .scene_packets import (load_scene_packet, packet_identity,
                            retain_scene_qualifications, scene_qualification_inputs, verify_scene_snapshot)
from .observation_dependencies import PROPAGATING
from .mapping import missing_intervals, union_intervals

DISPOSITIONS = {"PRESERVE", "STRENGTHEN", "REVISE", "DOWNGRADE", "REJECT", "OPEN"}


def _premise_state(packet, claim_id):
    ancestors, todo = {claim_id}, [claim_id]
    while todo:
        child = todo.pop()
        for edge in packet["dependencies"]:
            if edge["to"] == child and edge["relation"] in PROPAGATING and edge["from"] not in ancestors:
                ancestors.add(edge["from"])
                todo.append(edge["from"])
    nodes = [row for kind in ("observations","claims") for row in packet[kind] if row["id"] in ancestors]
    refs = {eid for row in nodes for eid in row.get("evidence_ids", [])}
    import copy
    records = {}
    for rows in packet["evidence"].values():
        for ref in rows:
            if ref["id"] in refs:
                records[ref["id"]] = copy.deepcopy(ref)
                records[ref["id"]]["artifact"].pop("path",None)
                if records[ref["id"]].get("review_artifact"):
                    records[ref["id"]]["review_artifact"].pop("path",None)
    return {"edges": [e for e in packet["dependencies"] if e["to"] in ancestors], "nodes":nodes,"evidence":records,
            "decisions": {k:v for k,v in packet["adjudication"]["decisions"].items() if k in ancestors}}


def _witness_signature(ref, interval=None):
    """Compare semantic coverage; aliases or irrelevant extent add no evidence."""
    locator = dict(ref["locator"])
    intervals = locator.get("intervals_seconds", [])
    points = locator.get("points_seconds", [])
    if interval is not None:
        lo, hi = interval
        intervals = [[max(a, lo), min(b, hi)] for a, b in intervals
                     if max(a, lo) < min(b, hi)]
        points = [point for point in points if lo <= point < hi]
    locator["intervals_seconds"] = union_intervals(intervals)
    locator["points_seconds"] = sorted(set(points))
    return {"artifact": ref["artifact"]["sha256"], "review": ref.get("review_artifact", {}).get("sha256"),
            "locator": locator, "record_pointer": ref.get("record_pointer")}


def _adequate_new_evidence(packet, reconciliation, prior_premises, premise_state, claim, changed_refs):
    """Changed material must actually support a relevant, source-bound premise."""
    eligible = set()
    direct = claim["proposition"] not in {"emotion", "interpretation"}
    claim_interval = claim.get("interval_seconds", packet["interval_seconds"])
    evidence = premise_state["evidence"]
    for node in premise_state["nodes"]:
        oid = node["id"]
        if "evidence_ids" not in node or reconciliation["dependency_assessment"]["state_overlay"][oid]["after"] not in {"SUPPORTED", "CONFIRMED"}:
            continue
        if direct and (node["proposition"] != claim["proposition"] or missing_intervals(
            [claim_interval], [node.get("interval_seconds", packet["interval_seconds"])])):
            continue
        relevant_interval = claim_interval if direct else node.get("interval_seconds", packet["interval_seconds"])
        prior_signatures = [_witness_signature(ref, relevant_interval)
                            for ref in prior_premises["evidence"].values()]
        for assessment in reconciliation["observations"][oid]["evidence_assessments"]:
            eid = assessment["evidence_id"]
            if eid not in changed_refs or not assessment["adequate_for_support"]:
                continue
            if _witness_signature(evidence[eid], relevant_interval) in prior_signatures:
                continue
            if direct and assessment["channel"] == "VIS" and not any(
                claim_interval[0] <= point < claim_interval[1]
                for point in evidence[eid]["locator"].get("points_seconds", [])):
                continue
            eligible.add(eid)
    return eligible


def scene_claim_delta(before, after, output, *, decisions=None, relocations=None):
    baseline = load_scene_packet(before, relocations=relocations)
    completed = load_scene_packet(after, relocations=relocations)
    a, b = baseline[0], completed[0]
    if (a["scene_id"] != b["scene_id"] or a["source"]["sha256"] != b["source"]["sha256"]
        or any(a["source"].get(k) != b["source"].get(k) for k in ("audio_stream","video_stream"))
        or a["interval_seconds"] != b["interval_seconds"]):
        raise AVError("Claim delta must compare the same scene/source/interval")
    supplied = read_event_json(decisions) if decisions else {"reviewer": "automatic-change-detector", "decisions": {}}
    object_fields(supplied, {"reviewer", "decisions"}, {"reviewer", "decisions"}, "delta decisions")
    text_value(supplied["reviewer"], "delta reviewer")
    if not isinstance(supplied["decisions"], dict):
        raise AVError("Delta decisions must map baseline claim IDs")
    old = {c["id"]: c for c in a["claims"]}
    new = {c["id"]: c for c in b["claims"]}
    if set(supplied["decisions"])-old.keys():
        raise AVError("Delta decision names a missing baseline claim")
    ar = reconcile_data(*baseline[:4])
    br = reconcile_data(*completed[:4])
    old_assessed = {c["id"]: c for c in ar["claims"]}
    new_assessed = {c["id"]: c for c in br["claims"]}
    rows = []
    for cid, claim in old.items():
        current = new.get(cid)
        before_premises, after_premises = _premise_state(a,cid), _premise_state(b,cid)
        prior_refs = set(before_premises["evidence"])
        new_refs = set(after_premises["evidence"]) if current else set()
        changed_refs = [eid for eid in sorted(new_refs) if eid not in baseline[1]
            or completed[1][eid]["artifact"]["sha256"] != baseline[1][eid]["artifact"]["sha256"]
            or completed[1][eid].get("review_artifact", {}).get("sha256") != baseline[1][eid].get("review_artifact", {}).get("sha256")
            or completed[1][eid]["locator"] != baseline[1][eid]["locator"]
            or completed[1][eid].get("record_pointer") != baseline[1][eid].get("record_pointer")]
        changed = (current != claim or any(completed[2].get(oid) != baseline[2].get(oid) for oid in set(claim["observation_ids"]) | set(current["observation_ids"] if current else []))
            or bool(changed_refs) or old_assessed[cid] != new_assessed.get(cid)
            or before_premises != after_premises)
        decision = supplied["decisions"].get(cid)
        if decision:
            object_fields(decision, {"disposition", "reason", "what_survived", "what_changed", "remaining_open_questions"},
                          {"disposition", "reason", "what_survived", "what_changed", "remaining_open_questions"}, "claim delta decision")
            choice(decision["disposition"], DISPOSITIONS, "claim disposition")
            for key in ("reason", "what_survived", "what_changed"):
                text_value(decision[key], key)
            strings(decision["remaining_open_questions"], "remaining open questions", 100)
            disposition = decision["disposition"]
            if disposition == "PRESERVE" and (not current or current["statement"] != claim["statement"]):
                raise AVError("PRESERVE cannot hide a changed formulation")
            if disposition == "STRENGTHEN" and (not changed_refs or not current or new_assessed[cid]["state"] not in {"SUPPORTED", "CONFIRMED"}
                or not _adequate_new_evidence(b, br, before_premises, after_premises, current, changed_refs)):
                raise AVError("STRENGTHEN requires relevant new evidence and adequately adjudicated support")
            if disposition == "REVISE" and (not current or current["statement"] == claim["statement"]):
                raise AVError("REVISE requires a new claim formulation")
        else:
            disposition = "OPEN" if changed else "PRESERVE"
            decision = {"reason": "Changed evidence/premises require explicit analyst comparison" if changed else "Claim and relevant premises are unchanged",
                        "what_survived": "Original formulation retained as the frozen baseline",
                        "what_changed": "See changed evidence and reconciled premise states" if changed else "No relevant changes detected",
                        "remaining_open_questions": b["open_questions"] if changed else []}
        if disposition != "OPEN" and current and new_assessed[cid]["state"] in {"RECHECK", "OPEN"}:
            if disposition in {"STRENGTHEN", "PRESERVE"} and changed:
                raise AVError("Changed unreviewed/rechecked premises cannot preserve or strengthen a claim automatically")
        rows.append({"claim_id": cid, "before": claim, "after": current,
            "before_state": old_assessed[cid]["state"], "after_state": new_assessed.get(cid, {}).get("state", "OPEN"),
            "relevant_new_evidence": changed_refs, "retained_evidence": sorted(prior_refs & new_refs),
            "cross_modal_conflicts": [r for r in br["conflicts"] if set(r["observation_ids"]) & set(current["observation_ids"] if current else [])],
            "disposition": disposition, **decision, "new_formulation": current["statement"] if current else None,
            "confidence_change": {"before": claim.get("confidence", "OPEN"), "after": current.get("confidence", "OPEN") if current else "OPEN"}})
        rows[-1]["auditory_evidence_delta"] = [{"observation_id": oid, "statement": completed[2][oid]["record"]["statement"],
            "assessments": [assessment for assessment in br["observations"][oid]["evidence_assessments"]
                            if assessment["channel"] in {"AO_STAGE1", "AO_STAGE2", "HL"}],
            "qualification_states": [assessment.get("task_capability_status", "SCOPED_HUMAN")
                for assessment in br["observations"][oid]["evidence_assessments"] if assessment["channel"] in {"AO_STAGE1", "AO_STAGE2", "HL"}],
            "independence": br["observations"][oid]["independence"],
            "state": br["dependency_assessment"]["state_overlay"][oid]["after"]}
            for oid in (current["observation_ids"] if current else []) if any(
                assessment["channel"] in {"AO_STAGE1", "AO_STAGE2", "HL"} for assessment in br["observations"][oid]["evidence_assessments"])]
        rows[-1]["support_routes"] = new_assessed.get(cid, {}).get("support_routes", [])
        rows[-1]["dependency_changes"] = {key: value for key, value in br["dependency_assessment"]["state_overlay"].items()
            if key in {node["id"] for node in after_premises["nodes"]} and value["after"] != value["before"]}
    result = {"schema": "ave.scene-claim-delta.v1", "scene_id": a["scene_id"], "source_sha256": a["source"]["sha256"],
              "baseline_packet_id": packet_identity(a), "completed_packet_id": packet_identity(b),
              "baseline_input_sha256": baseline[4]["sha256"], "completed_input_sha256": completed[4]["sha256"],
              "reviewer": supplied["reviewer"], "claims": rows, "new_claim_ids": sorted(new.keys()-old.keys()),
              "raw_records_preserved": True, "automatic_confidence_promotion": False,
              "event_synthesis": br["event_synthesis"],
              "scope": "Explicit changes to accuracy/scope/mechanism/confidence; more descriptive material alone is not improvement"}
    inputs = [before, after]+([decisions] if decisions else [])+[r["path"] for data in (baseline, completed) for r in data[3].values()]
    proof_inputs, enclosing_sources = scene_qualification_inputs({"before": baseline[3], "after": completed[3]})
    with output_transaction(output, inputs+proof_inputs) as stage:
        for name, path, data in (("before", before, baseline), ("after", after, completed)):
            payload = Path(path).read_bytes()
            import hashlib
            if hashlib.sha256(payload).hexdigest() != data[4]["sha256"]:
                raise AVError("Frozen claim input changed after admission")
            (stage/(name+"-packet.json")).write_bytes(payload)
        write_json(stage/"claim-delta.json", result)
        write_json(stage/"before-reconciliation.json", ar)
        write_json(stage/"after-reconciliation.json", br)
        proof_sources = retain_scene_qualifications(stage, {"before": baseline[3], "after": completed[3]})
        proof_sources = list({r["path"]: r for r in proof_sources+enclosing_sources}.values())
        verify_scene_snapshot(baseline[4],baseline[3])
        verify_scene_snapshot(completed[4],completed[3], qualification_inputs=proof_inputs)
        return finish_run(stage, "scene-delta", [baseline[4], completed[4]]+([file_record(decisions)] if decisions else [])+proof_sources,
                          metadata={"result_file": "claim-delta.json", "claim_count": len(rows), "automatic_confidence_promotion": False})
