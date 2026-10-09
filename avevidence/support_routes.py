"""Declared proposition roles and alternative, scoped proof obligations."""
from __future__ import annotations

from .common import AVError
from .event_contracts import array, choice, object_fields, strings
from .inventory import identifier, text_value

ROLES = {"REQUIRED", "SUPPORTING", "CONTEXTUAL", "CONTRADICTORY", "NON_DISCRIMINATING"}
AFFIRMATIVE = {"SUPPORTED", "CONFIRMED"}


def validate_routes(claim, nodes, edges):
    routes = claim.get("support_routes")
    if routes is None:
        return
    allowed = set(claim["observation_ids"])
    seen = set()
    for route in array(routes, "alternative support routes", 100, 1):
        keys = {"id", "scope", "essential", "interchangeable", "supplementary", "corroborating", "insufficient_alone"}
        object_fields(route, keys, keys, "support route")
        rid = identifier(route["id"], "support route ID")
        if rid in seen:
            raise AVError("Duplicate support route ID")
        seen.add(rid); text_value(route["scope"], "surviving support scope")
        referenced = set()
        for name in ("essential", "supplementary", "corroborating", "insufficient_alone"):
            referenced.update(strings(route[name], name+" observations", 100))
        groups = array(route["interchangeable"], "interchangeable evidence groups", 100)
        for group in groups:
            referenced.update(strings(group, "interchangeable observations", 100, 1))
        if not route["essential"] and not groups:
            raise AVError("A legitimate support route requires essential evidence")
        core = set(route["essential"]) | {key for group in groups for key in group}
        partitions = [set(route["essential"]), set(route["supplementary"]), set(route["corroborating"])] + [set(group) for group in groups]
        if (any(a & b for i, a in enumerate(partitions) for b in partitions[i+1:])
            or not set(route["insufficient_alone"]) <= set().union(*partitions)):
            raise AVError("Support route classifications must be disjoint; insufficient-alone annotates a classified premise")
        if (referenced != allowed or core & set(route["supplementary"]) or
            any(nodes[key]["type"] != "observation" for key in referenced)):
            raise AVError("Support route references missing or inconsistently classified claim premises")
        for key in core:
            relation = [e for e in edges if e["from"] == key and e["to"] == claim["id"]]
            if not any(e.get("role", "REQUIRED") in {"REQUIRED", "SUPPORTING"} for e in relation):
                raise AVError("Essential proof evidence cannot have a contextual/non-discriminating role")
        required = {e["from"] for e in edges if e["to"] == claim["id"] and e.get("role", "REQUIRED") == "REQUIRED"}
        if not (required & allowed) <= set(route["essential"]):
            raise AVError("An alternative route cannot omit a globally declared required premise")


def assess_routes(claim, observations, overlay, packet, evidence):
    rows = []
    for route in claim["support_routes"]:
        def adequate(key):
            return observations[key]["adequate_for_support"] and overlay[key]["after"] in AFFIRMATIVE
        selected = list(route["essential"])
        missing = [key for key in selected if not adequate(key)]
        for group in route["interchangeable"]:
            candidates = [key for key in group if adequate(key)]
            if candidates:
                selected.append(candidates[0])
            else:
                missing.extend(group)
        # Insufficient-alone evidence may participate in a combined route.
        if len(set(selected)) == 1 and set(selected) & set(route["insufficient_alone"]):
            missing.extend(selected)
        reasons = []
        if claim["proposition"] == "av_sync":
            kinds = {observations[key]["proposition"] for key in selected if adequate(key)}
            visual = any(a["channel"] == "TVIS" and a["adequate_for_support"] for key in selected
                         for a in observations[key]["evidence_assessments"])
            if not visual or "auditory_event_timing" not in kinds:
                reasons.append("Exact AV synchronization requires reviewed temporal visual and qualified audible-event timing evidence")
            # Scene locators already bind both evidence channels to one source
            # clock. Every essential observation must span the claimed interval.
        if claim["proposition"] not in {"interpretation", "emotion"}:
            from .mapping import missing_intervals
            claim_interval = claim.get("interval_seconds", packet["interval_seconds"])
            nodes = {r["id"]: r for r in packet["observations"]}
            if any(missing_intervals([claim_interval], [nodes[key].get("interval_seconds", packet["interval_seconds"])]) for key in selected):
                reasons.append("Essential observations do not cover the direct claim's complete interval")
            if claim["proposition"] != "av_sync" and not any(
                    adequate(key) and observations[key]["proposition"] == claim["proposition"] for key in selected):
                reasons.append("No essential evidence is competent for the exact direct proposition")
        rows.append({"id": route["id"], "scope": route["scope"], "selected_essential": sorted(set(selected)),
            "missing_essential": sorted(set(missing)), "status": "SURVIVES" if not missing and not reasons else "OPEN",
            "reasons": reasons, "supplementary": route["supplementary"], "corroborating": route["corroborating"],
            "insufficient_alone": route["insufficient_alone"]})
    return rows
