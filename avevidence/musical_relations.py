"""Musical/lyrical recurrence is orthogonal to recorded performance identity."""
from __future__ import annotations

from pathlib import Path

from .analysis_schema import locator
from .common import AVError, file_record, finish_run, output_transaction, read_json, write_json
from .inventory import identifier, text_value

RELATIONS = {"LYRIC_RECURRENCE", "MELODIC_RECURRENCE", "MUSICAL_PHRASE_RECURRENCE", "SAME_COMPOSITION",
    "SAME_COMPOSITION_DIFFERENT_REALIZATION", "ARRANGEMENT_TRANSFORMATION", "SAME_RECORDED_PERFORMANCE", "EDITED_RECORDING_REUSE"}


def musical_relations(config, output):
    from .evidence_claims import _artifact
    config = Path(config).resolve()
    data = read_json(config)
    deps = {}
    rows, seen = [], set()
    if not isinstance(data.get("relations"), list) or not 0 < len(data["relations"]) <= 1000:
        raise AVError("Require 1..1000 musical relation records")
    for row in data["relations"]:
        rid = identifier(row.get("id"), "relation ID")
        if rid in seen:
            raise AVError("Duplicate musical relation ID")
        seen.add(rid)
        locator(row.get("a")); locator(row.get("b"))
        if row.get("relation") not in RELATIONS or row.get("status") not in {"SUPPORTED", "PROVISIONAL", "OPEN", "CONTRADICTED"}:
            raise AVError("Invalid musical relation or support state")
        evidence = row.get("evidence", [])
        witnesses=[]
        for witness in evidence:
            witnesses.append(_artifact(witness, config.parent, deps))
        if row["status"] == "SUPPORTED":
            text_value(row.get("reviewer"), "relation reviewer")
            text_value(row.get("reason"), "relation evidence rationale")
            if not evidence:
                raise AVError("Supported musical relation needs evidence")
            if row["relation"] in {"SAME_RECORDED_PERFORMANCE", "EDITED_RECORDING_REUSE"}:
                from .inventory import verify_run
                linked=False
                for witness in witnesses:
                    path=Path(witness["path"])
                    if path.suffix != ".json" or not (path.parent/"run.json").is_file():
                        continue
                    verify_run(path.parent)
                    graph=read_json(path)
                    if graph.get("schema") != "ave.reuse.graph.v1":
                        continue
                    for edge in graph.get("matches",[]):
                        def same_span(side,loc):
                            return (side["source_sha256"]==loc["source_sha256"] and any(
                                abs(a-side["source_start_s"])<=1/side["sample_rate_hz"]+1e-6 and
                                abs(b-side["source_end_s"])<=1/side["sample_rate_hz"]+1e-6
                                for a,b in loc["intervals_seconds"]))
                        paired=(same_span(edge["a"],row["a"]) and same_span(edge["b"],row["b"])) or (
                            same_span(edge["a"],row["b"]) and same_span(edge["b"],row["a"]))
                        accepted=(edge.get("whole_unit_equivalence") is True if row["relation"]=="SAME_RECORDED_PERFORMANCE"
                            else edge.get("identity_decision") in {"same_performance","audio_only"})
                        if paired and accepted and edge.get("match_id")==row.get("reuse_match_id"):
                            linked=True
                if not linked:
                    raise AVError("Recording relation needs a matching accepted edge from a verified reuse graph; musical records cannot merge takes")
            if row["relation"] == "SAME_COMPOSITION_DIFFERENT_REALIZATION" and row.get("recording_identity_status") != "DISTINCT_VERIFIED":
                raise AVError("Different realization requires verified distinct performance evidence, not a reuse nonmatch")
        rows.append(dict(row, baseline_identity_effect="NONE", recording_identity_status=row.get("recording_identity_status", "OPEN")))
    with output_transaction(output, [config]) as stage:
        write_json(stage / "musical-relations.json", {"schema": "ave.musical-relations.v1", "relations": rows,
            "policy": "Multiple musical relationships can coexist. Recording identity is controlled only by the separate conservative reuse ledger."})
        return finish_run(stage, "musical-relations", [file_record(config)] + list(deps.values()),
                          metadata={"result_file": "musical-relations.json", "relation_count": len(rows)})


def ordered_chroma_candidate(a, b, *, max_frames=600):
    """Bounded ordered cosine/DTW descriptor; never automatic reprise/take identity."""
    import numpy as np
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 2 or b.ndim != 2 or a.shape[1] != 12 or b.shape[1] != 12:
        raise AVError("Ordered chroma requires frame-by-12 matrices")
    if not all(0 < len(x) <= max_frames and np.isfinite(x).all() for x in (a,b)):
        raise AVError("Chroma input is invalid or exceeds the bounded comparison budget")
    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    cost = 1-np.clip(a @ b.T, -1, 1)
    table = np.full((len(a)+1, len(b)+1), np.inf)
    length = np.zeros(table.shape, dtype=int)
    table[0,0] = 0
    for i in range(1,len(a)+1):
        for j in range(1,len(b)+1):
            choices = [(table[i-1,j], i-1,j), (table[i,j-1], i,j-1), (table[i-1,j-1], i-1,j-1)]
            _, pi,pj = min(choices)
            table[i,j] = table[pi,pj] + cost[i-1,j-1]
            length[i,j] = length[pi,pj]+1
    return {"ordered_dtw_mean_cosine_distance": float(table[-1,-1]/length[-1,-1]),
            "status": "CANDIDATE_ONLY", "recording_identity_effect": "NONE",
            "required_before_support": ["temporal/lyrical correspondence", "negative controls", "attributed musical review"]}
