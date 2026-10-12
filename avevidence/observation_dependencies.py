"""Premise-to-dependent graph; contradiction never rewrites raw observations."""
from __future__ import annotations

from collections import deque
import copy

from .common import AVError
from .event_contracts import STATES, array, choice, node_ids, object_fields
from .inventory import text_value

RELATIONS = {"SUPPORTS", "CONSTRAINS", "CONTRADICTS", "DEPENDS_ON",
             "REQUIRES_RECHECK_IF_CHANGED", "DOES_NOT_DISCRIMINATE"}
PROPAGATING = {"SUPPORTS", "CONSTRAINS", "DEPENDS_ON", "REQUIRES_RECHECK_IF_CHANGED"}


def material_edge(edge):
    """Supplementary agreement is not a necessary premise by default."""
    return edge["relation"] in PROPAGATING and edge.get("role", "REQUIRED") == "REQUIRED"


def validate_graph(observations, claims, edges):
    nodes = node_ids(observations, claims)
    array(edges, "dependencies", maximum=10000)
    seen, adjacency, indegree = set(), {key: [] for key in nodes}, {key: 0 for key in nodes}
    material = {key: [] for key in nodes}
    for edge in edges:
        object_fields(edge, {"from", "to", "relation", "reason", "role"}, {"from", "to", "relation", "reason"}, "dependency")
        if "role" in edge:
            from .support_routes import ROLES
            choice(edge["role"], ROLES, "proposition-specific evidentiary role")
        a, b, relation = edge["from"], edge["to"], edge["relation"]
        if not isinstance(a, str) or not isinstance(b, str) or a not in nodes or b not in nodes or a == b:
            raise AVError("Dependency endpoints must be distinct existing node IDs")
        choice(relation, RELATIONS, "dependency relationship")
        text_value(edge["reason"], "dependency reason")
        identity = (a, b, relation)
        if identity in seen:
            raise AVError("Duplicate dependency edge")
        seen.add(identity)
        if nodes[a]["type"] == "claim" and nodes[b]["type"] == "observation":
            raise AVError("A claim cannot retroactively establish a raw observation")
        if edge["relation"] in PROPAGATING:
            adjacency[a].append(b)
            indegree[b] += 1
        if material_edge(edge):
            material[a].append(b)
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        key = queue.popleft()
        visited += 1
        for child in adjacency[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(nodes):
        raise AVError("Circular evidentiary dependencies are not admissible")
    return nodes, material


def propagate_changes(observations, claims, edges, changes):
    """Return a separate state overlay. Edge direction is premise -> dependent."""
    nodes, adjacency = validate_graph(observations, claims, edges)
    if not isinstance(changes, dict):
        raise AVError("Changes must map node IDs to explicitly reasoned state decisions")
    decisions = copy.deepcopy(changes)
    for key, value in decisions.items():
        if key not in nodes:
            raise AVError("Changed node is absent from the graph")
        object_fields(value, {"state", "reason"}, {"state", "reason"}, "state change")
        choice(value["state"], STATES, "changed state")
        text_value(value["reason"], "change reason")
    affected = {}
    for root, decision in decisions.items():
        if decision["state"] == nodes[root]["record"].get("status", "PROVISIONAL"):
            continue
        queue, seen = deque(adjacency[root]), set()
        while queue:
            child = queue.popleft()
            if child in seen:
                continue
            seen.add(child)
            affected.setdefault(child, []).append(root)
            queue.extend(adjacency[child])
    overlay = {}
    for key, node in nodes.items():
        before = node["record"].get("status", "PROVISIONAL")
        choice(before, STATES, "original node state")
        if key in decisions:
            after, reason = decisions[key]["state"], decisions[key]["reason"]
        elif key in affected:
            after, reason = "RECHECK", "Changed premise: " + ", ".join(sorted(affected[key]))
        else:
            after, reason = before, "No changed premise reaches this node"
        overlay[key] = {"before": before, "after": after, "reason": reason,
                        "changed_premises": sorted(set(affected.get(key, [])))}
    return {"schema": "ave.observation-dependencies.v1", "edge_direction": "premise_to_dependent",
            "decisions": decisions, "state_overlay": overlay, "raw_records_preserved": True,
            "recheck_ids": sorted(key for key, row in overlay.items() if row["after"] == "RECHECK"),
            "unaffected_ids": sorted(key for key in nodes if key not in affected and key not in decisions)}


def dependence_groups(ids, nodes, edges, evidence):
    """Conservative provenance groups; their number is never a vote count."""
    if len(set(ids)) != len(ids) or set(ids) - nodes.keys():
        raise AVError("Independence check references missing or duplicate nodes")
    parent = {key: key for key in ids}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    def union(a, b):
        parent[find(b)] = find(a)
    origins = {}
    assumptions = {}
    for key in ids:
        node = nodes[key]["record"]
        origins[key] = set()
        assumptions[key] = set(node.get("shared_assumptions", []))
        for eid in node.get("evidence_ids", []):
            ref = evidence[eid]
            origins[key].add(ref["artifact"]["sha256"])
            origins[key].update(ref.get("origin_ids", []))
            origins[key].update(ref.get("context_evidence_ids", []))
            origins[key].update(ref.get("independence_declaration", {}).get("upstream_evidence_ids", []))
            assumptions[key].update(ref.get("shared_assumptions", []))
            if ref.get("stage1_evidence_id"):
                origins[key].add(ref["stage1_evidence_id"])
            origins[key].add(eid)
    adjacency = {key: [] for key in nodes}
    for edge in edges:
        if edge["relation"] in PROPAGATING:
            adjacency[edge["from"]].append(edge["to"])
    def reaches(a, b):
        todo, seen = [a], set()
        while todo:
            current = todo.pop()
            if current == b:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(adjacency[current])
        return False
    reasons = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = origins[a] & origins[b]
            assumed = assumptions[a] & assumptions[b]
            dependent = reaches(a, b) or reaches(b, a)
            if shared or assumed or dependent:
                union(a, b)
                reasons.append({"a": a, "b": b, "shared_origins": sorted(shared),
                                "shared_assumptions": sorted(assumed), "graph_dependence": dependent})
    groups = {}
    for key in ids:
        groups.setdefault(find(key), []).append(key)
    contextual = any(evidence[eid].get("context_evidence_ids") or evidence[eid].get("stage1_evidence_id")
                     for key in ids for eid in nodes[key]["record"].get("evidence_ids", []))
    independently_declared = len(ids) > 1 and not reasons and not contextual and all(
        evidence[eid].get("independence_declaration") for key in ids for eid in nodes[key]["record"].get("evidence_ids", []))
    return {"groups": sorted(sorted(rows) for rows in groups.values()), "dependence_reasons": reasons,
            "agreement_class": "CONTEXT_INFLUENCED_OBSERVATION" if contextual else "DEPENDENT_CONSISTENCY" if reasons else "INDEPENDENT_CORROBORATION" if independently_declared else "INDEPENDENCE_NOT_ESTABLISHED",
            "independence_not_proven": not independently_declared, "not_a_majority_vote": True}
