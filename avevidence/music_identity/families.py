"""Composition families retain recording ambiguity and partial/medley scope."""
from __future__ import annotations

from collections import defaultdict
import re
import unicodedata

from ..event_contracts import canonical_digest


def group_candidates(candidates):
    works, weak, isolated = defaultdict(list), defaultdict(list), []
    catalog_works = defaultdict(set)
    for c in candidates:
        if c.get("lookup_basis") == "CATALOGUE_RELATION" and c.get("musicbrainz_recording_id"):
            catalog_works[c["musicbrainz_recording_id"]].update(c.get("musicbrainz_work_ids", []))
    for c in candidates:
        # Join only through explicit recording/work catalogue relations. A work tag alone remains declared metadata.
        work_ids = sorted(catalog_works.get(c.get("musicbrainz_recording_id"), set()))
        if work_ids:
            for wid in work_ids:
                works[wid].append(c)
        elif c.get("title"):
            title = re.sub(r"\W+", " ", unicodedata.normalize("NFKC", c["title"]).casefold()).strip()
            weak[title].append(c)
        else:
            isolated.append(c)
    rows = []
    def add(members, basis, wid=None):
        ids = sorted(c["candidate_id"] for c in members)
        partial = any(len(catalog_works.get(c.get("musicbrainz_recording_id"), set())) > 1 or
            any(set(w.get("attributes", [])) & {"medley", "mashup", "partial", "excerpt"}
                for w in (c.get("relation") or {}).get("works", []) if isinstance(w, dict)) for c in members)
        work_titles = {w.get("title") for c in members for w in (c.get("relation") or {}).get("works", [])
                       if isinstance(w, dict) and w.get("id") == wid and w.get("title")}
        titles = work_titles or {c["title"] for c in members if c.get("title")}
        row = {"schema": "ave.music-identity.family.v1", "candidate_ids": ids,
               "identity_level": "COMPOSITION", "canonical_title": next(iter(titles)) if len(titles) == 1 else None,
               "candidate_recordings": sorted({c["musicbrainz_recording_id"] for c in members if c.get("musicbrainz_recording_id")}),
               "supporting_providers": sorted({c["provider"] for c in members}), "adjudication_evidence_ids": [],
               "work_id": wid, "basis": basis, "status": "CANDIDATE",
               "composition_scope": "PARTIAL_OR_MEDLEY" if partial else "QUERY_INTERVAL_CANDIDATE",
               "recording_identity_status": "UNRESOLVED", "score_aggregation": "NONE",
               "proposed_composition_status": "CANDIDATE" if wid is None or partial else "SUPPORTED_REQUIRES_ADJUDICATION",
               "reason": "Shared work relation is a family proposal, not proof of the queried recording" if wid else "Title similarity is only a weak candidate heuristic"}
        row["family_id"] = "music-family-" + canonical_digest(row)[:24]
        rows.append(row)
    for wid, members in sorted(works.items()):
        add(members, "SHARED_MUSICBRAINZ_WORK", wid)
    for title, members in sorted(weak.items()):
        add(members, "WEAK_TITLE_HEURISTIC" if len(members) > 1 else "UNLINKED_CANDIDATE")
    for c in isolated:
        add([c], "UNLINKED_CANDIDATE")
    return rows
