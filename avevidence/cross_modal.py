"""Proposition-specific competence and explicit, separate adjudication."""
from __future__ import annotations

import copy
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, write_json
from .event_contracts import read_event_json, span
from .mapping import missing_intervals
from .observation_dependencies import PROPAGATING, dependence_groups, propagate_changes, material_edge
from .scene_packets import load_scene_packet, packet_identity, verify_scene_snapshot

COMPETENCE = {
    "TXT": {"wording"}, "VIS": {"visual_fact"},
    "TVIS": {"visual_fact", "motion", "contact", "event_order", "av_sync"},
    "AM": {"sound_level", "speech_timing"},
    "MID": {"recording_identity", "composition_identity", "arrangement_identity", "performance_identity", "musical_reference"},
    "AO_STAGE1": {"wording", "delivery", "nonverbal_vocal", "music_structure", "performance_music", "soundscape", "speech_timing", "auditory_event_timing"},
    "AO_STAGE2": {"wording", "delivery", "nonverbal_vocal", "music_structure", "performance_music", "soundscape", "speech_timing", "auditory_event_timing"},
    "HL": {"delivery", "nonverbal_vocal", "music_structure", "performance_music", "soundscape", "speaker_identity", "speech_timing", "auditory_event_timing"},
    "VO": {"visual_fact", "motion", "contact", "event_order"},
    "AVO": {"visual_fact", "motion", "contact", "event_order", "av_sync", "delivery", "soundscape"},
    "INF": set(), "INT": set(),
}
TASK_KINDS = {"SPEECH_PERFORMANCE": {"delivery", "wording", "speech_timing"}, "NONVERBAL_VOCAL": {"nonverbal_vocal"},
              "MUSIC_STRUCTURE": {"music_structure"}, "PERFORMANCE_MUSIC": {"performance_music", "music_structure"},
              "SOUNDSCAPE": {"soundscape"}, "AV_SYNC": {"soundscape", "speech_timing"}}
TASK_KINDS.update({"SPEECH_DELIVERY": {"delivery"}, "AUDITORY_EVENT_TIMING": {"auditory_event_timing"},
                   "LEXICAL_TRANSCRIPTION": {"wording"}, "EXACT_WORD_TIMING": {"speech_timing"}})
GUARDS = ["Text cannot establish performed pitch, strain or breathiness",
          "Still images cannot establish motion, contact order or singing identity",
          "Mixed-track RMS cannot establish emotional intensity",
          "Subtitle boundaries are not phonetic boundaries",
          "Contextual Stage 2 is dependent evidence; model output is not a direct source fact"]


def evidence_assessment(ref, binding, proposition, interval, *, observation=None):
    channel = ref["channel"]
    competent = proposition in COMPETENCE[channel]
    result = {"evidence_id": ref["id"], "channel": channel, "competent": competent,
              "adequate_for_support": False, "authority": "NOT_COMPETENT" if not competent else "ATTRIBUTED_UNVALIDATED",
              "reasons": [], "global_qualification": False}
    if not competent:
        result["reasons"].append("This channel cannot establish this exact proposition")
        return result
    loc = ref["locator"]
    if channel != "VIS" and missing_intervals([interval], loc.get("intervals_seconds", [])):
        result["reasons"].append("Evidence locator does not cover this observation interval")
        return result
    if channel == "TXT":
        result.update(authority=ref.get("authority", "textual_witness"),
                      adequate_for_support=ref.get("authority") in {"canonical_text", "best_available_text"})
        result["reasons"].append("Text authority is an attributed project declaration; byte hashes do not authenticate a translation")
    elif channel == "MID":
        from .music_identity.workflow import load_music_run
        from .music_identity.review import decision_support, validate_decisions
        folder, raw = load_music_run(Path(binding["path"]).parent)
        from .common import sha256
        if sha256(folder / "music-identity.json") != ref["artifact"]["sha256"]:
            raise AVError("MID must cite the actual verified identity artifact, not another file in its run")
        validate_decisions(raw)
        src = raw["source"]
        if (src["sha256"] != loc["source_sha256"] or src["audio_stream"] != loc["stream_index"] or src["clock"] != loc["clock"]
            or missing_intervals(loc.get("intervals_seconds", []), [src["interval_seconds"]])):
            raise AVError("Music identity source/stream/clock/scope differs from its scene locator")
        level = {"recording_identity": "RECORDING", "composition_identity": "COMPOSITION", "arrangement_identity": "ARRANGEMENT",
                 "performance_identity": "PERFORMANCE", "musical_reference": "PHRASE"}[proposition]
        decisions = [d for d in raw["adjudication"] if d["level"] == level]
        if len(decisions) != 1:
            raise AVError("Music proposition needs a unique level-specific adjudication")
        decision = decisions[0]
        adequate, reason = decision_support(raw, decision)
        result.update(authority="ATTRIBUTED_MUSIC_IDENTITY_ADJUDICATION", identity_level=level, identity_status=decision["status"],
            adequate_for_support=bool(adequate and decision["status"] in {"SUPPORTED", "CONFIRMED"}))
        result["reasons"].append(reason)
    elif channel == "VIS":
        result["authority"] = "STATIC_EVIDENCE_REQUIRES_SOURCE_BOUND_REVIEW"
        if not any(interval[0] <= point < interval[1] for point in loc.get("points_seconds", [])):
            result["reasons"].append("No identified static source frame lies inside this observation interval")
            return result
        if binding.get("review_path"):
            from .visual import load_verified_run
            _, _, raw, _, _ = load_verified_run(Path(binding["path"]).parent, {"extract_frames"})
            review = read_event_json(binding["review_path"])
            if not isinstance(review,dict): raise AVError("Static review must be an attributed object")
            if (raw.get("schema") != "ave.frames.v1" or raw.get("parent_source_sha256") != loc["source_sha256"]
                or raw.get("parent_stream_index") != loc.get("stream_index")):
                raise AVError("Static frame inventory source/stream binding differs")
            selected = [r for r in raw["rows"] if r["source_seconds"] in loc.get("points_seconds", [])]
            if not selected or len({r["source_seconds"] for r in selected}) != len(loc.get("points_seconds", [])):
                raise AVError("Static locator must identify actual source-frame points in this verified inventory")
            result["adequate_for_support"] = bool(review.get("schema") == "ave.static-review.v1"
                and review.get("actual_visual_inspection_declared") is True and review.get("reviewer")
                and review.get("source_sha256") == loc["source_sha256"] and review.get("stream_index") == loc["stream_index"]
                and review.get("frame_inventory_sha256") == ref["artifact"]["sha256"]
                and {r["frame_id"] for r in selected} <= set(review.get("inspected_frame_ids", []))
                and review.get("observation"))
        result["reasons"].append("Only source-bound reviewed static facts are eligible; generated or unbound images stay provisional")
    elif channel == "TVIS":
        raw = read_event_json(binding["path"])
        if (not isinstance(raw, dict) or raw.get("schema") != "ave.temporal-inspection.v1"
            or raw.get("source_sha256") != loc["source_sha256"] or raw.get("stream_index") != loc["stream_index"]):
            raise AVError("Temporal evidence report differs from its source/stream binding")
        result["authority"] = "GENERATED_NOT_REVIEWED"
        if not binding.get("review_path"):
            result["reasons"].append("Temporal samples were generated, but no attributed sequence review is bound")
            return result
        from .temporal_inspection import validate_temporal_review
        assessment = read_event_json(binding["review_path"])
        if not isinstance(assessment,dict): raise AVError("Temporal assessment must be an object")
        recomputed = validate_temporal_review(Path(binding["path"]).parent, assessment.get("declaration"))
        if recomputed != assessment:
            raise AVError("Temporal review assessment differs from its verified declaration")
        adequate = assessment.get("adequate_interval_seconds")
        exact_types = {"contact": {"EXACT_CONTACT", "CONTACT_ORDER"}, "event_order": {"CONTACT_ORDER"}, "av_sync": {"AV_SYNC"}}
        required_type = proposition not in exact_types or assessment.get("question_type") in exact_types[proposition]
        result.update(authority="ATTRIBUTED_TEMPORAL_REVIEW", adequate_for_support=bool(
            assessment.get("status") == "ADEQUATE_DECLARED" and required_type and adequate
            and not missing_intervals([interval], [adequate])))
        result["temporal_status"] = "FRAME_COMPLETE_CONFIRMED" if assessment.get("complete_critical_source_frame_coverage_declared") else "FRAME_SEQUENCE_CONFIRMED"
        result["reasons"].append("Coverage and declaration verified; this does not certify the interpretation is true")
    elif channel in {"AO_STAGE1", "AO_STAGE2"}:
        raw = read_event_json(binding["path"])
        profile = raw.get("task_profile", "SPEECH_PERFORMANCE")
        result["competent"] = proposition in TASK_KINDS.get(profile, set())
        result.update(authority="NON_AUTHORITATIVE_LEXICAL_GUESS" if proposition == "wording" else "PROVISIONAL_MODEL_REPORT",
                      task_profile=profile, validation_status=raw.get("validation_status"),
                      task_capability_status=raw.get("task_capability_status", "UNQUALIFIED"),
                      evidence_class=channel, context_contaminated=channel == "AO_STAGE2")
        result["reasons"].append("Structured validity is separate from perceptual accuracy; no task was globally qualified by this iteration")
        if raw.get("validation_status") != "VALID" or raw.get("observer_type") == "mock":
            result["competent"] = False
            result["reasons"].append("Invalid output or a test double is not perceptual evidence")
        if binding.get("review_path") and raw.get("admission_dimensions"):
            _scoped_auditory_assessment(result, raw, ref, binding, proposition, interval, observation)
    elif channel == "HL":
        raw = read_event_json(binding["path"])
        if not isinstance(raw,dict): raise AVError("Human listening record must be an attributed object")
        # A scoped accuracy review reports its own judgment, not a new
        # independently transcribed vocal observation or backend qualification.
        result["authority"] = "SCOPED_HUMAN_JUDGMENT"
        if raw.get("schema") == "ave.scoped-human-auditory-review.v1":
            scopes = [r for r in raw.get("inspected_intervals", []) if r.get("source_sha256") == loc["source_sha256"] and r.get("stream_index") == loc["stream_index"]]
            result["adequate_for_support"] = False
            result["reasons"].append("Broad model-accuracy review is not a proposition-specific listening observation; supply the exact scoped human observation separately")
        elif raw.get("schema") == "ave.human-listening-observation.v1":
            result["adequate_for_support"] = bool(raw.get("actual_audio_inspection_declared") is True
                and raw.get("reviewer") and raw.get("source_sha256") == loc["source_sha256"]
                and raw.get("stream_index") == loc["stream_index"] and raw.get("proposition") == proposition
                and raw.get("observation") and not missing_intervals([interval], raw.get("inspected_intervals_seconds", [])))
        result["reasons"].append("Supplied listening declaration is local to these intervals; it never qualifies the backend globally")
    elif channel == "AM":
        raw = read_event_json(binding["path"])
        if not isinstance(raw,dict): raise AVError("Acoustic record must be an object")
        source_digest = raw.get("source_sha256", raw.get("parent_source_sha256"))
        if source_digest != loc["source_sha256"]:
            raise AVError("Acoustic measurement source binding differs")
        result["authority"] = "DETERMINISTIC_MEASUREMENT_REQUIRES_VERIFIED_RUN"
        if raw.get("schema") in {"ave.audio_metrics.v1", "ave.audio_tracks.v1"}:
            from .visual import load_verified_run
            _, manifest, verified, _, _ = load_verified_run(Path(binding["path"]).parent, {"measure_audio", "audio_track"})
            if verified != raw or (raw.get("parent_stream_index") != loc["stream_index"] or raw.get("clock") != loc["clock"]):
                raise AVError("Acoustic measurement run/clock/stream binding differs")
            selection = raw.get("selection", {})
            coverage = [[p["source_start_seconds"], p["source_end_seconds"]] for p in selection.get("parts", [])]
            result["adequate_for_support"] = bool(proposition == "sound_level" and selection.get("status") == "COMPLETE"
                and not missing_intervals([interval], coverage) and (raw.get("measurements") or
                    any(s.get("channels") for s in raw.get("segments", []))))
        else:
            result["reasons"].append("This generic or unsupported measurement record cannot close a claim")
        result["reasons"].append("Level describes this track/measurement; speech timing needs separately reviewed alignment boundaries")
    else:
        result["reasons"].append("Optional model visual/AV reports are attributed and unqualified; no backend was added")
    return result


def _scoped_auditory_assessment(result, raw, ref, binding, proposition, interval, observation):
    from .auditory_claims import validate_model_observation
    from .auditory_qualification import TASK_PROPOSITIONS, competence, load_qualification
    from .event_contracts import object_fields
    from .common import sha256
    from .scene_packets import resolve_artifact
    from .inventory import text_value
    # Collection integrity is necessary, but never sufficient for support.
    try:
        _, covered, _ = validate_model_observation(binding["path"], allow_experimental=True)
    except AVError as exc:
        result["reasons"].append(str(exc)); result["competent"] = False
        return
    review = read_event_json(binding["review_path"])
    keys = {"schema", "observation_sha256", "observation_index", "proposition", "interval_seconds",
            "reviewer", "scope", "statement", "adequacy", "unresolved_contradictions", "reason", "qualification_artifact"}
    object_fields(review, keys, keys, "scoped auditory observation assessment")
    if (review["schema"] != "ave.auditory-observation-assessment.v1" or
        review["observation_sha256"] != sha256(binding["path"]) or review["proposition"] != proposition):
        raise AVError("Auditory assessment differs from this exact observation/proposition")
    for key in ("reviewer", "scope", "statement", "reason"):
        text_value(review[key], key)
    from .event_contracts import strings, span, choice
    strings(review["unresolved_contradictions"], "unresolved auditory contradictions", 100)
    span(review["interval_seconds"])
    choice(review["adequacy"], {"ELIGIBLE_FOR_SUPPORT", "INADEQUATE", "RECHECK"}, "individual auditory adequacy")
    index = review["observation_index"]
    if type(index) is not int or not 0 <= index < len(covered):
        raise AVError("Auditory assessment requires an actual atomic observation index")
    if ref.get("record_pointer") not in {f"/source_observations/{index}", f"/parsed/observations/{index}"}:
        raise AVError("Scoped support must select the exact atomic timed observation")
    if (review["interval_seconds"] != interval or missing_intervals([interval], [covered[index]])
        or observation is None or review["statement"] != observation["statement"]):
        raise AVError("Auditory assessment cannot widen or relabel the actual observation's declared scope")
    qpath = resolve_artifact(review["qualification_artifact"], Path(binding["review_path"]).parent)
    task = competence(raw["task_profile"])
    qualification = load_qualification(qpath, identity=raw["backend_identity"], task=task,
        media_category=raw["media_category"], configuration_revision=raw["backend_identity"]["configuration_revision"])
    # Hold nested proof bytes stable through publication, as for scene evidence.
    qfolder = qpath if qpath.is_dir() else qpath.parent
    binding["qualification_proof"] = {"path": str(qfolder), "manifest_sha256": sha256(qfolder/"run.json")}
    if raw["backend_identity"].get("route_type") == "hosted" and qualification["returned_model"] != raw.get("provider_receipt", {}).get("returned_model"):
        raise AVError("Scoped qualification belongs to another provider-reported model revision")
    competent = proposition in TASK_PROPOSITIONS[task]
    result.update(competent=competent, task_capability_status=qualification["status"],
        qualification_scope=qualification["scope_description"], observation_scope=review["scope"],
        authority="SCOPED_QUALIFIED_AUDITORY_WITNESS", admission_status="ELIGIBLE_FOR_SUPPORT" if competent and
        review["adequacy"] == "ELIGIBLE_FOR_SUPPORT" and not review["unresolved_contradictions"] else "INADEQUATE",
        adequate_for_support=bool(competent and review["adequacy"] == "ELIGIBLE_FOR_SUPPORT" and not review["unresolved_contradictions"]))
    result["reasons"].append("Qualification and individual review verified; claim support still requires separate explicit adjudication")
    if ref["channel"] == "AO_STAGE2" and proposition == "wording":
        result.update(adequate_for_support=False, admission_status="CONTEXT_ONLY_FOR_SUPPLIED_WORDING")
        result["reasons"].append("Contextual reinspection cannot independently prove words supplied in its prompt")


def reconcile_data(packet, evidence, nodes, bindings):
    observations = {}
    decisions = copy.deepcopy(packet["adjudication"]["decisions"])
    for row in packet["observations"]:
        interval = row.get("interval_seconds", packet["interval_seconds"])
        assessments = [evidence_assessment(evidence[eid], bindings[eid], row["proposition"], interval, observation=row) for eid in row["evidence_ids"]]
        adequate = any(a["adequate_for_support"] for a in assessments)
        if "evidence_roles" in row:
            for assessment in assessments:
                assessment["role"] = row["evidence_roles"][assessment["evidence_id"]]
            positive = [a for a in assessments if a["role"] in {"REQUIRED", "SUPPORTING"}]
            adequate = (any(a["adequate_for_support"] for a in positive)
                        and all(a["adequate_for_support"] for a in positive if a["role"] == "REQUIRED")
                        and not any(a["adequate_for_support"] for a in assessments if a["role"] == "CONTRADICTORY"))
        observations[row["id"]] = {"proposition": row["proposition"], "evidence_assessments": assessments,
                                  "competent_evidence_available": any(a["competent"] for a in assessments),
                                  "adequate_for_support": adequate,
                                  "independence": dependence_groups([row["id"]], nodes, packet["dependencies"], evidence)}
        proposed = decisions.get(row["id"], {}).get("state", row["status"])
        if proposed in {"SUPPORTED", "CONFIRMED"} and (not adequate or row["id"] not in decisions):
            decisions[row["id"]] = {"state": "OPEN", "reason": "Affirmative observation needs competent adequate evidence and explicit attributed adjudication"}
    # Assess every rival before resolving conflicts. A nominal affirmative
    # decision may have failed adequacy or a material prerequisite; neither
    # observation order nor rejecting one rival settles the remaining rivals.
    affirmative = {"SUPPORTED", "CONFIRMED"}
    for _ in range(len(observations) + 1 if packet["conflicts"] else 0):
        preliminary = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"], decisions)
        effective = {key: "RECHECK" if value["changed_premises"] and value["after"] in affirmative else value["after"]
                     for key, value in preliminary["state_overlay"].items()}
        for _ in range(len(nodes)):
            before = effective.copy()
            for edge in packet["dependencies"]:
                if material_edge(edge) and effective[edge["to"]] in affirmative and effective[edge["from"]] not in affirmative:
                    effective[edge["to"]] = "RECHECK"
            if effective == before:
                break
        changed = False
        for conflict in packet["conflicts"]:
            ids = conflict["observation_ids"]
            resolved = (all(key in packet["adjudication"]["decisions"] and
                            effective[key] in affirmative | {"CONTRADICTED"} for key in ids)
                        and any(effective[key] == "CONTRADICTED" for key in ids))
            if resolved:
                continue
            for key in ids:
                observed = observations[key]
                for assessment in observed["evidence_assessments"]:
                    if assessment.get("admission_status") == "ELIGIBLE_FOR_SUPPORT":
                        assessment.update(adequate_for_support=False, admission_status="INADEQUATE")
                        assessment["reasons"].append("Unresolved same-proposition packet conflict prevents scoped auditory support")
                if any(a.get("authority") == "SCOPED_QUALIFIED_AUDITORY_WITNESS" for a in observed["evidence_assessments"]):
                    observed["adequate_for_support"] = False
                    if decisions.get(key, {}).get("state") in affirmative:
                        decisions[key] = {"state": "OPEN", "reason": "A competing auditory observation remains unresolved"}
                        changed = True
        if not changed:
            break
    overlay = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"], decisions)
    # An affirmative dependent decision cannot silently override a changed
    # premise. Re-formulate/review it in a subsequent packet/delta instead.
    for key, state in overlay["state_overlay"].items():
        if state["changed_premises"] and state["after"] in {"SUPPORTED", "CONFIRMED"}:
            state.update(after="RECHECK", reason="Explicit affirmative decision cannot override a changed premise in this reconciliation")
    claim_rows = []
    for row in packet["claims"]:
        premise_states = {oid: overlay["state_overlay"][oid]["after"] for oid in row["observation_ids"]}
        inadequate = [oid for oid in row["observation_ids"] if not observations[oid]["adequate_for_support"]]
        state = overlay["state_overlay"][row["id"]]
        routes = None
        if "support_routes" in row:
            from .support_routes import assess_routes
            routes = assess_routes(row, observations, overlay["state_overlay"], packet, evidence)
            selected = {key for r in routes if r["status"] == "SURVIVES" for key in r["selected_essential"]}
            inadequate = [key for key in selected if not observations[key]["adequate_for_support"]]
            if state["after"] in {"SUPPORTED", "CONFIRMED"} and (
                    not any(r["status"] == "SURVIVES" for r in routes) or
                    row["id"] not in packet["adjudication"]["decisions"] or
                    row["proposition"] in {"emotion", "interpretation", "speaker_identity"} and not (row.get("inference") or row.get("interpretation"))):
                state.update(after="OPEN", reason="No adequate declared support route or explicit articulated adjudication")
        elif state["after"] in {"SUPPORTED", "CONFIRMED"}:
            reasoning = row.get("inference") or row.get("interpretation")
            direct_fact = row["proposition"] not in {"interpretation", "emotion"}
            claim_interval = row.get("interval_seconds",packet["interval_seconds"])
            same_kind = any(observations[oid]["proposition"] == row["proposition"] and observations[oid]["adequate_for_support"]
                and not missing_intervals([claim_interval],[nodes[oid]["record"].get("interval_seconds",packet["interval_seconds"])])
                and any(a["adequate_for_support"] and (a["channel"] != "VIS" or any(
                    claim_interval[0] <= point < claim_interval[1]
                    for point in evidence[a["evidence_id"]]["locator"].get("points_seconds", [])))
                    for a in observations[oid]["evidence_assessments"])
                for oid in row["observation_ids"])
            if (row["id"] not in packet["adjudication"]["decisions"] or inadequate or direct_fact and not same_kind
                or any(s in {"CONTRADICTED", "OPEN", "RECHECK", "REPORTED", "PROVISIONAL"} for s in premise_states.values())
                or row["proposition"] in {"emotion", "interpretation", "speaker_identity"} and not reasoning):
                state.update(after="OPEN", reason="Claim lacks explicit adjudication, adequate premises, or articulated inference")
        claim_rows.append({"id": row["id"], "statement": row["statement"], "proposition": row["proposition"],
                           "state": state["after"], "premise_states": premise_states, "inadequate_premises": inadequate,
                           "independence": dependence_groups(row["observation_ids"], nodes, packet["dependencies"], evidence),
                           **({"support_routes": routes, "surviving_scopes": [r["scope"] for r in routes if r["status"] == "SURVIVES"]} if routes is not None else {})})
    # Propagate adequacy downgrades introduced by this stage through claim ->
    # claim edges as well, rather than leaving descendants apparently supported.
    final_changes = {key: {"state": value["after"], "reason": value["reason"]}
                     for key, value in overlay["state_overlay"].items() if value["after"] != value["before"]}
    if final_changes:
        propagated = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"], final_changes)
        for key, value in propagated["state_overlay"].items():
            if value["changed_premises"] and overlay["state_overlay"][key]["after"] in {"SUPPORTED", "CONFIRMED"}:
                overlay["state_overlay"][key].update(after="RECHECK", reason="A transitively dependent premise failed adequacy", changed_premises=value["changed_premises"])
        for row in claim_rows:
            row["state"] = overlay["state_overlay"][row["id"]]["after"]
    # Every declared propagating prerequisite gates an affirmative node, even
    # when the unresolved prerequisite was already OPEN/PROVISIONAL and never
    # changed. Observation lists are not a bypass around the explicit DAG.
    prerequisites = {key: set() for key in nodes}
    for edge in packet["dependencies"]:
        if material_edge(edge):
            prerequisites[edge["to"]].add(edge["from"])
    affirmative = {"SUPPORTED", "CONFIRMED"}
    for _ in range(len(nodes)):
        changed = False
        for key, state in overlay["state_overlay"].items():
            if state["after"] not in affirmative:
                continue
            unresolved = sorted(parent for parent in prerequisites[key]
                if overlay["state_overlay"][parent]["after"] not in affirmative)
            if unresolved:
                recheck = bool(state["changed_premises"] or any(
                    overlay["state_overlay"][parent]["after"] == "RECHECK" for parent in unresolved))
                state.update(after="RECHECK" if recheck else "OPEN",
                    reason="Unresolved declared prerequisite: " + ", ".join(unresolved))
                changed = True
        if not changed:
            break
    for row in claim_rows:
        row["state"] = overlay["state_overlay"][row["id"]]["after"]
        premise_ids = set(row["premise_states"]) | prerequisites[row["id"]]
        row["premise_states"] = {key: overlay["state_overlay"][key]["after"] for key in sorted(premise_ids)}
    overlay["recheck_ids"] = sorted(key for key,row in overlay["state_overlay"].items() if row["after"] == "RECHECK")
    overlay["unaffected_ids"] = [key for key in overlay["unaffected_ids"]
        if overlay["state_overlay"][key]["after"] == overlay["state_overlay"][key]["before"]]
    conflicts = []
    for conflict in packet["conflicts"]:
        ids = conflict["observation_ids"]
        authority = [{"observation_id": oid, "evidence": observations[oid]["evidence_assessments"]} for oid in ids]
        text = [oid for oid in ids if any(a["authority"] in {"canonical_text", "best_available_text"} for a in observations[oid]["evidence_assessments"])]
        recommendation = "Prefer the stronger textual witness for wording; adjudicate the lexical item separately from delivery" if conflict["proposition"] == "wording" and text else "Use proposition-specific competent evidence and preserve disagreement pending explicit review"
        dependents = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"], {oid: {"state": "RECHECK", "reason": "Assess downstream scope"} for oid in ids})["recheck_ids"]
        conflicts.append(dict(conflict, authority=authority, independence=dependence_groups(ids, nodes, packet["dependencies"], evidence),
                              recommendation=recommendation, preferred_text_observations=text,
                              potentially_affected_nodes=dependents, resolution="EXPLICIT_ADJUDICATION_REQUIRED"))
    return {"schema": "ave.scene-reconciliation.v1", "packet_id": packet_identity(packet), "scene_id": packet["scene_id"],
            "source_sha256": packet["source"]["sha256"], "interval_seconds": packet["interval_seconds"],
            "observations": observations, "claims": claim_rows, "conflicts": conflicts,
            "dependency_assessment": overlay, "adjudication": copy.deepcopy(packet["adjudication"]),
            "holistic_analysis": packet.get("holistic_analysis"), "open_questions": packet["open_questions"],
            "proposition_guards": GUARDS, "no_majority_vote": True, "raw_records_preserved": True,
            "source_fact_promotion": "NONE_TOOLKIT_DOES_NOT_ASSIGN_PROJECT_F-AUD",
            "event_synthesis": {"formulation": packet.get("holistic_analysis"),
                "established_claim_ids": [r["id"] for r in claim_rows if r["state"] in {"SUPPORTED", "CONFIRMED"}],
                "requires_correction": [r["id"] for r in claim_rows if r["state"] in {"RECHECK", "CONTRADICTED"}],
                "open_claim_ids": [r["id"] for r in claim_rows if r["state"] in {"OPEN", "PROVISIONAL", "REPORTED"}],
                "alternatives": {r["id"]: r.get("alternatives", []) for r in packet["claims"]},
                "authority": "ATTRIBUTED_ANALYST_EVENT_INTERPRETATION"},
            "scope": "Auditable analyst decisions and evidence adequacy; no automated literary truth or native hearing/video perception"}


def reconcile_scene(input, output, *, relocations=None):
    packet, evidence, nodes, bindings, snapshot, _ = load_scene_packet(input, relocations=relocations)
    result = reconcile_data(packet, evidence, nodes, bindings)
    verify_scene_snapshot(snapshot,bindings)
    inputs = [input]+[r["path"] for r in bindings.values()]+[r["review_path"] for r in bindings.values() if "review_path" in r]
    with output_transaction(output, inputs) as stage:
        write_json(stage/"reconciliation.json", result)
        verify_scene_snapshot(snapshot,bindings)
        return finish_run(stage, "scene-reconcile", [snapshot]+[file_record(p, "scene_evidence") for p in inputs[1:]],
                          metadata={"result_file": "reconciliation.json", "packet_id": result["packet_id"], "raw_records_preserved": True})
