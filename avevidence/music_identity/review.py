"""Separate attributed adjudication with level-specific support guards."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil

from ..common import AVError, file_record, finish_run, output_transaction, sha256, write_json
from ..event_contracts import array, canonical_digest, choice, object_fields, read_event_json
from ..inventory import identifier
from . import LEVELS, STATUSES
from .contracts import candidate, query_receipt, source_scope, validate_result
from .workflow import load_music_run

KINDS = {"SOURCE_VISIBLE_CREDIT", "OFFICIAL_CREDIT", "SCOPED_IDENTITY_REVIEW", "REFERENCE_IDENTITY"}


def decision_support(result, decision):
    """A lookup/family/negative alone never closes a proposition."""
    evidence = {e["evidence_id"]: e for e in result["evidence"]}
    selected = [evidence[eid] for eid in decision.get("evidence_ids", [])]
    level, ids = decision["level"], set(decision.get("candidate_ids", []))
    scoped = [e for e in selected if e.get("level") == level and e.get("actual_inspection_declared") is True and
              e.get("source") == result["source"] and ids <= set(e.get("candidate_ids", []))]
    if not ids:
        return False, "An identity decision must name its candidate subject"
    kinds = {e.get("kind") for e in scoped}
    if not scoped:
        return False, "No inspected source-bound evidence for this exact identity level"
    if level == "RECORDING":
        references = [e for e in scoped if e["kind"] == "REFERENCE_IDENTITY"]
        exact = [m for m in result["correspondences"] if m.get("status") in {"EXACT", "NEAR_EXACT"}
                 and m.get("verification_representation") == "native_rate_f64" and m.get("information_sufficient") is True]
        for e in references:
            for m in exact:
                if m["match_id"] not in e.get("match_ids", []):
                    continue
                if not m.get("exact_native_pcm") and e.get("all_channels_inspected_declared") is not True:
                    continue
                for side, other in (("a", "b"), ("b", "a")):
                    s, r = m[side], m[other]
                    bounds = result["source"]["interval_seconds"]
                    if (s["source_sha256"] == result["source"]["sha256"] and s["audio_stream"] == result["source"]["audio_stream"]
                        and s["source_start_s"] <= bounds[0] + .002 and s["source_end_s"] >= bounds[1] - .002
                        and r["source_sha256"] == e.get("reference_source_sha256") and r["audio_stream"] == e.get("reference_audio_stream")):
                        return True, "Attributed reference identity plus full query coverage by native-verified recording correspondence"
        return False, "Recording identity needs a credited reference and matching full-coverage native-verified reuse edge"
    credit_hashes = {e.get("artifact", {}).get("sha256") for e in scoped if e.get("kind") in {"SOURCE_VISIBLE_CREDIT", "OFFICIAL_CREDIT"}}
    if level == "COMPOSITION" and {"SOURCE_VISIBLE_CREDIT", "OFFICIAL_CREDIT"} <= kinds and len(credit_hashes) >= 2:
        return True, "Explicitly inspected source credit and official credit agree at composition level"
    reviews = [e for e in scoped if e["kind"] == "SCOPED_IDENTITY_REVIEW"]
    if reviews and all(e.get("actual_audio_inspection_declared") is True for e in reviews):
        if level == "PERFORMANCE" and not all(e.get("performance_identity_basis") for e in reviews):
            return False, "Performance identity needs its own inspected performance basis"
        if level in {"ARRANGEMENT", "PHRASE", "COMPOSITION"} and not all(e.get("comparison_basis") for e in reviews):
            return False, "Musical identity needs explicit temporal, lyrical or musical comparison basis"
        if level == "UNKNOWN":
            return False, "An unknown identity level cannot close an exact proposition"
        return True, "Proposition-specific attributed listening review; no global observer qualification"
    return False, "Metadata, catalogue grouping and model names remain candidates without scoped adjudication"


def validate_decisions(result):
    for d in result["adjudication"]:
        if d["status"] in {"CONFIRMED", "SUPPORTED", "CONTRADICTED"}:
            adequate, reason = decision_support(result, d)
            if not adequate:
                raise AVError(reason)
    for family in result["families"]:
        if family["status"] == "SUPPORTED" and not any(d["level"] == "COMPOSITION" and d["status"] in {"SUPPORTED", "CONFIRMED"}
            and family["family_id"] in d.get("family_ids", []) and set(d["candidate_ids"]) <= set(family["candidate_ids"])
            and family["adjudication_evidence_ids"] == d["evidence_ids"] for d in result["adjudication"]):
            raise AVError("Supported family lacks its separately adjudicated composition evidence")


def adjudicate(input, config, output):
    folder, parent = load_music_run(input)
    config = Path(config).resolve()
    review = read_event_json(config)
    object_fields(review, {"schema", "reviewer", "source", "evidence", "decisions", "declared_candidates"},
                  {"schema", "reviewer", "source", "evidence", "decisions"}, "music review")
    if review["schema"] != "ave.music-identity.review.v1" or review["source"] != parent["source"]:
        raise AVError("Review source hash/stream/clock/interval differs from the immutable identity run")
    from .contracts import bounded_text
    bounded_text(review["reviewer"])
    result = copy.deepcopy(parent)
    dependencies = [file_record(folder / "music-identity.json"), file_record(config)]
    with output_transaction(output, [folder, config]) as stage:
        declared = {}
        if review.get("declared_candidates"):
            q = query_receipt(parent["source"], "attributed-identity-review", observation_sha256=sha256(config),
                              reason="Supplied reviewed identity subject; still a candidate until separately adjudicated")
            for row in array(review["declared_candidates"], "declared identity subjects", 100):
                object_fields(row, {"label", "level", "title", "artist"}, {"label", "level", "title"}, "declared candidate")
                identifier(row["label"], "declared candidate label")
                if row["label"] in declared:
                    raise AVError("Duplicate declared identity label")
                c = candidate(provider="attributed-identity-review", level=row["level"], title=row["title"], artist=row.get("artist"),
                    basis="ATTRIBUTED_IDENTITY_SUBJECT", query_id=q["query_id"], interval=parent["source"]["interval_seconds"])
                declared[row["label"]] = c["candidate_id"]
                result["candidates"].append(c)
                q["candidate_ids"].append(c["candidate_id"])
            q["status"] = "CANDIDATE"
            result["queries"].append(q)
        for e in result["evidence"]:
            original = folder / e["artifact"]["path"]
            target = stage / e["artifact"]["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        # Existing source records stay immutable; retain them by hash, not silently rewritten paths.
        result["parent"] = {"sha256": sha256(folder / "music-identity.json"), "schema": SCHEMA,
                            "relationship": "SEPARATE_ADJUDICATION"}
        existing_ids = {e["evidence_id"] for e in result["evidence"]}
        for row in array(review["evidence"], "review evidence", 100):
            allowed = {"evidence_id", "kind", "artifact", "source", "level", "candidate_ids", "actual_inspection_declared",
                       "actual_audio_inspection_declared", "comparison_basis", "performance_identity_basis", "match_ids",
                       "reference_source_sha256", "reference_audio_stream", "statement"}
            allowed.add("all_channels_inspected_declared")
            object_fields(row, allowed, {"evidence_id", "kind", "artifact", "source", "level", "candidate_ids", "actual_inspection_declared", "statement"}, "review evidence")
            identifier(row["evidence_id"], "review evidence ID")
            if row["evidence_id"] in existing_ids:
                raise AVError("Later review must add a new evidence record, not rewrite an existing one")
            existing_ids.add(row["evidence_id"])
            choice(row["kind"], KINDS, "review evidence class")
            choice(row["level"], LEVELS, "review identity level")
            if row["source"] != parent["source"]:
                raise AVError("Review evidence scope differs from the original source")
            bounded_text(row["statement"])
            if type(row["actual_inspection_declared"]) is not bool:
                raise AVError("Declare whether actual inspection was performed")
            artifact = row["artifact"]
            object_fields(artifact, {"path", "sha256"}, {"path", "sha256"}, "review artifact")
            path = (config.parent / artifact["path"]).resolve()
            if not path.is_file() or path.is_symlink() or sha256(path) != artifact["sha256"]:
                raise AVError("Review artifact hash differs")
            snapshot = file_record(path, "music_review_artifact")
            # The evidence snapshot is private. A portable export carries its hash/alias instead of arbitrary raw bytes.
            name = "evidence/review-" + canonical_digest(row["evidence_id"])[:24] + path.suffix.lower()
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha256(target) != artifact["sha256"]:
                raise AVError("Review artifact changed while its evidence snapshot was copied")
            result["evidence"].append({**row, "candidate_ids": [declared.get(cid, cid) for cid in row["candidate_ids"]],
                                      "artifact": {"path": name, "sha256": artifact["sha256"]}})
            dependencies.append(snapshot)
        decisions = {d["level"]: d for d in result["adjudication"]}
        seen = set()
        for row in array(review["decisions"], "review decisions", 6, 1):
            object_fields(row, {"level", "status", "candidate_ids", "evidence_ids", "reason", "family_ids"},
                          {"level", "status", "candidate_ids", "evidence_ids", "reason"}, "identity decision")
            choice(row["level"], LEVELS, "decision level")
            choice(row["status"], STATUSES, "decision status")
            if row["level"] in seen:
                raise AVError("Duplicate decision level")
            seen.add(row["level"])
            bounded_text(row["reason"])
            decisions[row["level"]] = {**row, "candidate_ids": [declared.get(cid, cid) for cid in row["candidate_ids"]], "reviewer": review["reviewer"]}
        result["adjudication"] = list(decisions.values())
        from .families import group_candidates
        result["families"] = group_candidates(result["candidates"])
        for d in result["adjudication"]:
            if d["level"] == "COMPOSITION" and d["status"] in {"SUPPORTED", "CONFIRMED"}:
                for f in result["families"]:
                    if f["family_id"] in d.get("family_ids", []):
                        if f["basis"] != "SHARED_MUSICBRAINZ_WORK" or f["composition_scope"] == "PARTIAL_OR_MEDLEY" or not set(d["candidate_ids"]) <= set(f["candidate_ids"]):
                            raise AVError("Cannot promote a weak, partial or differently scoped composition family")
                        f.update(status="SUPPORTED", adjudication_evidence_ids=d["evidence_ids"])
        validate_result(result)
        validate_decisions(result)
        write_json(stage / "music-identity.json", result)
        return finish_run(stage, "music-adjudicate", dependencies,
            metadata={"result_file": "music-identity.json", "original_observations_changed": False})


from . import SCHEMA
