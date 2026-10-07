"""Versioned correspondence ledger; partial edges never union whole performances."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..common import AVError, utc_now, sha256
from ..performance_cache import digest
from .media import QUALITY


def connect(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS reuse_meta (version INTEGER NOT NULL);
        INSERT INTO reuse_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM reuse_meta);
        CREATE TABLE IF NOT EXISTS events (revision INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, action TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS occurrences (id TEXT PRIMARY KEY, introduced INTEGER NOT NULL, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, introduced INTEGER NOT NULL, record TEXT NOT NULL);
    """)
    if db.execute("SELECT version FROM reuse_meta").fetchone()[0] != 1:
        raise AVError("Unsupported reuse ledger schema")
    return db


def event(db, action, payload):
    return db.execute("INSERT INTO events(created_at,action,payload) VALUES (?,?,?)",
                      (utc_now(), action, json.dumps(payload, ensure_ascii=False, allow_nan=False))).lastrowid


def ingest(database, occurrences, matches, run_identity):
    db = connect(database)
    try:
        with db:
            # A verified run can be indexed again after interruption without a
            # second analytical occurrence or a duplicate decision event.
            for e in db.execute("SELECT revision,payload FROM events WHERE action='ingest'"):
                if json.loads(e["payload"])["run_identity"] == run_identity:
                    return e["revision"]
            revision = event(db, "ingest", {"run_identity": run_identity})
            for table, field, rows in (("occurrences", "occurrence_id", occurrences), ("matches", "match_id", matches)):
                for row in rows:
                    old = db.execute(f"SELECT record FROM {table} WHERE id=?", (row[field],)).fetchone()
                    data = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    if old and old["record"] != data:
                        # Existing attribution is never silently overwritten by
                        # an unrelated scan. New observations need a new ledger
                        # or an explicit decision on the correspondence.
                        prior = json.loads(old["record"])
                        if table == "occurrences":
                            compared = set(row) - {"source_path", "label", "role"}
                            if any(prior.get(k) != row.get(k) for k in compared):
                                raise AVError(f"Occurrence metadata changed for {row[field]}; preserve the old ledger and review the differing declaration")
                        elif prior != row:
                            raise AVError("Match identity collision")
                    if not old:
                        db.execute(f"INSERT INTO {table} VALUES (?,?,?)", (row[field], revision, data))
            return revision
    finally:
        db.close()


def ledger(database, revision=None):
    if not Path(database).is_file():
        raise AVError("Reuse ledger does not exist; scan or compare first")
    db = connect(database)
    try:
        latest = db.execute("SELECT COALESCE(MAX(revision),0) FROM events").fetchone()[0]
        revision = latest if revision is None else revision
        if not isinstance(revision, int) or not 0 <= revision <= latest:
            raise AVError("Revision is outside the ledger history")
        occ = {r["id"]: json.loads(r["record"]) for r in db.execute("SELECT * FROM occurrences WHERE introduced<=? ORDER BY introduced,id", (revision,))}
        matches = {r["id"]: json.loads(r["record"]) for r in db.execute("SELECT * FROM matches WHERE introduced<=? ORDER BY introduced,id", (revision,))}
        events = [{"revision": r["revision"], "created_at": r["created_at"], "action": r["action"],
                   "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM events WHERE revision<=? ORDER BY revision", (revision,))]
        return occ, matches, events, revision
    finally:
        db.close()


def duration(o):
    return o["source_end_s"]-o["source_start_s"]


def full(edge, side, occurrence):
    span = edge[side]
    # Full identity needs actual endpoint coverage, never a percentage alone.
    tolerance = max(2/occurrence["sample_rate_hz"], min(.04, edge.get("alignment_tolerance_s", 0)))
    covered = span["end_s"]-span["start_s"]
    return (span["start_s"] <= tolerance and span["end_s"] >= duration(occurrence)-tolerance
            and covered >= .98*duration(occurrence))


def vocal_gate(a, b):
    return (a.get("vocal_status") == b.get("vocal_status") == "verified_voice" and
            a.get("speaker") and a.get("speaker") == b.get("speaker") and
            "isolated_voice_asset" in {a.get("quality"), b.get("quality")})


def default_decision(edge, a, b):
    if "automatic_identity_decision" in edge:
        return edge["automatic_identity_decision"]
    compatible = not (a.get("speaker") and b.get("speaker") and a["speaker"] != b["speaker"])
    isolated_reference = compatible and any(o.get("quality") == "isolated_voice_asset" and
        o.get("vocal_status") == "verified_voice" and o.get("speaker") and full(edge, side, o)
        for side, o in (("a", a), ("b", b)))
    exact_vocal_mix = (edge.get("exact_native_pcm") and compatible and
        a.get("vocal_status") == b.get("vocal_status") == "verified_voice" and
        a.get("speaker") and b.get("speaker") and full(edge, "a", a) and full(edge, "b", b))
    if edge.get("auto_performance_link") and (vocal_gate(a, b) or isolated_reference or exact_vocal_mix):
        return "same_performance"
    if edge["classification"] in {"EXACT", "NEAR_EXACT"}:
        return "audio_only"
    return "unresolved"


def correspondence_key(edge):
    # Observation scores and software versions can change while the same
    # source-bound correspondence remains the subject of a human decision.
    fields = ("occurrence_id", "source_sha256", "audio_stream", "input_sample_start", "input_sample_end", "sample_rate_hz", "matching_channel")
    sides = [{k: edge[side][k] for k in fields} for side in ("a", "b")]
    sides.sort(key=lambda side: json.dumps(side, sort_keys=True))
    return "link-"+digest(sides)[:24]


def snapshot(database, revision=None, speaker=None):
    occ, matches, events, revision = ledger(database, revision)
    observations = list(matches.values())
    latest, histories = {}, {}
    for m in observations:
        key = correspondence_key(m)
        latest[key] = m
        histories.setdefault(key, []).append(m["match_id"])
    decisions, promotions = {}, []
    for e in events:
        if e["action"] == "decision":
            p = e["payload"]
            decisions[correspondence_key(matches[p["match_id"]])] = p
        elif e["action"] == "promote":
            promotions.append(e["payload"])
        elif e["action"] == "annotate":
            p = e["payload"]
            occ[p["occurrence_id"]].update(p["changes"])
    parent = {k: k for k in occ}
    order = {k: i for i, k in enumerate(occ)}

    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k

    edges = []
    for key, m in latest.items():
        a, b = occ[m["a"]["occurrence_id"]], occ[m["b"]["occurrence_id"]]
        override = decisions.get(key)
        state = default_decision(m, a, b) if not override or override["decision"] == "reset" else override["decision"]
        row = dict(m, correspondence_id=key, observation_history=histories[key], identity_decision=state,
                   decision_authority=override or {"method": "conservative_rule_v1"})
        declared_units = a["unit_kind"] in {"performance", "fragment"} and b["unit_kind"] in {"performance", "fragment"}
        attributed_units = (a.get("vocal_status") == b.get("vocal_status") == "verified_voice" and
                            a.get("speaker") and a.get("speaker") == b.get("speaker"))
        explicit_identity = override is not None and override["decision"] == "same_performance"
        row["whole_unit_equivalence"] = bool(state == "same_performance" and declared_units and
            (attributed_units or explicit_identity) and full(m, "a", a) and full(m, "b", b))
        edges.append(row)
        if row["whole_unit_equivalence"]:
            x, y = find(a["occurrence_id"]), find(b["occurrence_id"])
            if x != y:
                earlier, later = sorted([x, y], key=lambda k: order[k])
                parent[later] = earlier
    groups = {}
    for k, o in occ.items():
        root = find(k)
        groups.setdefault(root, []).append(o)
    performances, media_edits, membership = [], [], {}
    for root, members in groups.items():
        declared_unit = any(o["unit_kind"] in {"performance", "fragment"} for o in members)
        pid = "perf-" + root.removeprefix("occ-") if declared_unit else None
        identity = pid or "edit-"+root.removeprefix("occ-")
        ranked = sorted(members, key=lambda o: (QUALITY[o["quality"]], -duration(o), order[o["occurrence_id"]]))
        canonical = ranked[0]["occurrence_id"]
        preferred = {}
        for p in promotions:
            aliases = {"perf-"+o["occurrence_id"].removeprefix("occ-") for o in members}
            if p["performance_id"] in aliases and p["occurrence_id"] in {o["occurrence_id"] for o in members}:
                if p["feature"] == "default": canonical = p["occurrence_id"]
                else: preferred[p["feature"]] = p["occurrence_id"]
        record = {"identity_id": identity, "performance_id": pid,
            "media_edit_id": None if declared_unit else identity,
            "record_type": "vocal_unit" if declared_unit else "media_edit_or_unresolved_window",
            "canonical_occurrence_id": canonical,
            "preferred_witness_by_feature": preferred, "occurrence_ids": [o["occurrence_id"] for o in members],
            "aliases": ["perf-"+o["occurrence_id"].removeprefix("occ-") for o in members if o["occurrence_id"] != root],
            "identity_scope": "declared_unit_or_recorded_fragment; not reconstructed recording-session history" if declared_unit else "media_window_only"}
        (performances if declared_unit else media_edits).append(record)
        for o in members: membership[o["occurrence_id"]] = identity
    if speaker is not None:
        keep = {k for k, o in occ.items() if o.get("speaker") == speaker}
        occ = {k: o for k, o in occ.items() if k in keep}
        # Keep complete edges/groups touching the requested speaker; linked
        # participants are explicitly included rather than dangling references.
        edges = [e for e in edges if e["a"]["occurrence_id"] in keep or e["b"]["occurrence_id"] in keep]
        linked = {e[s]["occurrence_id"] for e in edges for s in ("a", "b")}
        original, _, _, _ = ledger(database, revision)
        occ.update({k: original[k] for k in linked})
        performances = [p for p in performances if set(p["occurrence_ids"]) & set(occ)]
        media_edits = [p for p in media_edits if set(p["occurrence_ids"]) & set(occ)]
        for p in performances+media_edits:
            occ.update({k: original[k] for k in p["occurrence_ids"]})
    from .appearances import appearances
    actor_membership = {k: p["performance_id"] for p in performances+media_edits for k in p["occurrence_ids"]}
    fragments = appearances(edges, occ, actor_membership)
    for edit in media_edits:
        edit["components"] = sorted([f for f in fragments if f["containing_occurrence_id"] in edit["occurrence_ids"]],
                                    key=lambda f: f["occurrence_ranges_s"][0][0])
    return {"schema": "ave.reuse.graph.v1", "revision": revision, "occurrences": list(occ.values()),
            "performances": performances, "media_edits": media_edits, "matches": edges, "events": events,
            "fragment_occurrences": fragments,
            "raw_observation_count": len(observations),
            "direction_policy": "a/b express correspondence only; historical derivation is unknown unless separately documented",
            "policy_implementation_sha256": sha256(__file__),
            "perceptual_review": "NOT_PERFORMED_BY_TOOLKIT"}


def annotate(database, occurrence_id, changes, reason, reviewer):
    allowed = {"speaker", "label", "vocal_status", "vocal_basis", "quality", "unit_kind", "context", "measurement_exclusions"}
    if not isinstance(changes, dict) or not changes or set(changes)-allowed:
        raise AVError("Annotations may change attribution/context/quality only; source identities and spans are immutable")
    g = snapshot(database)
    current = next((o for o in g["occurrences"] if o["occurrence_id"] == occurrence_id), None)
    if current is None: raise AVError("Unknown occurrence")
    revised = dict(current, **changes)
    if revised["quality"] not in QUALITY or revised["vocal_status"] not in {"unknown", "candidate", "verified_voice", "verified_nonvocal"}:
        raise AVError("Invalid attribution or quality")
    if revised["unit_kind"] not in {"performance", "fragment", "window", "composite"}:
        raise AVError("Invalid unit kind")
    if revised["vocal_status"].startswith("verified_") and not str(revised.get("vocal_basis", "")).strip():
        raise AVError("Verified attribution requires its basis")
    if not reason.strip() or not reviewer.strip(): raise AVError("Annotation requires a reason and reviewer")
    if not isinstance(revised.get("measurement_exclusions", []), list) or set(revised.get("measurement_exclusions", []))-{"pitch","level","timbre","timing"}:
        raise AVError("Invalid measurement exclusions")
    db = connect(database)
    try:
        with db:
            rev = event(db, "annotate", {"occurrence_id": occurrence_id, "changes": changes, "reason": reason, "reviewer": reviewer})
        return {"revision": rev, "occurrence_id": occurrence_id, "changes": changes,
                "identity_decisions": "Unchanged; use decide to revise specific correspondence decisions"}
    finally: db.close()


def decide(database, match_id, decision, reason, reviewer):
    if decision not in {"same_performance", "audio_only", "distinct", "unresolved", "reset"}:
        raise AVError("Invalid identity decision")
    if not reason.strip() or not reviewer.strip():
        raise AVError("Decision requires a reason and reviewer")
    _, matches, _, _ = ledger(database)
    if match_id not in matches:
        raise AVError("Unknown match ID")
    db = connect(database)
    try:
        with db:
            rev = event(db, "decision", {"match_id": match_id, "decision": decision, "reason": reason, "reviewer": reviewer})
        return {"revision": rev, "match_id": match_id, "decision": decision}
    finally:
        db.close()


def promote(database, performance_id, occurrence_id, reason, reviewer, feature="default"):
    if feature not in {"default", "pitch", "level", "timbre", "timing"}:
        raise AVError("Invalid witness feature")
    if not reason.strip() or not reviewer.strip():
        raise AVError("Promotion requires a reason and reviewer")
    graph = snapshot(database)
    p = next((p for p in graph["performances"] if p["performance_id"] == performance_id), None)
    if p is None or occurrence_id not in p["occurrence_ids"]:
        raise AVError("Promotion requires a full-coverage member of this performance; partial witnesses remain range links")
    db = connect(database)
    try:
        with db:
            rev = event(db, "promote", {"performance_id": performance_id, "occurrence_id": occurrence_id,
                        "feature": feature, "reason": reason, "reviewer": reviewer})
        return {"revision": rev, "performance_id": performance_id, "canonical_occurrence_id": occurrence_id, "feature": feature}
    finally:
        db.close()
