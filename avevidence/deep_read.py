"""Claim-directed local preparation for a reasoning analyst, with honest stage states."""
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, read_json, write_json


def deep_read(input, output, *, transcript=None, method=None, claims=None, contour_run=None,
              episode_isolated=False, auditory_observer="human", execute_local_queries=False,
              audio_stream=None, video_stream=None, external_audio=None, external_audio_offset=None,
              screenshots_manifest=None, direct_receipt=None, observer_receipt=None, timestamp_tolerance_ms=None,
              prepare_observer_witnesses=False, execute_observer=False, observer_requests=None,
              allow_remote_media=None, credential_env="OPENAI_API_KEY", budget_usd=.50, request_budget_usd=.10,
              max_output_tokens=600, observer_backend=None, scene_packet=None):
    from .analysis_preflight import preflight
    from .evidence_claims import audit_claims
    from .evidence_queries import contour_query
    from .analysis_benchmark import select_analytical_method, stage_analytical_method
    if auditory_observer not in {"human", "mock", "none", "openai-audio"}:
        raise AVError("Choose human, mock, none or the explicitly selected openai-audio backend")
    hosted = auditory_observer == "openai-audio"
    if not hosted and (execute_observer or prepare_observer_witnesses or observer_requests or observer_backend):
        raise AVError("Hosted preparation/execution requires explicit --auditory-observer openai-audio")
    backend = None
    if hosted:
        from .providers.registry import create_backend
        from .providers.openai_common import authorize
        from .observer_execution import require_capability
        backend = observer_backend if observer_backend is not None else create_backend("openai-audio", credential_env=credential_env,
            allow_remote_media=allow_remote_media, max_output_tokens=max_output_tokens)
        if backend.identity.get("backend_id") != "openai-audio":
            raise AVError("Selected observer does not match the deep-read hosted route")
        if execute_observer:
            authorize(allow_remote_media, credential_env)
            backend.allow_remote_media = allow_remote_media
            require_capability(observer_receipt, backend)
    payload, selected, method_sources = select_analytical_method(method)
    deps = [file_record(input)] + [file_record(p) for p in (transcript, claims, scene_packet) if p] + method_sources
    if scene_packet:
        from .scene_packets import load_scene_packet
        scene_data = load_scene_packet(scene_packet)
        if scene_data[0]["source"]["sha256"] != deps[0]["sha256"]:
            raise AVError("Scene packet and deep-read source identities differ")
        for key,value in (("audio_stream",audio_stream),("video_stream",video_stream)):
            if value is not None and scene_data[0]["source"].get(key,value) != value:
                raise AVError("Scene packet and deep-read selected streams differ")
    if contour_run:
        deps.append(file_record(Path(contour_run)/"run.json"))
    deps += [file_record(p, "observer_request") for p in (observer_requests or [])]
    inputs = [input]+[p for p in (transcript,claims,contour_run,external_audio,direct_receipt,observer_receipt,scene_packet) if p]+[s["path"] for s in method_sources]+list(observer_requests or [])
    with output_transaction(output, inputs) as stage:
        preflight(input, stage/"preflight", audio_stream=audio_stream, video_stream=video_stream,
            external_audio=external_audio, external_audio_offset=external_audio_offset, transcript=transcript,
            screenshots_manifest=screenshots_manifest, direct_receipt=direct_receipt, observer_receipt=observer_receipt,
            timestamp_tolerance_ms=timestamp_tolerance_ms)
        capabilities = read_json(stage/"preflight"/"capabilities.json")
        selected_capabilities = stage/"preflight"/"capabilities.json"
        if scene_packet:
            from .scene_packets import prepare_scene_packet
            from .scene_planning import plan_scene_queries
            prepare_scene_packet(scene_packet,stage/"dramatic-event")
            plan_scene_queries(scene_packet,stage/"dramatic-event-queries")
        if hosted:
            from .providers.openai_common import HostedError
            cap = {"status": "NOT_TESTED", "backend_id": "openai-audio", "backend_identity": backend.identity}
            if observer_receipt:
                try:
                    cap = dict(require_capability(observer_receipt, backend), backend_id="openai-audio")
                except HostedError as exc:
                    cap["admission_error"] = exc.code
            capabilities.setdefault("capabilities", {})["auditory_observer"] = cap
            selected_capabilities = stage/"selected-observer-capabilities.json"
            write_json(selected_capabilities, capabilities)
        requests, executions = [], []
        if claims:
            audit_claims(claims, stage/"claims", capabilities=selected_capabilities)
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
        hosted_plans, hosted_runs = [], []
        auditory_status = "HUMAN_REVIEW_AVAILABLE" if auditory_observer == "human" else "MOCK_HAS_NO_PERCEPTION" if auditory_observer == "mock" else "NOT_CONFIGURED"
        if hosted:
            from .observer_planning import prepare_requests
            from .observer_execution import observe_request
            from .providers.openai_common import _account, _rows
            hosted_plans, added_deps = prepare_requests(requests, capabilities, stage, backend=backend,
                prepare_witnesses=prepare_observer_witnesses or execute_observer, explicit_requests=observer_requests,
                timestamp_tolerance_ms=timestamp_tolerance_ms)
            deps += added_deps
            auditory_status = "HOSTED_PREPARED_NOT_SUBMITTED" if any(p["status"] == "PREPARED_NOT_SUBMITTED" for p in hosted_plans) else "HOSTED_REFINEMENT_REQUIRED"
            if execute_observer:
                if not hosted_plans:
                    raise AVError("No refined hosted requests are available; supply delivery claims or --observer-request")
                queue = stage/"observer-queue-usage.jsonl"
                for i, plan in enumerate(hosted_plans):
                    if plan["status"] != "PREPARED_NOT_SUBMITTED":
                        continue
                    request_path = Path(plan["request_file"])
                    witness_path = Path(plan["witness_run"]) if plan.get("witness_run") else None
                    if plan["paths_relative_to"] == "deep_read_run":
                        request_path = stage/request_path
                        witness_path = stage/witness_path if witness_path else None
                    run = stage/"hosted-observations"/("request-%03d" % i)
                    manifest = observe_request(request_path, run, witness_run=witness_path, backend=backend,
                        capability=observer_receipt, budget_usd=budget_usd, request_budget_usd=request_budget_usd,
                        queue_ledger=queue, queue_budget_usd=min(budget_usd, 2.))
                    status = manifest["metadata"]["execution_status"]
                    plan["execution_status"] = status
                    plan["observation_run"] = run.relative_to(stage).as_posix()
                    hosted_runs.append(run)
                    if status != "OBSERVATION_VALID":
                        break
                auditory_status = "HOSTED_OBSERVATIONS_AWAITING_ADJUDICATION" if hosted_runs and all(read_json(r/"run.json")["metadata"]["execution_status"] == "OBSERVATION_VALID" for r in hosted_runs) else "HOSTED_EXECUTION_INCOMPLETE"
                if queue.exists():
                    ledger = _rows(queue); committed, unknown = _account(ledger, queue_limit=min(budget_usd, 2.))
                    write_json(stage/"hosted-cost-summary.json", {"estimated_cost_usd": sum(r["estimated_cost_usd"] for r in ledger if r["event"] == "RESERVED"),
                        "actual_cost_usd": None if unknown else float(committed), "committed_or_reserved_usd": float(committed),
                        "billing_unresolved": unknown, "budget_usd": budget_usd, "scope": "Local guard; not an account balance"})
                if claims and hosted_runs:
                    from .auditory_claims import claim_delta
                    try:
                        claim_delta(claims, hosted_runs, stage/"auditory-claim-delta")
                    except AVError as exc:
                        # An abstention or invalid response cannot be promoted to
                        # evidence. Retain the failure without changing the route.
                        write_json(stage/"auditory-admission.json", {"status": "NO_AFFIRMATIVE_AUDITORY_EVIDENCE", "reason": str(exc), "claim_status_changes": "NONE"})
            write_json(stage/"hosted-auditory-plan.json", {"schema": "ave.hosted-auditory-plan.v1", "backend_identity": backend.identity,
                "requests": hosted_plans, "execution_authorized": bool(execute_observer and allow_remote_media == "openai"),
                "budget_usd": budget_usd, "request_budget_usd": request_budget_usd, "status": auditory_status,
                "scope": "Explicit hosted route; no automatic human/mock fallback or claim promotion"})
        receipt = stage_analytical_method(stage, payload, selected,
            relative_path="method.txt", episode_isolated=episode_isolated)
        stages = {"preflight": "COMPLETED", "source_lock": "HASHED", "initial_text_visual_reading": "REQUIRES_REASONING_ANALYST",
            "claim_planning": "COMPLETED" if claims else "AWAITING_ATOMIC_CLAIMS", "targeted_queries": executions,
            "auditory_escalation": auditory_status,
            "claim_adjudication": "REQUIRES_SOURCE_REVIEW", "counterevidence_pass": "REQUIRES_REASONING_ANALYST",
            "final_deep_reading": "NOT_GENERATED_BY_MEDIA_TOOL"}
        if scene_packet:
            stages["dramatic_event"] = {"status":"PREPARED_NOT_INTERPRETED","packet_run":"dramatic-event",
                "query_plan_run":"dramatic-event-queries","principle":"Separate provenance; integrated interpretation"}
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
            "The preparer does not generate a literary interpretation. Hosted audio calls require an explicitly selected backend, execution opt-in, authorization, a matched probe and a budget. Human listening uses performance-review and observer-human-import.\n"
            + ("Use dramatic-event/evidence-bindings.json and dramatic-event-queries/query-plan.json to interpret one event jointly, adjudicate exact propositions, and produce scene delta against a frozen before packet.\n" if scene_packet else ""), encoding="utf-8")
        return finish_run(stage, "deep-read", deps, metadata={**receipt, "method_receipt_file": "method-receipt.json",
            "result_file": "analysis-plan.json", "reasoning_status": "REQUIRES_REASONING_ANALYST"})
