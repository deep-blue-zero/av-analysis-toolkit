"""Refine claim-directed hosted preparation without forwarding narrative context."""
from pathlib import Path

from .common import AVError, file_record, read_json, write_json
from .observer_execution import estimate_observation
from .observer_requests import load_request

NEUTRAL_QUESTION = ("Describe audible differences in pitch movement, pace, loudness, hesitation, articulation and vocal tension. "
                    "Distinguish audible descriptions from uncertain emotional readings and state interference or abstentions.")


def prepare_requests(requests, capabilities, stage, *, backend, prepare_witnesses=False, explicit_requests=None, timestamp_tolerance_ms=None):
    from .audio_witness import audio_witness
    stage = Path(stage)
    audio_source = capabilities.get("audio_source", {})
    selected = capabilities.get("selected_streams", {}).get("audio")
    plans, deps = [], []
    if explicit_requests:
        seen = set()
        for i, path in enumerate(explicit_requests):
            request, clips, _, inputs = load_request(path)
            if request["request_id"] in seen:
                raise AVError("Duplicate observer request ID in the deep-read queue; no repeated submission")
            seen.add(request["request_id"])
            if any(c["source_sha256"] != audio_source.get("sha256") or c["stream_index"] != selected for c in clips):
                raise AVError("Deep-read observer request differs from the selected episode/audio stream")
            estimate = estimate_observation(path, backend=backend)
            deps += inputs
            plans.append({"request_id": request["request_id"], "claim_id": None, "request_file": str(Path(path).resolve()),
                "witness_run": None, "paths_relative_to": "absolute", "status": "PREPARED_NOT_SUBMITTED", "estimate": estimate["estimate"]})
        return plans, deps
    for req in requests:
        if req["route"] not in {"HUMAN_LISTENING", "BOUNDED_AUDITORY_OBSERVER", "HOSTED_PROBE_REQUIRED"}:
            continue
        plan = {"request_id": req["request_id"], "claim_id": req["claim_id"], "priority": req["priority"],
                "status": "REFINEMENT_REQUIRED", "reason": "Select 1..4 atomic clips, at most 30 s each and 60 s total; broad debt intervals are never submitted"}
        spans = [(loc, a, b) for loc in req["locators"] for a,b in loc["intervals_seconds"]]
        max_clip = min(30, req["budget"].get("max_clip_seconds", 30))
        if not 1 <= len(spans) <= 4 or any(b-a > max_clip for _,a,b in spans) or sum(b-a for _,a,b in spans) > 60:
            plans.append(plan); continue
        if any(loc["source_sha256"] != audio_source.get("sha256") or loc.get("stream_index", selected) != selected for loc,_,_ in spans):
            plan["reason"] = "This claim is not bound to the selected episode/audio stream"; plans.append(plan); continue
        if not prepare_witnesses:
            plan.update(status="BOUNDED_WITNESS_PREPARATION_REQUIRED", reason="Use --prepare-observer-witnesses for these refined intervals; no remote submission is implied")
            plans.append(plan); continue
        if sum(len(p.get("prepared_clips", [])) for p in plans) + len(spans) > 50:
            plan["reason"] = "Local preparation is capped at 50 microclips per deep-read run; refine a smaller consequential queue"
            plans.append(plan); continue
        folder = stage/"observer-requests"/req["request_id"]
        folder.mkdir(parents=True)
        clip_entries = []
        for i, (_,a,b) in enumerate(spans):
            label = "ABCD"[i]
            witness = folder/("witness-"+label)
            audio_witness(audio_source["path"], witness, start=a, end=b, stream_index=selected,
                          timestamp_tolerance_ms=timestamp_tolerance_ms)
            clip_entries.append({"label": label, "witness_run": witness.name})
        request = {"schema": "ave.observer-request.v1", "request_id": req["request_id"], "question": NEUTRAL_QUESTION, "stage": 1}
        # Legacy delivery requests retain their profile; other tasks remain
        # explicit and cannot silently inherit speech-only qualification.
        if "task_profile" in req:
            from .auditory_profiles import task_profile, profile_prompt
            request["task_profile"] = task_profile(req["task_profile"])
            request["question"] = profile_prompt(request["task_profile"])
        if len(spans) == 1:
            request["source_sha256"] = audio_source["sha256"]
        else:
            request["clips"] = clip_entries
        request_path = folder/"request.json"
        write_json(request_path, request)
        single_witness = folder/clip_entries[0]["witness_run"] if len(spans) == 1 else None
        estimate = estimate_observation(request_path, witness_run=single_witness, backend=backend)
        write_json(folder/"estimate.json", estimate)
        plan.update(status="PREPARED_NOT_SUBMITTED", reason="Neutral first pass; analytical question and alternatives stay in the local claim plan",
            request_file=request_path.relative_to(stage).as_posix(),
            witness_run=single_witness.relative_to(stage).as_posix() if single_witness else None,
            paths_relative_to="deep_read_run", prepared_clips=clip_entries, estimate=estimate["estimate"])
        plans.append(plan)
    return plans, deps
