"""Claim-directed local preparation for a reasoning analyst, with honest stage states."""
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, read_json, write_json


def deep_read(input, output, *, transcript=None, method=None, claims=None, contour_run=None,
              episode_isolated=False, auditory_observer="human", execute_local_queries=False,
              audio_stream=None, video_stream=None, external_audio=None, external_audio_offset=None,
              screenshots_manifest=None, direct_receipt=None, observer_receipt=None, timestamp_tolerance_ms=None):
    from .analysis_preflight import preflight
    from .evidence_claims import audit_claims
    from .evidence_queries import contour_query
    from .analysis_benchmark import select_analytical_method, stage_analytical_method
    if auditory_observer not in {"human", "mock", "none"}:
        raise AVError("Choose human, mock or none; real provider selection is separate")
    payload, selected, method_sources = select_analytical_method(method)
    deps = [file_record(input)] + [file_record(p) for p in (transcript, claims) if p] + method_sources
    if contour_run:
        deps.append(file_record(Path(contour_run)/"run.json"))
    inputs = [input]+[p for p in (transcript,claims,contour_run,external_audio) if p]+[s["path"] for s in method_sources]
    with output_transaction(output, inputs) as stage:
        preflight(input, stage/"preflight", audio_stream=audio_stream, video_stream=video_stream,
            external_audio=external_audio, external_audio_offset=external_audio_offset, transcript=transcript,
            screenshots_manifest=screenshots_manifest, direct_receipt=direct_receipt, observer_receipt=observer_receipt,
            timestamp_tolerance_ms=timestamp_tolerance_ms)
        capabilities = read_json(stage/"preflight"/"capabilities.json")
        requests, executions = [], []
        if claims:
            audit_claims(claims, stage/"claims", capabilities=stage/"preflight"/"capabilities.json")
            requests = read_json(stage/"claims"/"claim-audit.json")["requests"]
        if execute_local_queries and contour_run:
            from .evidence_queries import load_contours
            _,_,audio,_ = load_contours(contour_run)
            for req in requests:
                if req["route"] != "CONTOUR_QUERY":
                    continue
                if any(loc["source_sha256"] != audio["source_sha256"] for loc in req["locators"]):
                    raise AVError("Planned query and contour source identities differ")
                queries = [{"id": req["request_id"]+"-%d" % i, "start_s": a, "end_s": b,
                    "boundary_origin": "estimated_boundary"} for i,(a,b) in enumerate(
                        span for loc in req["locators"] for span in loc["intervals_seconds"])]
                if any(q["end_s"]-q["start_s"] > req["budget"].get("max_clip_seconds",30) for q in queries):
                    executions.append({"request_id": req["request_id"], "status": "BUDGET_EXCEEDED", "no_measurement_claimed": True}); continue
                queryfile = stage/(req["request_id"]+".json")
                write_json(queryfile, {"queries": queries})
                contour_query(contour_run, stage/req["request_id"], queries=queryfile)
                executions.append({"request_id": req["request_id"], "status": "MEASURED_NOT_ADJUDICATED", "run": req["request_id"]})
        receipt = stage_analytical_method(stage, payload, selected,
            relative_path="method.txt", episode_isolated=episode_isolated)
        stages = {"preflight": "COMPLETED", "source_lock": "HASHED", "initial_text_visual_reading": "REQUIRES_REASONING_ANALYST",
            "claim_planning": "COMPLETED" if claims else "AWAITING_ATOMIC_CLAIMS", "targeted_queries": executions,
            "auditory_escalation": "HUMAN_REVIEW_AVAILABLE" if auditory_observer == "human" else "MOCK_HAS_NO_PERCEPTION" if auditory_observer == "mock" else "NOT_CONFIGURED",
            "claim_adjudication": "REQUIRES_SOURCE_REVIEW", "counterevidence_pass": "REQUIRES_REASONING_ANALYST",
            "final_deep_reading": "NOT_GENERATED_BY_MEDIA_TOOL"}
        write_json(stage/"analysis-plan.json", {**receipt, "schema": "ave.deep-read.v1", "modalities": capabilities["modalities"],
            "stages": stages, "requests": requests,
            "method_policy": "Owner-supplied method" if selected["method_selection"] == "explicit" else "Builtin fallback method",
            "scope": "Executable evidence preparation and analyst handoff; no fabricated automated literary reading or model listening"})
        boundary = ("Semantic evidence is limited to the supplied episode/clip. Do not use later episodes or external-series outcomes.\n"
                    if episode_isolated else "Apply the owner's requested semantic evidence scope.\n")
        (stage/"START_HERE.txt").write_text("Use method.txt as the governing analytical method. Method selection and episode isolation are independent.\n"
            + boundary + "Method examples and hypotheses are analytical guidance, not observations of this source.\n"
            "Inspect preflight/capabilities.json, then claims/report.html when supplied.\n"
            "Complete an independent source reading, submit atomic claims, run the smallest consequential evidence queries, preserve OPEN debts, and write the final interpretation.\n"
            "The preparer does not itself call a reasoning model or remote service. Human listening uses performance-review and observer-human-import.\n", encoding="utf-8")
        return finish_run(stage, "deep-read", deps, metadata={**receipt, "method_receipt_file": "method-receipt.json",
            "result_file": "analysis-plan.json", "reasoning_status": "REQUIRES_REASONING_ANALYST"})
