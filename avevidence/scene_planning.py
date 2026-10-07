"""Small cross-modal event queries; planning never invokes an observer."""
from __future__ import annotations

import math

from .common import AVError, finish_run, output_transaction, write_json
from .event_contracts import span
from .inventory import integer, number
from .scene_packets import load_scene_packet, packet_identity, verify_scene_snapshot

ROUTES = {
    "wording": [("TEXT_WITNESS", None)],
    "delivery": [("AUDITORY", "SPEECH_PERFORMANCE")],
    "nonverbal_vocal": [("AUDITORY", "NONVERBAL_VOCAL")],
    "music_structure": [("AUDITORY", "MUSIC_STRUCTURE")],
    "performance_music": [("AUDITORY", "PERFORMANCE_MUSIC"), ("TEMPORAL", "TEMPORAL_HIGH"), ("TEXT_WITNESS", "LYRICS_SECTIONS")],
    "soundscape": [("AUDITORY", "SOUNDSCAPE")],
    "av_sync": [("AUDITORY", "AV_SYNC"), ("TEMPORAL", "TEMPORAL_LOW")],
    "motion": [("TEMPORAL", "TEMPORAL_LOW")],
    "contact": [("TEMPORAL", "TEMPORAL_LOW")],
    "event_order": [("TEMPORAL", "TEMPORAL_LOW")],
    "visual_fact": [("STATIC", None)],
    "sound_level": [("ACOUSTIC_MEASUREMENT", None)],
    "speech_timing": [("ALIGNMENT_REVIEW", None)],
    "speaker_identity": [("SOURCE_ATTRIBUTION_REVIEW", None)],
    "emotion": [("AUDITORY", "SPEECH_PERFORMANCE"), ("TEMPORAL", "TEMPORAL_LOW"), ("TEXT_WITNESS", None)],
    "interpretation": [("PREMISE_REVIEW", None)],
}


def plan_scene_queries(input, output, *, relocations=None, max_requests=60, max_frames=360, max_audio_seconds=120.):
    integer(max_requests, "request ceiling", 1)
    integer(max_frames, "frame ceiling")
    number(max_audio_seconds, "audio seconds ceiling")
    if max_requests > 200 or max_frames > 10000 or max_audio_seconds > 1200:
        raise AVError("Planner ceilings exceed the bounded event policy")
    packet_data = load_scene_packet(input, relocations=relocations)
    packet, _, nodes, bindings, snapshot, _ = packet_data
    from .cross_modal import reconcile_data
    reconciled = reconcile_data(*packet_data[:4])
    states = reconciled["dependency_assessment"]["state_overlay"]
    requests, unresolved, spent_frames, spent_audio, keys = [], [], 0, 0., {}
    for claim in packet["claims"]:
        cid = claim["id"]
        if states[cid]["after"] in {"SUPPORTED", "CONFIRMED"}:
            continue
        bounds = span(claim.get("interval_seconds", packet["interval_seconds"]), packet["interval_seconds"])
        for route, profile in ROUTES[claim["proposition"]]:
            seconds = bounds[1]-bounds[0]
            # Full coherent objects survive; formal boundaries must be supplied
            # before issuing long music requests. Do not carve by arbitrary time.
            if route == "AUDITORY" and seconds > (120 if profile == "PERFORMANCE_MUSIC" else 30):
                unresolved.append({"claim_id": cid, "route": route, "status": "OPEN_REFINE_INTERVAL",
                    "reason": "Supply a consequential microinterval or explicit formal performance-section boundary",
                    "source_object_interval_seconds": bounds})
                continue
            question_type = "AV_SYNC" if claim["proposition"] == "av_sync" else "EXACT_CONTACT" if claim["proposition"] == "contact" else "CONTACT_ORDER" if claim["proposition"] == "event_order" else "GENERAL_MOTION"
            key = (route, profile, tuple(bounds), question_type if route == "TEMPORAL" else claim["proposition"])
            if key in keys:
                keys[key]["claim_ids"].append(cid)
                keys[key]["analytical_claims"].append(claim["statement"])
                continue
            frames = math.ceil(seconds*({"TEMPORAL_LOW": 2, "TEMPORAL_HIGH": 12}.get(profile, 0))) if route == "TEMPORAL" else 1 if route == "STATIC" else 0
            audio = seconds if route == "AUDITORY" else 0.
            if len(requests) >= max_requests or spent_frames+frames > max_frames or spent_audio+audio > max_audio_seconds:
                unresolved.append({"claim_id": cid, "route": route, "status": "OPEN_RESOURCE_CEILING",
                                   "reason": "Explicit acquisition ceiling exhausted; no certainty is inferred"})
                continue
            request = {"request_id": "event-query-%03d" % (len(requests)+1), "claim_ids": [cid], "route": route,
                       "profile": profile, "source_sha256": packet["source"]["sha256"], "clock": packet["source"]["clock"],
                       "interval_seconds": bounds, "question": claim["statement"], "analytical_claims": [claim["statement"]], "status": "REQUEST_PLANNED_NOT_EXECUTED",
                       "estimated_frame_count": frames, "planned_audio_seconds": audio,
                       "analysis_status": "OPEN", "api_submission_authorized": False}
            if route == "AUDITORY":
                from .auditory_profiles import profile_prompt
                request["question"] = profile_prompt(profile)
                request.update(stage=1, task_profile=profile, context_policy="CONTEXT_MINIMIZED_NEUTRAL_QUESTION_REQUIRED",
                               audio_mode="coherent_section" if seconds > 30 else "microclip",
                               execution_requirement="Separate source-bound observer request, authorization, capability and budget guards remain mandatory")
            if route == "TEMPORAL":
                request.update(question_type=question_type,
                    escalation_policy="Narrow the critical window; inspect 8fps then12fps then every original frame until adequately reviewed or OPEN",
                    generation_does_not_confirm_motion=True)
            keys[key] = request
            requests.append(request)
            spent_frames += frames
            spent_audio += audio
    result = {"schema": "ave.scene-query-plan.v1", "packet_id": packet_identity(packet), "scene_id": packet["scene_id"],
              "requests": requests, "open_requests": unresolved,
              "ceilings": {"max_requests": max_requests, "max_frames": max_frames, "max_audio_seconds": max_audio_seconds},
              "planned_resources": {"frames": spent_frames, "audio_seconds": spent_audio}, "api_spend_usd": 0,
              "execution": "NONE", "scope": "Unresolved claims drive bounded acquisition; analyst interpretation and actual adequacy remain separate"}
    with output_transaction(output, [input]+[r["path"] for r in bindings.values()]) as stage:
        write_json(stage/"query-plan.json", result)
        verify_scene_snapshot(snapshot,bindings)
        return finish_run(stage, "scene-plan", [snapshot], metadata={"result_file": "query-plan.json", "request_count": len(requests), "api_spend_usd": 0})
