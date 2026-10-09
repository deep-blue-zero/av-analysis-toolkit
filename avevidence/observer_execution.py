"""Observer orchestration; provider transport never becomes claim adjudication."""
from __future__ import annotations

import json
from functools import wraps
import math
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, utc_now, write_json
from .inventory import verify_run
from .performance_cache import digest
from .observer_requests import (load_request, public_clip, source_observations, validate_comparison_response, wav_identity)
from .providers.openai_common import BudgetSession, HostedError, authorize, model_matches
from .auditory_profiles import request_policy


def _serialized_hosted(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        backend = kwargs.get("backend")
        if backend is not None and hasattr(backend, "execution_guard"):
            with backend.execution_guard():
                return function(*args, **kwargs)
        return function(*args, **kwargs)
    return invoke


def _prompt(request):
    from .auditory_profiles import profile_prompt
    return profile_prompt(request.get("task_profile")) + "\nQuestion: " + request["question"]


def _backend_request(request):
    """Only neutral task policy and question cross the provider boundary."""
    return {k: request[k] for k in ("request_id", "question", "stage", "task_profile", "audio_mode",
                                  "allow_long_section", "section_budget_usd") if k in request} | {"prompt": _prompt(request)}


def validate_hosted_capability(receipt, identity):
    if receipt.get("backend_identity") != identity or receipt.get("schema") not in {"ave.hosted-auditory-capability.v1", "ave.hosted-auditory-capability.v2"}:
        raise HostedError("BACKEND_NOT_PROBED", "Hosted admission requires a matching scored hosted capability receipt")
    if receipt.get("status") != "PROBE_PASSED" or receipt.get("semantic_probe_passed") is not True:
        raise HostedError("BACKEND_PROBE_FAILED", "This auditory route has no passed semantic probe")
    if any(receipt.get(k) != identity.get(i) for k,i in (("interface","api_route"),("model_revision","model_revision"),("adapter_revision","adapter_revision"))):
        raise HostedError("BACKEND_NOT_PROBED", "Capability interface and revision fields disagree with their bound backend")
    score = receipt.get("probe_result")
    if not isinstance(score, dict):
        raise HostedError("BACKEND_PROBE_FAILED", "Semantic probe statistics are absent")
    n, correct = score.get("trials"), score.get("correct")
    if type(n) is not int or not 12 <= n <= 200 or type(correct) is not int or not 0 <= correct <= n:
        raise HostedError("BACKEND_PROBE_FAILED", "Semantic probe counts are malformed")
    p = sum(math.comb(n, k) for k in range(correct, n+1))/2**n
    if correct/n < .8 or p > .01 or score.get("passed") is not True:
        raise HostedError("BACKEND_PROBE_FAILED", "Semantic probe did not meet the statistical threshold")
    if any(score.get(key) != value for key, value in (("accuracy", correct/n), ("chance_one_sided_p", p))):
        raise HostedError("BACKEND_PROBE_FAILED", "Probe statistics do not agree with their recorded counts")
    families = receipt.get("family_results")
    if not isinstance(families, dict) or set(families) != {"pitch_direction", "signal_presence"}:
        raise HostedError("BACKEND_PROBE_FAILED", "Both blinded contrast families are required")
    for family in families.values():
        if (not isinstance(family, dict) or type(family.get("trials")) is not int or family["trials"] < 6
            or type(family.get("correct")) is not int or not .8 <= family["correct"]/family["trials"] <= 1):
            raise HostedError("BACKEND_PROBE_FAILED", "A blinded contrast family failed its input-influence check")
    if sum(f["trials"] for f in families.values()) != n or sum(f["correct"] for f in families.values()) != correct:
        raise HostedError("BACKEND_PROBE_FAILED", "Contrast families disagree with the overall probe counts")
    if receipt["schema"] == "ave.hosted-auditory-capability.v2":
        pairs = receipt.get("paired_results")
        if not isinstance(pairs, dict) or set(pairs) != set(families):
            raise HostedError("BACKEND_PROBE_FAILED", "Probe v2 requires independently scored reversed pairs")
        for key, result in pairs.items():
            count, successes = result.get("pairs"), result.get("correct_pairs")
            if (type(count) is not int or count < 6 or type(successes) is not int or not 0 <= successes <= count
                or count*2 != families[key]["trials"] or successes*2 > families[key]["correct"]):
                raise HostedError("BACKEND_PROBE_FAILED", "Malformed reversed-pair counts")
            probability = sum(math.comb(count, k) for k in range(successes, count+1))/2**count
            if successes/count < .8 or probability > .05 or result.get("chance_one_sided_p") != probability or result.get("passed") is not True:
                raise HostedError("BACKEND_PROBE_FAILED", "Reversed-pair influence threshold was not met")
    returned_model = receipt.get("returned_model")
    if not model_matches(identity["model_revision"], returned_model):
        raise HostedError("BACKEND_NOT_PROBED", "Probe lacks a stable provider-reported audio model identity")
    return receipt


def require_capability(path, backend):
    from .analysis_preflight import capability_receipt
    if not path:
        raise HostedError("BACKEND_NOT_PROBED", "A matching passed blinded semantic probe is required")
    receipt = capability_receipt(read_json(path))
    if receipt.get("backend_identity") != backend.identity:
        raise HostedError("BACKEND_NOT_PROBED", "The probe belongs to another provider/model/adapter/prompt/transport route")
    if receipt["status"] != "PROBE_PASSED":
        raise HostedError("BACKEND_PROBE_FAILED", "This auditory route has no passed semantic probe")
    if backend.identity.get("route_type") == "hosted":
        validate_hosted_capability(receipt, backend.identity)
    return receipt


def _stage1_parent(request, clips, base):
    if "stage1_run" not in request:
        raise AVError("Hosted stage 2 requires a separate source-matched stage1_run")
    if not isinstance(request["stage1_run"], str) or not request["stage1_run"]:
        raise AVError("Stage 1 parent must be an observation-run path")
    folder = Path(request["stage1_run"])
    folder = folder.resolve() if folder.is_absolute() else (base/folder).resolve()
    verify_run(folder)
    path = folder/"observation.json"
    parent = read_json(path)
    if parent.get("stage") != 1 or parent.get("validation_status") != "VALID":
        raise AVError("Stage 2 requires a valid stage 1 baseline, including any recorded abstentions")
    if parent.get("schema") not in {"ave.auditory-observation.v1", "ave.auditory-comparison.v1"}:
        raise AVError("Stage 1 baseline is not an observer record")
    prior = parent.get("clips") or [parent]
    def binding(c):
        # Identical silence/reused bytes can occur at different source times.
        # Hash agreement alone does not bind a contextual reinspection to its
        # original event, sample mapping, clip order and opaque labels.
        return (c.get("label", "A"), c.get("clip_sha256"), c.get("source_sha256"),
                c.get("stream_index"), c.get("source_interval_seconds"),
                c.get("actual_source_interval_seconds"),
                c.get("review_mapping", c.get("clip_to_source_mapping")))
    expected = [binding(c) for c in clips]
    recorded = [binding(c) for c in prior]
    if expected != recorded:
        raise AVError("Stage 2 input clips differ from the stage 1 baseline")
    from .auditory_profiles import task_profile
    if task_profile(parent.get("task_profile")) != task_profile(request.get("task_profile")):
        raise AVError("Stage 2 reinspection must retain the Stage 1 auditory task profile")
    return folder, file_record(path, "stage1_observation")


def estimate_observation(request_file, *, witness_run=None, backend=None, output=None):
    from .providers.registry import create_backend
    backend = backend if backend is not None else create_backend("openai-audio")
    if not hasattr(backend, "estimate_cost"):
        raise AVError("Selected backend has no hosted cost estimator")
    request, clips, context, deps = load_request(request_file, witness_run)
    actual = _backend_request(request)
    estimate = backend.estimate_cost(actual, {"clips": clips}, context or None)
    report = {"schema": "ave.observer-estimate.v1", "request_id": request["request_id"],
              "backend_identity": backend.identity, "clips": [public_clip(c) for c in clips],
              "estimate": estimate, "submitted": False, "credential_required": False,
              "task_profile": estimate["audio_policy"]["task_profile"]}
    if not output:
        return report
    with output_transaction(output, [request_file] + [c["witness_run"] for c in clips]) as stage:
        write_json(stage/"estimate.json", report)
        return finish_run(stage, "observer-estimate", deps, metadata={"result_file": "estimate.json", "submitted": False})


def _atomic_record(request, clip, *, backend, prompt, context, capability_status, capability_receipt, raw, parsed,
                   started, completed, validation_error=None, execution_status=None, provider_receipt=None, parent=None,
                   admission=None, qualification=None):
    identity = backend.identity
    from .auditory_profiles import request_policy
    policy = request_policy(request)
    is_test = identity.get("type") == "mock" or identity.get("execution_mode") == "TEST_DOUBLE"
    return {"schema": "ave.auditory-observation.v1", "observer_type": "mock" if is_test else "model_audio",
        "backend_identity": identity, "request_id": request["request_id"], "source_sha256": clip["source_sha256"],
        "source_interval_seconds": clip["source_interval_seconds"], "stream_index": clip["stream_index"], "clip_sha256": clip["clip_sha256"],
        "clip_to_source_mapping": clip["review_mapping"], "transformations": clip["transformations"], "wav_format": clip["wav_format"],
        "sample_rate_hz": clip["identity"]["sample_rate_hz"], "channels": clip["identity"]["channels"], "duration_seconds": clip["duration_seconds"],
        "source_clock_uncertainty_seconds": clip["source_clock_uncertainty_seconds"],
        "actual_source_interval_seconds": clip["actual_source_interval_seconds"],
        "prompt": prompt, "prompt_sha256": digest(prompt), "stage": request.get("stage", 1), "supplied_context": context,
        "evidence_class": "AO_STAGE2" if request.get("stage", 1) == 2 else "AO_STAGE1",
        "task_profile": policy["task_profile"], "audio_policy": policy,
        "task_capability_status": qualification["status"] if qualification else "UNQUALIFIED",
        "admission_dimensions": admission, "task_qualification": qualification,
        "observation_lane": request.get("observation_lane", "QUALIFIED"),
        "media_category": request.get("media_category"), "source_binding_receipt": public_clip(clip),
        "raw_response": raw, "parsed": parsed, "validation_status": "INVALID" if validation_error else "VALID",
        "validation_error": validation_error, "capability_status": capability_status, "execution_status": execution_status,
        "capability_receipt": capability_receipt,
        "source_observations": source_observations(parsed, clip) if parsed else [], "provider_receipt": provider_receipt,
        "stage1_parent": parent, "started_at": started, "completed_at": completed,
        "confidence_calibration": "UNCALIBRATED_REVIEWER_JUDGMENT",
        "scope": "Attributed auditory witness; never the coordinating model's own hearing; test doubles are not perceptual evidence"}


@_serialized_hosted
def observe_request(request_file, output, *, witness_run=None, backend=None, capability=None,
                    budget_usd=.50, request_budget_usd=.10, queue_ledger=None, queue_budget_usd=2.,
                    observation_lane=None, qualification=None, media_category=None):
    from .auditory_observer import MockObserver, validate_response
    backend = backend if backend is not None else MockObserver()
    hosted = backend.identity.get("route_type") == "hosted"
    if hosted:
        authorize(backend.allow_remote_media, backend.credential_env)
    request, clips, context, deps = load_request(request_file, witness_run)
    if observation_lane is not None:
        if request.get("observation_lane", observation_lane) != observation_lane:
            raise AVError("CLI and request auditory lanes disagree")
        if observation_lane not in {"QUALIFIED", "EXPERIMENTAL"}:
            raise AVError("Unknown auditory observation lane")
        request["observation_lane"] = observation_lane
    if media_category is not None:
        from .inventory import text_value
        text_value(media_category, "source-media category")
        if request.get("media_category", media_category) != media_category:
            raise AVError("CLI and request media categories disagree")
        request["media_category"] = media_category
    inputs = [request_file] + [c["witness_run"] for c in clips]
    is_mock = backend.identity.get("type") == "mock"
    capability_status, receipt = "NOT_CONFIGURED", None
    lane = request.get("observation_lane", "QUALIFIED")
    task_record = None
    # Preserve existing injected-transport regression behavior, never live
    # execution authority or perceptual adequacy. Explicit lanes use new rules.
    legacy_test = (backend.identity.get("execution_mode") == "TEST_DOUBLE"
                   and "observation_lane" not in request and qualification is None)
    if not is_mock and legacy_test:
        receipt = require_capability(capability, backend)
        capability_status = receipt["status"]
        deps.append(file_record(capability, "auditory_capability")); inputs.append(capability)
    elif not is_mock:
        if capability:
            from .analysis_preflight import capability_receipt
            receipt = capability_receipt(read_json(capability))
            if receipt.get("backend_identity") != backend.identity:
                raise AVError("Historical input-influence receipt belongs to another exact backend")
            capability_status = receipt["status"]
            deps.append(file_record(capability)); inputs.append(capability)
        if lane == "QUALIFIED":
            from .auditory_qualification import competence, load_qualification
            if not qualification or not request.get("media_category"):
                raise HostedError("TASK_NOT_QUALIFIED", "Qualified observation requires benchmark qualification and an explicit media category")
            task_record = load_qualification(qualification, identity=backend.identity,
                task=competence(request.get("task_profile", "SPEECH_PERFORMANCE")), media_category=request["media_category"],
                configuration_revision=backend.identity.get("configuration_revision"))
            qfolder = Path(qualification) if Path(qualification).is_dir() else Path(qualification).parent
            deps.append(file_record(qfolder/"qualification.json", "task_qualification")); inputs.append(qfolder)
        if hosted and any(c.get("witness_kind") != "PROVIDER_SUBMISSION_WITNESS" for c in clips):
            raise AVError("Hosted lanes require a verified PCM16 provider-submission witness")
    elif capability:
        from .analysis_preflight import capability_receipt
        receipt = capability_receipt(read_json(capability))
        if receipt.get("backend_identity") != backend.identity:
            raise AVError("Capability probe belongs to another adapter/model revision")
        capability_status = receipt["status"]
        deps.append(file_record(capability)); inputs.append(capability)
    if not is_mock and not hosted and backend.identity.get("route_type") not in {"local", "human"}:
        raise AVError("Unknown auditory execution route")
    parent = None
    if request.get("stage", 1) == 2 and (hosted or "stage1_run" in request or "task_profile" in request):
        folder, record = _stage1_parent(request, clips, Path(request_file).resolve().parent)
        deps.append(record); inputs.append(folder)
        parent = {"path": str(folder), "observation_sha256": record["sha256"], "stage": 1}
    prompt = _prompt(request)
    # Only the neutral prompt, opaque labels and clip bytes enter the backend;
    # request paths, claim IDs, methods and source filenames are not forwarded.
    actual = _backend_request(request)
    supplied_clip = clips[0] if len(clips) == 1 else {"clips": clips}
    with output_transaction(output, inputs) as stage:
        if task_record:
            import shutil
            shutil.copytree(qfolder, stage/"qualification-proof")
        session = None
        if hosted:
            policy = request_policy(request)
            if policy["explicit_request_budget_required"]:
                # This narrows an operator-configured ceiling; it never raises it.
                request_budget_usd = min(request_budget_usd, policy["section_budget_usd"])
            session = BudgetSession(stage, budget_usd=budget_usd, request_budget_usd=request_budget_usd,
                                    queue_ledger=queue_ledger, queue_budget_usd=queue_budget_usd,
                                    model=backend.identity["model_revision"])
            backend.session = session
            backend.admitted_capability = backend.identity.copy()
            backend.admitted_returned_model = receipt.get("returned_model") if legacy_test else (
                task_record["returned_model"] if task_record else None)
            backend.observation_lane = None if legacy_test else lane
            backend.precheck()
        started = utc_now()
        raw, provider_receipt, error = None, None, None
        try:
            raw = backend.observe(actual, supplied_clip, context or None)
        except HostedError as exc:
            error = exc
        completed = utc_now()
        if hosted:
            provider_receipt = backend.last_receipt
            if provider_receipt:
                provider_receipt = {k: v for k, v in provider_receipt.items() if k != "raw_response"}
            if raw is None and backend.last_receipt:
                raw = backend.last_receipt.get("raw_response")
        validation_error, parsed = None, None
        if error:
            validation_error = str(error)
        elif not isinstance(raw, str) or len(raw.encode("utf-8")) > 1048576:
            validation_error = "Auditory adapter did not return text within the 1 MiB receipt budget"
            raw = None
        else:
            try:
                parsed = validate_response(raw, clips[0]["duration_seconds"]) if len(clips) == 1 else validate_comparison_response(raw, clips)
            except AVError as exc:
                validation_error = str(exc)
        status = error.code if error else "OBSERVATION_INVALID" if validation_error else "OBSERVATION_VALID"
        contract_status = "NOT_EVALUATED"
        if isinstance(raw, str):
            try:
                validate_response(raw, clips[0]["duration_seconds"]) if len(clips) == 1 else validate_comparison_response(raw, clips)
                contract_status = "VALID"
            except AVError:
                contract_status = "INVALID"
        admission = {"schema": "ave.auditory-admission.v1", "lane": lane,
            "source_authorization": "VERIFIED_AUTHORIZED" if hosted else "LOCAL_SOURCE_BOUND",
            "transport": "ACCEPTED" if provider_receipt and provider_receipt.get("status") == "REQUEST_ACCEPTED" else "NOT_ESTABLISHED",
            "response_contract": contract_status,
            "transport_details": {"audio_submission_attempted": bool(provider_receipt and provider_receipt.get("submission_attempted")),
                "expected_model_returned": bool(provider_receipt and model_matches(backend.identity.get("model_revision"), provider_receipt.get("returned_model"))),
                "usage_accounted": bool(provider_receipt and (provider_receipt.get("usage") or {}).get("actual_cost_usd") is not None)},
            "input_influence": capability_status,
            "task_competence": task_record["status"] if task_record else "UNQUALIFIED",
            "individual_adequacy": "REQUIRES_PROPOSITION_SPECIFIC_ADJUDICATION",
            "test_double": is_mock or backend.identity.get("execution_mode") == "TEST_DOUBLE"}
        shared = dict(backend=backend, prompt=prompt, context=context, capability_status=capability_status, raw=raw,
                      capability_receipt=receipt,
                      started=started, completed=completed, validation_error=validation_error, execution_status=status,
                      provider_receipt=provider_receipt, parent=parent, admission=admission, qualification=task_record)
        atomic_files = []
        if len(clips) == 1:
            report = _atomic_record(request, clips[0], parsed=parsed, **shared)
            write_json(stage/"observation.json", report)
            atomic_files.append("observation.json")
        else:
            report = {"schema": "ave.auditory-comparison.v1", "request_id": request["request_id"],
                "observer_type": "mock" if is_mock or backend.identity.get("execution_mode") == "TEST_DOUBLE" else "model_audio",
                "backend_identity": backend.identity, "clips": [public_clip(c) for c in clips], "stage": request.get("stage", 1),
                "prompt": prompt, "prompt_sha256": digest(prompt), "supplied_context": context, "stage1_parent": parent,
                "evidence_class": "AO_STAGE2" if request.get("stage", 1) == 2 else "AO_STAGE1",
                "task_profile": request.get("task_profile", "SPEECH_PERFORMANCE"), "audio_policy": request_policy(request),
                "task_capability_status": task_record["status"] if task_record else "UNQUALIFIED",
                "admission_dimensions": admission, "task_qualification": task_record,
                "observation_lane": lane, "media_category": request.get("media_category"),
                "raw_response": raw, "parsed": parsed, "validation_status": "INVALID" if validation_error else "VALID",
                "validation_error": validation_error, "execution_status": status, "capability_status": capability_status,
                "capability_receipt": receipt,
                "provider_receipt": provider_receipt, "started_at": started, "completed_at": completed,
                "scope": "Independent clip clocks; comparisons are witnesses, not claim decisions"}
            write_json(stage/"observation.json", report)
            if parsed:
                by_label = {r["label"]: r["observation"] for r in parsed["clips"]}
                for c in clips:
                    record = _atomic_record(request, c, parsed=by_label[c["label"]], **shared)
                    record["comparison_projection"] = {"label": c["label"], "parent_file": "observation.json", "parent_sha256": sha256(stage/"observation.json")}
                    record["raw_comparison_clips"] = [public_clip(item) for item in clips]
                    name = "clip-" + c["label"] + "-observation.json"
                    write_json(stage/name, record); atomic_files.append(name)
        if session:
            write_json(stage/"cost-summary.json", session.summary())
        return finish_run(stage, "observer-observe", deps, metadata={"result_file": "observation.json",
            "atomic_observation_files": atomic_files, "validation_status": report["validation_status"], "execution_status": status,
            "backend_identity": backend.identity, "claim_status_changes": "NONE_WITHOUT_SEPARATE_ADJUDICATION"})


@_serialized_hosted
def probe_backend(fixtures, output, *, backend, budget_usd=.50, request_budget_usd=.10,
                  queue_ledger=None, queue_budget_usd=2., dry_run=False):
    from .auditory_observer import score_probe
    from .providers.openai_common import estimate_cost
    folder = Path(fixtures).resolve()
    verify_run(folder)
    questions_path = folder/"observer-input"/"questions.json"
    answers_path = folder/"adjudication"/"answers.json"
    questions, answers = read_json(questions_path), read_json(answers_path)
    protocol = "v2" if questions.get("schema") == "ave.blinded-audio-probe.v2" else "v1"
    trials, truth = questions.get("trials"), answers.get("trials")
    if not isinstance(trials, list) or not isinstance(truth, list) or not 12 <= len(trials) <= 200 or len(trials) != len(truth):
        raise AVError("Require the existing bounded blinded semantic fixtures")
    # Validate coordinator-side answer balance and binding before any spending.
    score_probe([None]*len(trials), [r.get("answer") for r in truth])
    admitted, estimates, deps = [], [], [file_record(folder/"run.json"), file_record(questions_path), file_record(answers_path)]
    seen = set()
    for trial, answer in zip(trials, truth):
        if (not isinstance(trial, dict) or set(trial) != {"trial_id", "clips", "question"}
            or trial["trial_id"] in seen or trial["trial_id"] != answer.get("trial_id")
            or answer.get("family") not in {"pitch_direction", "signal_presence"}
            or not isinstance(trial["clips"], list) or len(trial["clips"]) != 2):
            raise AVError("Malformed or mismatched blinded probe trial")
        expected_question = ("Which clip has rising pitch? Return the zero-based index only." if answer["family"] == "pitch_direction"
            else "Which clip contains a tone rather than digital silence? Return the zero-based index only.")
        if protocol == "v2":
            expected_question = expected_question.replace("Return the zero-based index only.", 'Return only JSON {"choice": 0} or {"choice": 1}.')
        if trial["question"] != expected_question:
            raise AVError("Blinded probe question differs from the neutral fixture contract")
        seen.add(trial["trial_id"])
        clips = []
        for i, name in enumerate(trial["clips"]):
            if not isinstance(name, str) or len(name) != 36 or not name.endswith(".wav") or any(c not in "0123456789abcdef" for c in name[:-4]):
                raise AVError("Probe inputs must use opaque fixture WAV names")
            path = folder/"observer-input"/name
            if path.is_symlink():
                raise AVError("Probe audio cannot be a symlink")
            wav = wav_identity(path)
            if wav["duration_seconds"] != 1 or wav["sample_rate_hz"] != 16000 or wav["channels"] != 1 or wav["bits_per_sample"] != 16:
                raise AVError("Unexpected blinded fixture sample framing")
            clips.append({"label": str(i), "clip_sha256": sha256(path), "duration_seconds": 1., "execution_audio_path": str(path)})
            deps.append(file_record(path, "blinded_probe_audio"))
        # No answer, family, seed, original filename or adjudication path is sent.
        admitted.append({"trial_id": trial["trial_id"], "question": trial["question"], "clips": clips})
        instruction = ('Return ONLY a JSON object with exactly one key, choice, whose value is the integer 0 or 1.'
                       if protocol == "v2" else "Return the zero-based clip index (0 or 1) only; no explanation.")
        labels = "\n".join(backend.clip_instruction(c) for c in clips) if hasattr(backend, "clip_instruction") else ""
        estimates.append(estimate_cost([1., 1.], trial["question"] + "\n" + instruction + "\n" + labels,
                                       max_output_tokens=128 if protocol == "v2" else 16, model=backend.identity["model_revision"]))
    if protocol == "v2":
        paired = {}
        for index, row in enumerate(truth):
            if not isinstance(row.get("pair_id"), str):
                raise AVError("Probe v2 requires declared reversed-order pairs")
            paired.setdefault((row["family"], row["pair_id"]), []).append(index)
        for indices in paired.values():
            if len(indices) != 2 or {truth[i]["answer"] for i in indices} != {0, 1}:
                raise AVError("Probe v2 pair is unbalanced")
            a, b = indices
            if [c["clip_sha256"] for c in admitted[a]["clips"]] != [c["clip_sha256"] for c in admitted[b]["clips"]][::-1]:
                raise AVError("Probe v2 reverse pair does not contain the same audio in opposite order")
        for family in ("pitch_direction", "signal_presence"):
            labels = [r["answer"] for r in truth if r["family"] == family]
            if len(labels) < 6 or sum(labels)*2 != len(labels):
                raise AVError("Probe v2 correct positions must balance within every family")
    plan = {"schema": "ave.observer-probe-plan.v1", "backend_identity": backend.identity,
        "trials": len(trials), "audio_seconds": len(trials)*2, "estimated_cost_usd": round(sum(e["estimated_cost_usd"] for e in estimates), 9),
        "estimate_status": "PLANNING_ESTIMATE", "estimates": estimates, "submitted": False,
        "scope": "Synthetic nonlexical input-influence test; no acting reliability implied"}
    if not dry_run:
        authorize(backend.allow_remote_media, backend.credential_env)
        if any(e["estimated_cost_usd"] > request_budget_usd for e in estimates) or plan["estimated_cost_usd"] > min(budget_usd, queue_budget_usd):
            raise HostedError("BUDGET_GUARD", "The complete probe plan exceeds its request/run/queue planning budget")
    with output_transaction(output, [folder]) as stage:
        write_json(stage/"probe-plan.json", plan)
        if dry_run:
            return finish_run(stage, "observer-probe-estimate", deps, metadata={"result_file": "probe-plan.json", "execution_status": "PLANNED_NOT_SUBMITTED"})
        session = BudgetSession(stage, budget_usd=budget_usd, request_budget_usd=request_budget_usd,
                                queue_ledger=queue_ledger, queue_budget_usd=queue_budget_usd,
                                model=backend.identity["model_revision"])
        backend.session = session
        backend.precheck()
        records, predictions, error = [], [], None
        for fixture in admitted:
            try:
                result = backend.probe_capability(fixture, {"prompt_revision": backend.identity["prompt_revision"], "protocol": protocol})
                predictions.append(result["prediction"])
                records.append({"trial_id": fixture["trial_id"], **result})
            except HostedError as exc:
                provider_receipt = backend.last_receipt or {}
                reported_cost = (provider_receipt.get("usage") or {}).get("actual_cost_usd")
                returned_model = provider_receipt.get("returned_model")
                # A completed, accounted-for malformed answer is a wrong trial,
                # not a transport outage. Never retry or repair its response.
                counted_wrong = (exc.code == "PROVIDER_RESPONSE_INVALID"
                    and isinstance(provider_receipt.get("raw_response"), str)
                    and type(reported_cost) in (float, int) and math.isfinite(reported_cost) and reported_cost >= 0
                    and isinstance(returned_model, str)
                    and model_matches(backend.identity["model_revision"], returned_model))
                records.append({"trial_id": fixture["trial_id"], "prediction": None, "error_code": exc.code,
                    "counted_as_incorrect": counted_wrong, "provider_receipt": backend.last_receipt})
                if counted_wrong:
                    predictions.append(None)
                    continue
                error = exc
                break
        scored_predictions = predictions + [None]*(len(truth)-len(predictions)) if protocol == "v2" else predictions
        score = score_probe(scored_predictions, [r["answer"] for r in truth]) if len(scored_predictions) == len(truth) else None
        families = {}
        for family in ("pitch_direction", "signal_presence"):
            indices = [i for i, row in enumerate(truth) if row["family"] == family]
            families[family] = {"trials": len(indices), "correct": sum(predictions[i] == truth[i]["answer"] for i in indices if i < len(predictions))}
            if protocol == "v2":
                f = families[family]
                f["chance_one_sided_p"] = sum(math.comb(f["trials"], k) for k in range(f["correct"], f["trials"]+1))/2**f["trials"]
                f["passed"] = f["correct"]/f["trials"] >= .8 and f["chance_one_sided_p"] <= .05
        returned_models = {r.get("provider_receipt", {}).get("returned_model") for r in records if r.get("provider_receipt")}
        passed = bool(score and score["passed"] and len(returned_models) == 1 and None not in returned_models
            and all(v["trials"] >= 6 and v["correct"]/v["trials"] >= .8 for v in families.values()))
        receipt = {"schema": "ave.hosted-auditory-capability.v1", "interface": backend.identity["api_route"],
            "model_revision": backend.identity["model_revision"], "adapter_revision": backend.identity["adapter_revision"],
            "backend_identity": backend.identity, "status": "PROBE_PASSED" if passed else "PROBE_FAILED", "semantic_probe_passed": passed,
            "fixture_sha256": sha256(folder/"run.json"), "questions_sha256": sha256(questions_path), "private_answers_sha256": sha256(answers_path),
            "probe_result": score, "family_results": families, "completed_trials": len(predictions),
            "returned_model": next(iter(returned_models)) if len(returned_models) == 1 else None,
            "execution_status": error.code if error else "PROBE_PASSED" if passed else "BACKEND_PROBE_FAILED",
            "created_at": utc_now(), "execution_mode": backend.identity.get("execution_mode"),
            "authorization": {"provider": "openai", "remote_media_authorized": True},
            "scope": "Input influence on blinded pitch and signal-presence contrasts only; not Japanese acting/emotion qualification; test transport passes do not authorize a live route"}
        if protocol == "v2":
            pair_results = {}
            for family in ("pitch_direction", "signal_presence"):
                groups = [indices for (fam, _), indices in paired.items() if fam == family]
                correct_pairs = sum(all(i < len(predictions) and predictions[i] == truth[i]["answer"] for i in indices) for indices in groups)
                n_pairs = len(groups)
                p_pairs = sum(math.comb(n_pairs, k) for k in range(correct_pairs, n_pairs+1))/2**n_pairs
                pair_results[family] = {"pairs": n_pairs, "correct_pairs": correct_pairs,
                    "chance_one_sided_p": p_pairs, "passed": n_pairs >= 6 and correct_pairs/n_pairs >= .8 and p_pairs <= .05}
            outcomes = []
            for index, record in enumerate(records):
                pred = record.get("prediction")
                provider = record.get("provider_receipt") or {}
                accounted = (provider.get("usage") or {}).get("actual_cost_usd") is not None
                kind = ("TRANSPORT_FAILURE" if provider.get("status") != "REQUEST_ACCEPTED" and not accounted
                        else "MALFORMED_RESPONSE" if pred is None else
                        "CORRECT" if pred == truth[index]["answer"] else "INCORRECT_PERCEPTUAL_JUDGMENT")
                outcomes.append({"trial_id": record["trial_id"], "family": truth[index]["family"], "outcome": kind})
            passed = passed and all(f["passed"] for f in families.values()) and all(f["passed"] for f in pair_results.values()) and error is None
            receipt.update(schema="ave.hosted-auditory-capability.v2", protocol="balanced-reversed-json-v2",
                status="PROBE_PASSED" if passed else "PROBE_FAILED", semantic_probe_passed=passed,
                outcomes=outcomes, overall_failure_rate=1-score["correct"]/score["trials"],
                not_attempted_trials=len(truth)-len(records),
                paired_results=pair_results, execution_status=error.code if error else "PROBE_PASSED" if passed else "BACKEND_PROBE_FAILED",
                criteria={"overall_min_accuracy": .8, "overall_max_p": .01, "family_min_accuracy": .8, "family_max_p": .05,
                    "minimum_pairs_per_family": 6, "pair_max_p": .05, "pair_null_success_probability": .5,
                    "pair_scope": "Both orders must be correct; conservative binary null; no independent-trial claim for reversed calls"},
                task_qualification="NONE_INPUT_INFLUENCE_ONLY")
        write_json(stage/"probe-responses.json", {"schema": "ave.observer-probe-responses.v1", "trials": records})
        write_json(stage/"capability.json", receipt)
        write_json(stage/"cost-summary.json", session.summary())
        return finish_run(stage, "observer-probe", deps, metadata={"result_file": "capability.json",
            "execution_status": receipt["execution_status"], "semantic_probe_passed": passed, "backend_identity": backend.identity})
