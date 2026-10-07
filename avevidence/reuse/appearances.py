"""Distinct appearances of matched reference material within containing windows."""
from ..mapping import union_intervals
from ..performance_cache import digest


def appearances(edges, occurrences, membership):
    clusters = []
    by_pair = {}
    for edge in sorted(edges, key=lambda e: (e["a"]["occurrence_id"], e["b"]["occurrence_id"], e["b"]["start_s"])):
        a,b=edge["a"],edge["b"]
        ratio=edge.get("duration_ratio_b_over_a",1.)
        offset=b["start_s"]-ratio*a["start_s"]
        tolerance=max(.002,edge.get("alignment_tolerance_s",0))
        pair=(a["occurrence_id"],b["occurrence_id"])
        cluster=next((c for c in by_pair.get(pair,[]) if abs(c["ratio"]-ratio)<.005 and abs(c["offset"]-offset)<=max(c["tolerance"],tolerance)
            and any(a["start_s"]<=r[1]+.002 and a["end_s"]>=r[0]-.002 for r in c["source_ranges"])),None)
        if cluster is None:
            cluster={"anchor":a["occurrence_id"],"container":b["occurrence_id"],"ratio":ratio,"offset":offset,
                     "tolerance":tolerance,"source_ranges":[],"target_ranges":[],"edges":[]}
            clusters.append(cluster)
            by_pair.setdefault(pair,[]).append(cluster)
        cluster["source_ranges"].append([a["start_s"],a["end_s"]])
        cluster["target_ranges"].append([b["start_s"],b["end_s"]])
        cluster["edges"].append(edge)
    result=[]
    for c in clusters:
        source_ranges,target_ranges=union_intervals(c["source_ranges"]),union_intervals(c["target_ranges"])
        base={"anchor_occurrence_id":c["anchor"],"containing_occurrence_id":c["container"],
              "anchor_ranges_s":source_ranges,"occurrence_ranges_s":target_ranges}
        result.append({"fragment_occurrence_id":"fragment-occ-"+digest(base)[:24],**base,
            "source_ranges_s":[[a+occurrences[c["container"]]["source_start_s"],b+occurrences[c["container"]]["source_start_s"]] for a,b in target_ranges],
            "correspondence_ids":[e["correspondence_id"] for e in c["edges"]],
            "performance_ids":[membership[c["anchor"]]] if any(e["identity_decision"]=="same_performance" for e in c["edges"])
                and membership[c["anchor"]] is not None else [],
            "matched_seconds":sum(b-a for a,b in target_ranges),
            "status":"ACCEPTED_PERFORMANCE_REUSE" if any(e["identity_decision"]=="same_performance" for e in c["edges"]) else "AUDIO_CORRESPONDENCE_OR_CANDIDATE",
            "historical_direction":"unknown",
            "counting_scope":"Appearance of the specified reference fragment; overlapping template observations are coalesced, not new performance votes"})
    return result
