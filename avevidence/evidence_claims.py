"""Atomic claims, minimum evidence escalation and claim-driven reports.

Validation checks declared witnesses and source binding; it cannot certify an
external reviewer's attention or the truth of an interpretation.
"""
from __future__ import annotations

import html
from pathlib import Path
import shutil

from .analysis_schema import GATES, KINDS, LAYERS, flags, locator, modalities
from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, write_json
from .inventory import identifier, text_value
from .mapping import missing_intervals


def _artifact(value, base, dependencies):
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise AVError("Evidence artifact must have a path and SHA-256")
    path = Path(value["path"])
    path = path.resolve() if path.is_absolute() else (base / path).resolve()
    item = file_record(path, "evidence_witness")
    if item["sha256"] != value["sha256"]:
        raise AVError("Evidence artifact hash mismatch")
    dependencies[str(path)] = item
    return item


def validate_claims(data, base):
    dependencies, witnesses = {}, {}
    if not isinstance(data, dict) or data.get("schema") != "ave.claims.v1":
        raise AVError("Claim input requires schema ave.claims.v1")
    evidence = data.get("evidence", [])
    claims = data.get("claims")
    if not isinstance(evidence, list) or not isinstance(claims, list) or not 0 < len(claims) <= 1000 or len(evidence) > 10000:
        raise AVError("Require 1..1000 claims and at most 10000 evidence records")
    for e in evidence:
        eid = identifier(e.get("id"), "evidence ID")
        if eid in witnesses:
            raise AVError("Duplicate evidence ID")
        if e.get("layer") not in LAYERS or e.get("modality") not in {"text", "audio", "visual_stills", "motion"}:
            raise AVError("Unknown evidence layer or modality")
        locator(e.get("locator"), modality=e["modality"])
        text_value(e.get("statement"), "evidence statement")
        qflags = flags(e.get("quality_flags", []))
        artifact = _artifact(e.get("artifact"), base, dependencies)
        if e["layer"] in {"observed", "auditory_observation"}:
            text_value(e.get("reviewer"), "attributed observer")
            if e.get("inspection_status") != "REVIEW_DECLARED":
                raise AVError("Perceptual observations require an explicit inspection declaration")
        if e["layer"] == "auditory_observation":
            if e.get("observer_type") not in {"human_audio", "model_audio"}:
                raise AVError("Auditory witness must separately identify human or model")
            if e["observer_type"] == "model_audio":
                # The record itself must be a validated observation, not a fluent paragraph.
                obs = read_json(artifact["path"])
                if (obs.get("schema") != "ave.auditory-observation.v1" or obs.get("validation_status") != "VALID"
                        or obs.get("capability_status") != "PROBE_PASSED" or obs.get("observer_type") != "model_audio"
                        or not obs.get("parsed", {}).get("observations")):
                    qflags.append("UNVALIDATED_AUDITORY_ROUTE")
                elif obs.get("source_sha256") != e["locator"]["source_sha256"]:
                    raise AVError("Observer record is bound to another source")
                elif missing_intervals(e["locator"]["intervals_seconds"], [obs["source_interval_seconds"]]):
                    raise AVError("Auditory evidence exceeds its actual witness interval")
        if e["layer"] == "measured" and e["modality"] != "audio" and e.get("method") is None:
            raise AVError("Non-audio measurements must identify their measurement method")
        witnesses[eid] = dict(e, quality_flags=sorted(set(qflags)))
    seen, rows = set(), []
    for c in claims:
        cid = identifier(c.get("id"), "claim ID")
        if cid in seen:
            raise AVError("Duplicate claim ID")
        seen.add(cid)
        text_value(c.get("statement"), "atomic claim")
        kind = c.get("kind")
        if kind not in KINDS:
            raise AVError("Unknown analytical question kind")
        required = modalities(c.get("required_modalities"))
        wanted = c.get("locators")
        if not isinstance(wanted, list) or not wanted:
            raise AVError("Claim needs recoverable source locators")
        for loc in wanted:
            locator(loc)
        supporting = c.get("evidence_ids", [])
        counter = c.get("counterevidence_ids", [])
        if not all(isinstance(x, list) for x in (supporting, counter)) or set(supporting + counter)-witnesses.keys():
            raise AVError("Claim references absent evidence IDs")
        if set(supporting) & set(counter):
            raise AVError("The same witness cannot be both support and counterevidence without a separate atomic observation")
        supported_modalities, blockers, present = set(), set(flags(c.get("quality_flags", []))), []
        for eid in supporting:
            e = witnesses[eid]
            matching = [loc for loc in wanted if loc["source_sha256"] == e["locator"]["source_sha256"]]
            if not matching:
                raise AVError("Evidence and claim have no common source")
            if e["modality"] in {"audio", "motion"}:
                if any("stream_index" in loc and loc["stream_index"] != e["locator"]["stream_index"] for loc in matching):
                    raise AVError("Evidence stream differs from its claim locator")
            if e["modality"] == "visual_stills":
                points = e["locator"]["points_seconds"]
                if not any(any(a <= p < b for a,b in loc["intervals_seconds"]) for p in points for loc in matching):
                    raise AVError("Still evidence does not occur in the claim interval")
            elif not any(not missing_intervals(e["locator"]["intervals_seconds"], loc["intervals_seconds"]) for loc in matching):
                raise AVError("Evidence interval lies outside its claim locator")
            credible_layer = e["layer"] not in {"inference", "interpretation"}
            if credible_layer:
                supported_modalities.add(e["modality"])
            blockers.update(e["quality_flags"])
            present.append(e)
        if kind in {"motion", "editing"} and not any(e["modality"] == "motion" and e["layer"] == "observed" for e in present):
            blockers.add("MOTION_NOT_VERIFIED")
        if kind in {"delivery", "speaker_identity"} and not any(e["layer"] == "auditory_observation" for e in present):
            blockers.add("AUDITORY_OBSERVER_UNAVAILABLE")
        if kind == "speech_latency" and not any(e.get("boundary_origin") in {"manual_verified", "forced_aligned_verified"} for e in present):
            blockers.add("ALIGNMENT_UNCERTAIN")
        if kind == "pitch" and not any(e.get("speaker_isolation_verified") is True for e in present):
            blockers.add("SPEAKER_ISOLATION_NOT_ESTABLISHED")
        missing = sorted(set(required)-supported_modalities)
        material_blockers = sorted(blockers & GATES.get(kind, {"COVERAGE_GAP", "UNMAPPED_EXTERNAL_SOURCE"}))
        ready = not missing and not material_blockers and bool(supporting)
        adjudication = c.get("adjudication")
        status = "PROVISIONAL" if ready else "OPEN"
        if adjudication is not None:
            if not isinstance(adjudication, dict) or adjudication.get("status") not in {"SUPPORTED", "PROVISIONAL", "OPEN", "CONTRADICTED"}:
                raise AVError("Invalid adjudication status")
            text_value(adjudication.get("reviewer"), "adjudicator")
            text_value(adjudication.get("reason"), "adjudication reason")
            if adjudication["status"] == "SUPPORTED" and not ready:
                raise AVError("SUPPORTED claim lacks required evidence or has unresolved quality gates")
            if adjudication["status"] == "CONTRADICTED" and not counter:
                raise AVError("CONTRADICTED claim needs counterevidence")
            status = adjudication["status"]
        for name in ("inference", "interpretation", "confidence", "alternatives"):
            if name not in c:
                raise AVError(f"Claim must visibly separate {name}")
        if c["confidence"] not in {"high", "medium", "low", "unknown"} or not isinstance(c["alternatives"], list):
            raise AVError("Confidence and alternative readings are malformed")
        rows.append(dict(c, status=status, quality_flags=sorted(blockers), missing_modalities=missing,
                         blocking_flags=material_blockers, evidence_ready=ready))
    return rows, witnesses, list(dependencies.values())


def _next_step(claim, capabilities):
    kind = claim["kind"]
    if claim["status"] in {"SUPPORTED", "CONTRADICTED"}:
        return "STOP", "The claim has a recorded adjudication; preserve its alternatives"
    if kind == "mixed_energy":
        return "CONTOUR_QUERY", "Query retained mixed RMS windows before decoding again"
    if kind in {"pitch", "speech_latency"}:
        return ("VERIFY_BOUNDARIES" if kind == "speech_latency" else "VALIDATE_PITCH_SOURCE"), "Confirm segment authority, interference and speaker isolation before baseline admission"
    if kind in {"motion", "editing"}:
        return "BOUNDED_VIDEO_SEQUENCE", "Inspect a continuous window and original-PTS cut candidates"
    if kind == "visual_fact":
        return "STILL_INSPECTION", "Inspect source-bound point evidence; escalate only if temporal change matters"
    if kind == "recording_identity":
        return "REUSE_COMPARE", "Use conservative recording correspondences; nonmatch stays unresolved"
    if kind == "musical_relation":
        return "MUSICAL_RELATION_REVIEW", "Compare ordered phrases and lyrics separately from take identity"
    if kind in {"delivery", "speaker_identity"}:
        if capabilities.get("auditory_observer", {}).get("status") == "PROBE_PASSED":
            return "BOUNDED_AUDITORY_OBSERVER", "Use a context-minimized observer question with source-bound clips"
        return "HUMAN_LISTENING", "Ask one consequential listening comparison using the existing review form"
    return "SOURCE_REVIEW", "Adjudicate the atomic claim against its sources and counterevidence"


def plan_claims(claims, capabilities):
    requests = []
    for c in claims:
        route, why = _next_step(c, capabilities)
        if route == "STOP":
            continue
        budget = c.get("budget", {"max_clip_seconds": 30, "max_observer_calls": 1, "max_wall_seconds": 120, "remote_cost_usd": 0})
        if not isinstance(budget, dict) or any(type(v) not in (int, float) or v < 0 for v in budget.values()):
            raise AVError("Evidence budget must contain nonnegative numeric limits")
        requests.append({"request_id": "request-" + c["id"], "claim_id": c["id"], "question": c.get("question", c["statement"]),
            "required_modalities": c["required_modalities"], "locators": c["locators"], "route": route, "routing_reason": why,
            "alternatives": c["alternatives"], "current_evidence": c.get("evidence_ids", []),
            "missing_modalities": c["missing_modalities"], "blocking_flags": c["blocking_flags"],
            "decision_impact": c.get("decision_impact", "unspecified"), "priority": c.get("priority", "medium"),
            "transcript_allowed_first_pass": False, "budget": budget, "execution_status": "PLANNED_NOT_EXECUTED"})
    return sorted(requests, key=lambda r: {"high": 0, "medium": 1, "low": 2}.get(r["priority"], 1))


def render_claim_report(path, claims, witnesses, requests):
    e = html.escape
    pieces = ["<!doctype html><meta charset='utf-8'><title>Claim evidence review</title>",
        "<style>body{font:16px system-ui;max-width:1000px;margin:2em auto;padding:1em;line-height:1.5}article{border-top:1px solid #aaa;margin:2em 0}code{overflow-wrap:anywhere}pre{white-space:pre-wrap}</style>",
        "<h1>Claim evidence review</h1><p>Measurements, attributed observations and interpretations remain separate. Validation checks recorded evidence; it does not certify perceptual truth.</p>"]
    for c in claims:
        pieces += [f"<article><h2>{e(c['id'])} · {e(c['status'])}</h2><p>{e(c['statement'])}</p>",
                   f"<p>Confidence: {e(c['confidence'])} (reviewer judgment).</p>"]
        for layer, title in (("textual", "Textual evidence"), ("observed", "Observed"), ("measured", "Measured"), ("auditory_observation", "Auditory observation")):
            pieces.append(f"<h3>{title}</h3><ul>")
            group = [witnesses[i] for i in c.get("evidence_ids", []) if witnesses[i]["layer"] == layer]
            for item in group:
                link = item.get("portable_artifact")
                witness_link = f" · <a href='{e(link,quote=True)}'>Evidence artifact</a>" if link else ""
                pieces.append(f"<li>{e(item['statement'])}{witness_link}<br><code>{e(str(item['locator']))}</code></li>")
            pieces.append("</ul>" if group else "<li>OPEN — no witness in this layer.</li></ul>")
        for key in ("inference", "interpretation", "alternatives", "quality_flags", "counterevidence_ids"):
            pieces.append(f"<h3>{e(key.replace('_', ' ').capitalize())}</h3><p>{e(str(c[key]))}</p>")
        for item in requests:
            if item["claim_id"] == c["id"]:
                pieces.append(f"<p>Next evidence step: <strong>{e(item['route'])}</strong>. {e(item['routing_reason'])}</p>")
        pieces.append("</article>")
    Path(path).write_text("\n".join(pieces), encoding="utf-8")


def audit_claims(config, output, *, capabilities=None, filter_flags=()):
    config = Path(config).resolve()
    data = read_json(config)
    claims, witnesses, deps = validate_claims(data, config.parent)
    selected_flags = set(flags(list(filter_flags)))
    if selected_flags:
        claims = [c for c in claims if selected_flags & set(c["quality_flags"])]
    capability = read_json(capabilities).get("capabilities", {}) if capabilities else {}
    requests = plan_claims(claims, capability)
    deps += [file_record(config)] + ([file_record(capabilities)] if capabilities else [])
    with output_transaction(output, [config] + ([capabilities] if capabilities else [])) as stage:
        portable = stage/"witnesses"
        portable.mkdir()
        copied = set()
        for item in witnesses.values():
            path = Path(item["artifact"]["path"])
            path = path.resolve() if path.is_absolute() else (config.parent/path).resolve()
            if path.stat().st_size <= 16777216:
                name = item["artifact"]["sha256"][:16]+path.suffix.lower()
                if name not in copied:
                    shutil.copyfile(path,portable/name); copied.add(name)
                item["portable_artifact"] = "witnesses/"+name
            else:
                item["portable_artifact"] = None
                item["relocation_requirement"] = "External large witness; resolve the full SHA-256 within owner-specified roots"
        report = {"schema": "ave.claim-audit.v1", "claims": claims, "evidence": list(witnesses.values()),
                  "requests": requests, "source_config_sha256": sha256(config), "truth_certification": "NOT_ESTABLISHED"}
        write_json(stage / "claim-audit.json", report)
        render_claim_report(stage / "report.html", claims, witnesses, requests)
        return finish_run(stage, "evidence-plan", deps, metadata={"result_file": "claim-audit.json", "view_file": "report.html",
            "claim_count": len(claims), "open_count": sum(c["status"] == "OPEN" for c in claims), "request_count": len(requests)})
