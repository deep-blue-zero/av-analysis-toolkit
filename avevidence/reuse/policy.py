"""Counting policy is query-specific; occurrences and partial coverage survive."""
from __future__ import annotations

from ..mapping import union_intervals
from .graph import duration, full
from .media import QUALITY


FEATURES = ("pitch", "level", "timbre", "timing")


def coverage(spans):
    return sum(b-a for a, b in union_intervals(spans))


def audit(graph, baseline=None):
    occ = {o["occurrence_id"]: o for o in graph["occurrences"]}
    identities = graph["performances"]+graph.get("media_edits", [])
    membership = {k: p["identity_id"] for p in identities for k in p["occurrence_ids"]}
    groups = {p["identity_id"]: p for p in identities}
    partial = {k: [] for k in occ}
    effects = {k: {f: [] for f in FEATURES} for k in occ}
    suspicious = []
    for edge in graph["matches"]:
        ka, kb = edge["a"]["occurrence_id"], edge["b"]["occurrence_id"]
        a, b = occ[ka], occ[kb]
        if edge["identity_decision"] == "same_performance":
            if abs(edge.get("pitch_shift_semitones_b_minus_a", 0)) > .05:
                for feature in ("pitch", "timbre"): effects[kb][feature].append("relative_pitch_transformation")
            if abs(edge.get("duration_ratio_b_over_a", 1)-1) > .005:
                effects[kb]["timing"].append("relative_timing_transformation")
            gain = edge["metrics"].get("fitted_gain_b_over_a")
            if gain is not None and abs(abs(gain)-1) > .015:
                effects[kb]["level"].append("relative_gain_transformation")
        if edge["identity_decision"] == "unresolved":
            suspicious.append(edge["match_id"])
        if edge["identity_decision"] != "same_performance" or edge["whole_unit_equivalence"]:
            continue
        # Analysis ownership is explicitly separate from historical ancestry.
        # Full declared performances may own a trimmed fragment; otherwise a
        # fully matched reference fragment supplies part of a larger edit.
        if a["unit_kind"] == "composite" and b["unit_kind"] != "composite":
            src, dst = "b", "a"
        elif b["unit_kind"] == "composite" and a["unit_kind"] != "composite":
            src, dst = "a", "b"
        elif a["unit_kind"] == "performance" and b["unit_kind"] in {"fragment", "window"}:
            src, dst = "a", "b"
        elif b["unit_kind"] == "performance" and a["unit_kind"] in {"fragment", "window"}:
            src, dst = "b", "a"
        elif a["unit_kind"] in {"performance", "fragment"} and b["unit_kind"] in {"performance", "fragment"}:
            src, dst = ("a", "b") if (-duration(a), QUALITY[a["quality"]], ka) <= (-duration(b), QUALITY[b["quality"]], kb) else ("b", "a")
        elif full(edge, "a", a) and not full(edge, "b", b):
            src, dst = "a", "b"
        elif full(edge, "b", b) and not full(edge, "a", a):
            src, dst = "b", "a"
        else:
            # A partial/partial match proves local overlap, not either entire
            # performance's identity. Keep it for duration audit and review.
            src, dst = ("a", "b") if (QUALITY[a["quality"]], -duration(a), ka) <= (QUALITY[b["quality"]], -duration(b), kb) else ("b", "a")
        source, target = edge[src], edge[dst]
        partial[target["occurrence_id"]].append({"match_id": edge["match_id"],
            "performance_id": groups[membership[source["occurrence_id"]]]["performance_id"],
            "source_identity_id": membership[source["occurrence_id"]],
            "source_occurrence_id": source["occurrence_id"], "source_start_s": source["start_s"], "source_end_s": source["end_s"],
            "start_s": target["start_s"], "end_s": target["end_s"],
            "assignment": "analytical_coverage_not_historical_direction"})
    records = []
    eligible = {f: [] for f in FEATURES}
    total_unique_duration = 0.
    independent = set()
    for pid, p in groups.items():
        canonical = occ[p["canonical_occurrence_id"]]
        links = [r for k in p["occurrence_ids"] for r in partial[k]]
        # Coverage is expressed on each occurrence's local clock. Never sum
        # ranges from distinct witnesses as if they shared that clock.
        per_occ = {k: min(duration(occ[k]), coverage([(r["start_s"], r["end_s"]) for r in partial[k]])) for k in p["occurrence_ids"]}
        full_derivative = any(per_occ[k] >= duration(occ[k])-2/occ[k]["sample_rate_hz"] for k in p["occurrence_ids"])
        implicated = bool(links)
        is_composite = canonical["unit_kind"] == "composite" or len({r["performance_id"] for r in links}) > 1
        known_unit = p["performance_id"] is not None
        voice = canonical["vocal_status"] == "verified_voice" and bool(canonical.get("speaker"))
        countable = voice and known_unit and not full_derivative and not implicated and not is_composite
        if countable:
            independent.add(pid)
            total_unique_duration += duration(canonical)
        by_feature = {}
        for feature in FEATURES:
            def assess(k):
                witness = occ[k]
                reasons = list(effects[k][feature])
                if not countable: reasons.append("not_an_independently_resolved_vocal_unit")
                if QUALITY[witness["quality"]] > 2: reasons.append("mixed_or_unknown_witness")
                if feature in witness.get("measurement_exclusions", []): reasons.append("supplied_measurement_exclusion")
                return sorted(set(reasons))
            preferred = p["preferred_witness_by_feature"].get(feature)
            candidates = [preferred] if preferred else [p["canonical_occurrence_id"]]+sorted(
                (k for k in p["occurrence_ids"] if k != p["canonical_occurrence_id"]), key=lambda k: QUALITY[occ[k]["quality"]])
            witness_id = next((k for k in candidates if not assess(k)), candidates[0])
            reasons = assess(witness_id)
            by_feature[feature] = {"eligible": not reasons, "witness_occurrence_id": witness_id, "reasons": reasons,
                "selection": "explicit_feature_preference" if preferred else "best_eligible_available_witness",
                "rejected_higher_priority_witnesses": [{"occurrence_id": k, "reasons": assess(k)} for k in candidates[:candidates.index(witness_id)]]}
            if not reasons: eligible[feature].append(pid)
        records.append({**p, "speaker": canonical.get("speaker"), "raw_occurrence_count": len(p["occurrence_ids"]),
            "independent_unit_contribution": int(countable), "baseline_eligibility": by_feature,
            "relationship": "composite" if is_composite else "partial_or_trimmed_reuse" if implicated else "whole_unit_group",
            "source_fragment_links": links, "matched_reuse_seconds_by_occurrence": per_occ,
            "unresolved_seconds_by_occurrence": {k: max(0., duration(occ[k])-per_occ[k]) for k in p["occurrence_ids"]} if implicated else {},
            "representativeness": "NOT_ESTABLISHED; context selection remains a separate requirement"})
    existing = []
    seen = {}
    for row in baseline or []:
        k = row.get("occurrence_id")
        if k not in membership:
            existing.append({"occurrence_id": k, "status": "NOT_INDEXED"})
            continue
        pid = membership[k]
        status = "DUPLICATE_WHOLE_UNIT" if pid in seen else "PARTIAL_REUSE_REVIEW" if partial[k] else "INDEXED"
        existing.append({"occurrence_id": k, "performance_id": groups[pid]["performance_id"], "identity_id": pid, "status": status,
                         "first_baseline_row": seen.get(pid), "source_fragment_links": partial[k]})
        seen.setdefault(pid, len(existing)-1)
    contributions = {feature: [{"performance_id": p["performance_id"],
        "occurrence_id": p["baseline_eligibility"][feature]["witness_occurrence_id"], "weight": 1,
        "source_sha256": occ[p["baseline_eligibility"][feature]["witness_occurrence_id"]]["source_sha256"],
        "source_start_s": occ[p["baseline_eligibility"][feature]["witness_occurrence_id"]]["source_start_s"],
        "source_end_s": occ[p["baseline_eligibility"][feature]["witness_occurrence_id"]]["source_end_s"]}
        for p in records if p["baseline_eligibility"][feature]["eligible"]] for feature in FEATURES}
    return {"schema": "ave.reuse.audit.v1", "graph_revision": graph["revision"],
            "raw_occurrence_count": len(occ), "independent_resolved_unit_count": len(independent),
            "configured_source_window_count": len(occ),
            "detected_fragment_appearance_count": len(graph.get("fragment_occurrences",[])),
            "independence_status": "PROVISIONAL_WITHIN_SEARCH_SCOPE; count uses supplied units and accepted identity links, not proof of different recording acts",
            "unique_declared_vocal_seconds": total_unique_duration,
            "duration_definition": "Union-counted whole resolved units; unresolved partial coverage is reported separately, not invented as independent speech",
            "eligible_performance_ids_by_feature": eligible, "performances": records,
            "baseline_contributions_by_feature": contributions,
            "suspicious_match_ids": suspicious, "existing_baseline_rows": existing,
            "policy": "One independent resolved unit per group; all media occurrences remain available for presentation-frequency analysis",
            "perceptual_review": "NOT_PERFORMED_BY_TOOLKIT"}
