"""Small, bounded, provenance-preserving music contracts."""
from __future__ import annotations

import copy
import re

from ..common import AVError
from ..event_contracts import CLOCK, array, canonical_digest, choice, object_fields, span
from ..inventory import digest_value, identifier, integer, number, text_value
from . import LEVELS, SCHEMA, STATUSES

PRIVACY_CLASSES = {"LOCAL_ONLY", "DERIVED_FINGERPRINT_LOOKUP", "REMOTE_MEDIA_LOOKUP"}
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def mbid(value):
    return value if isinstance(value, str) and MBID.fullmatch(value) else None


def bounded_text(value, maximum=2048):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise AVError("Music text must be bounded printable text")
    return value.strip()


def source_scope(source):
    object_fields(source, {"alias", "sha256", "display_name", "audio_stream", "clock", "interval_seconds"},
                  {"alias", "sha256", "display_name", "audio_stream", "clock", "interval_seconds"}, "music source")
    identifier(source["alias"], "logical source alias")
    bounded_text(source["display_name"], 256)
    digest_value(source["sha256"], "music source hash")
    integer(source["audio_stream"], "selected absolute audio stream")
    if source["clock"] != CLOCK:
        raise AVError("Music identity requires the original source clock")
    span(source["interval_seconds"])
    return source


def candidate(*, provider, level="RECORDING", title=None, artist=None, album=None, isrc=None,
              recording_id=None, work_ids=(), score=None, basis, query_id, interval,
              fingerprint_sha256=None, provider_id=None, relation=None):
    choice(level, LEVELS, "identity level")
    if score is not None:
        number(score, "provider-native score")
    row = {"level": level, "title": bounded_text(title), "artist": bounded_text(artist),
           "album": bounded_text(album), "isrc": bounded_text(isrc),
           "musicbrainz_recording_id": mbid(recording_id), "musicbrainz_work_ids": sorted(set(filter(None, map(mbid, work_ids)))),
           "provider": provider, "provider_id": bounded_text(provider_id), "provider_native_score": score,
           "score_is_probability": False, "lookup_basis": basis, "query_id": query_id,
           "query_interval_seconds": span(interval), "fingerprint_sha256": fingerprint_sha256,
           "relation": copy.deepcopy(relation), "status": "CANDIDATE"}
    row["candidate_id"] = "music-candidate-" + canonical_digest(row)[:24]
    return row


def query_receipt(source, provider, privacy_class="LOCAL_ONLY", **extra):
    choice(privacy_class, PRIVACY_CLASSES, "submission class")
    source_scope(source)
    row = {"schema": "ave.music-identity.lookup.v1", "provider": provider,
           "source": copy.deepcopy(source), "submission_class": privacy_class,
           "media_uploaded": False, "fingerprint_sent": False, "duration_sent": False,
           "authorization": "NOT_REQUIRED_LOCAL", "status": "NOT_TESTED",
           "fingerprint_sha256": None, "queried_at": None, "candidate_ids": [],
           "normalized_metadata": [], "missing_fields": [], "reason": None, **extra}
    row["query_id"] = "music-query-" + canonical_digest({k: v for k, v in row.items() if k != "queried_at"})[:24]
    return row


def validate_result(result):
    fields = {"schema", "source", "queries", "candidates", "families", "correspondences", "reference_comparisons",
              "adjudication", "unresolved", "network_activity", "privacy", "parent", "evidence"}
    object_fields(result, fields, fields - {"parent"}, "music identity result")
    if result["schema"] != SCHEMA:
        raise AVError("Unsupported music identity schema")
    source_scope(result["source"])
    bounds = result["source"]["interval_seconds"]
    ids = set()
    queries = {}
    for q in array(result["queries"], "music queries", 400):
        q_fields = {"schema", "query_id", "provider", "source", "submission_class", "media_uploaded", "fingerprint_sent", "duration_sent",
                    "authorization", "status", "fingerprint_sha256", "queried_at", "candidate_ids", "normalized_metadata", "missing_fields", "reason"}
        object_fields(q, q_fields | {"response_sha256", "external_request_performed", "test_double", "requested_recording_id", "metadata_retrieved_at", "observation_sha256"}, q_fields, "music lookup receipt")
        if q.get("schema") != "ave.music-identity.lookup.v1" or q.get("source") != result["source"]:
            raise AVError("Music query differs from its admitted source scope")
        identifier(q.get("query_id"), "query ID")
        if q["query_id"] in queries:
            raise AVError("Duplicate query identity")
        choice(q.get("submission_class"), PRIVACY_CLASSES, "submission class")
        choice(q.get("status"), STATUSES, "query status")
        choice(q.get("authorization"), {"NOT_REQUIRED_LOCAL", "EXPLICIT_EXTERNAL_LOOKUP"}, "lookup authorization")
        for key in ("fingerprint_sent", "duration_sent", "media_uploaded"):
            if type(q.get(key)) is not bool:
                raise AVError("Music privacy declarations must be explicit booleans")
        array(q["normalized_metadata"], "normalized metadata", 1000)
        array(q["candidate_ids"], "query candidates", 1000)
        if q.get("media_uploaded") is not False:
            raise AVError("This implementation admits no remote media lookup")
        external = q["submission_class"] == "DERIVED_FINGERPRINT_LOOKUP"
        if q.get("fingerprint_sent") and (not external or q.get("authorization") != "EXPLICIT_EXTERNAL_LOOKUP"):
            raise AVError("Fingerprint submission lacks separate lookup authorization")
        if q.get("fingerprint_sha256") is not None:
            digest_value(q["fingerprint_sha256"], "fingerprint hash")
        queries[q["query_id"]] = q
    for c in array(result["candidates"], "music candidates", 1000):
        if not isinstance(c, dict):
            raise AVError("Music candidate must be an attributed object")
        fields = {"candidate_id", "level", "title", "artist", "album", "isrc", "musicbrainz_recording_id", "musicbrainz_work_ids",
                  "provider", "provider_id", "provider_native_score", "score_is_probability", "lookup_basis", "query_id",
                  "query_interval_seconds", "fingerprint_sha256", "relation", "status"}
        object_fields(c, fields, fields, "music candidate")
        expected_id = "music-candidate-" + canonical_digest({k: v for k, v in c.items() if k != "candidate_id"})[:24]
        if c["candidate_id"] != expected_id:
            raise AVError("Music candidate content differs from its immutable identity")
        if not isinstance(c["musicbrainz_work_ids"], list) or len(c["musicbrainz_work_ids"]) > 100 or any(not mbid(w) for w in c["musicbrainz_work_ids"]):
            raise AVError("Malformed candidate work identifiers")
        if c["musicbrainz_recording_id"] is not None and not mbid(c["musicbrainz_recording_id"]):
            raise AVError("Malformed recording identifier")
        if c["relation"] is not None and not isinstance(c["relation"], dict):
            raise AVError("Malformed candidate relation")
        for key in ("title", "artist", "album", "isrc", "provider_id"):
            bounded_text(c[key])
        identifier(c.get("candidate_id"), "candidate ID")
        if c["candidate_id"] in ids or c.get("query_id") not in queries or c.get("status") != "CANDIDATE":
            raise AVError("Duplicate, unbound or automatically promoted music candidate")
        ids.add(c["candidate_id"])
        choice(c.get("level"), LEVELS, "candidate level")
        span(c.get("query_interval_seconds"), bounds)
        if c["query_interval_seconds"] != bounds or c.get("score_is_probability") is not False:
            raise AVError("Candidate scope or score semantics differ")
        if c.get("provider_native_score") is not None:
            number(c["provider_native_score"], "native score")
        q = queries[c["query_id"]]
        if c.get("fingerprint_sha256") != q.get("fingerprint_sha256"):
            raise AVError("Candidate fingerprint differs from its query")
    for q in queries.values():
        if set(q["candidate_ids"]) != {c["candidate_id"] for c in result["candidates"] if c["query_id"] == q["query_id"]}:
            raise AVError("Query candidate references differ")
    family_ids = set()
    for family in array(result["families"], "candidate families", 1000):
        identifier(family.get("family_id"), "family ID")
        if family["family_id"] in family_ids or not set(family.get("candidate_ids", [])) <= ids:
            raise AVError("Invalid family members")
        family_ids.add(family["family_id"])
        if family.get("status") not in {"CANDIDATE", "SUPPORTED"} or family.get("recording_identity_status") != "UNRESOLVED":
            raise AVError("Grouping alone cannot adjudicate an identity")
        if family.get("status") == "SUPPORTED" and (family.get("basis") != "SHARED_MUSICBRAINZ_WORK" or
            family.get("composition_scope") != "QUERY_INTERVAL_CANDIDATE" or not family.get("adjudication_evidence_ids")):
            raise AVError("Family support requires an explicit nonpartial work-level adjudication")
    evidence_ids = set()
    for e in array(result["evidence"], "identity evidence", 1000):
        identifier(e.get("evidence_id"), "identity evidence ID")
        if e["evidence_id"] in evidence_ids:
            raise AVError("Duplicate identity evidence ID")
        evidence_ids.add(e["evidence_id"])
        if e.get("source") != result["source"]:
            raise AVError("Identity review evidence scope differs")
        choice(e.get("level"), LEVELS, "review evidence level")
        if not isinstance(e.get("candidate_ids"), list) or not set(e["candidate_ids"]) <= ids:
            raise AVError("Identity review evidence cites missing candidates")
    levels = set()
    for d in array(result["adjudication"], "identity decisions", 6):
        choice(d.get("level"), LEVELS, "adjudication level")
        choice(d.get("status"), STATUSES, "adjudication status")
        if d["level"] in levels:
            raise AVError("Only one current adjudication per identity level")
        levels.add(d["level"])
        if not set(d.get("candidate_ids", [])) <= ids or not set(d.get("evidence_ids", [])) <= evidence_ids:
            raise AVError("Identity adjudication cites missing evidence")
        if not set(d.get("family_ids", [])) <= family_ids:
            raise AVError("Identity adjudication cites missing families")
        if d["status"] in {"CONFIRMED", "SUPPORTED"} and (not d.get("reviewer") or not d.get("reason") or not d.get("evidence_ids")):
            raise AVError("Supported identity requires a separate attributed evidence review")
    if set(result["network_activity"]) != {"external_requests", "media_uploads", "paid_calls"}:
        raise AVError("Malformed network activity")
    for key, value in result["network_activity"].items():
        integer(value, key)
    if result["network_activity"]["media_uploads"] or result["network_activity"]["paid_calls"]:
        raise AVError("Music identification has no media-upload or paid backend")
    if levels != set(LEVELS):
        raise AVError("Music identity must retain a separate status for every identity level")
    return result
