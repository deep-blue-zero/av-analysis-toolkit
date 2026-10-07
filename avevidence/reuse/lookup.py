"""Attach reuse evidence to existing source-bound dialogue search results."""
from .graph import snapshot
from .policy import audit


def attach(records, database, revision=None):
    graph = snapshot(database, revision)
    policies = audit(graph)
    by_source = {}
    membership = {k: p for p in policies["performances"] for k in p["occurrence_ids"]}
    for o in graph["occurrences"]:
        by_source.setdefault((o["source_sha256"], o["audio_stream"]), []).append(o)
    for record in records:
        linked = []
        for o in by_source.get((record["source_sha256"], record["audio_stream"]), []):
            overlap = min(record["end_s"], o["source_end_s"])-max(record["start_s"], o["source_start_s"])
            if overlap <= 0: continue
            p = membership[o["occurrence_id"]]
            linked.append({"occurrence_id": o["occurrence_id"], "overlap_s": overlap,
                "whole_search_cue_covered": o["source_start_s"] <= record["start_s"]+1/o["sample_rate_hz"] and o["source_end_s"] >= record["end_s"]-1/o["sample_rate_hz"],
                "performance_id": p["performance_id"], "canonical_occurrence_id": p["canonical_occurrence_id"],
                "relationship": p["relationship"], "source_fragment_links": p["source_fragment_links"],
                "eligibility_scope": "linked occurrence only; never automatically extended to the entire cue",
                "baseline_eligibility": p["baseline_eligibility"]})
        record["reuse"] = {"graph_revision": graph["revision"], "status": "LINKED" if linked else "NOT_INDEXED",
                           "correspondences": linked}
    return records
