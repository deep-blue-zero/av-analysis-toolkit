"""Provider-independent observer contract, blinded probes, human-review bridge.

No paid or remote backend is configured by this module. Local adapters are
explicit operator-supplied implementations; mock output is never audio evidence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import random
from typing import Protocol

from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, utc_now, write_json
from .inventory import verify_run, text_value
from .performance_cache import digest

NEUTRAL_PROMPT = ("Describe only audible pitch movement, pace, loudness, hesitation, articulation and vocal tension. "
    "Separate acoustic descriptions from emotional readings. Give timed observations, alternatives, interference and abstentions. "
    "Do not infer speaker identity from filenames, subtitles or camera focus.")


class ObserverBackend(Protocol):
    identity: dict
    def probe_capability(self, fixture: dict, configuration: dict) -> dict: ...
    def observe(self, request: dict, source_bound_clip: dict, optional_text=None) -> str: ...


class MockObserver:
    identity = {"type": "mock", "model_revision": "fixture-only-v1", "adapter_revision": "1"}
    def probe_capability(self, fixture, configuration):
        return {"status": "NOT_CONFIGURED", "semantic_probe_passed": False, "scope": "Mock tests transport only"}
    def observe(self, request, source_bound_clip, optional_text=None):
        return json.dumps({"observations": [], "alternatives": [], "interference": [],
                           "abstentions": ["Mock backend has no auditory perception"], "confidence": "unknown"})


def validate_response(raw, duration):
    # Preserve malformed responses instead of manufacturing observations.
    try:
        obj = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, TypeError) as exc:
        raise AVError("Observer response is not strict JSON") from exc
    keys = {"observations", "alternatives", "interference", "abstentions", "confidence"}
    if not isinstance(obj, dict) or set(obj) != keys or obj["confidence"] not in {"high", "medium", "low", "unknown"}:
        raise AVError("Observer response schema is invalid")
    for name in ("observations", "alternatives", "interference", "abstentions"):
        if not isinstance(obj[name], list) or len(obj[name]) > 100:
            raise AVError("Observer response list is invalid or unbounded")
    for item in obj["observations"]:
        if not isinstance(item, dict) or set(item) != {"start_s", "end_s", "description"}:
            raise AVError("Auditory observation needs clip-relative boundaries and a description")
        a, b = item["start_s"], item["end_s"]
        if any(type(v) not in (float,int) or not math.isfinite(v) for v in (a,b)) or not 0 <= a < b <= duration:
            raise AVError("Auditory observation exceeds its clip")
        text_value(item["description"], "auditory description")
    for name in ("alternatives", "interference", "abstentions"):
        for item in obj[name]:
            text_value(item, name)
    return obj


def observe_clip(witness_run, request_file, output, *, backend=None, capability=None):
    folder = Path(witness_run).resolve()
    verify_run(folder)
    clip = read_json(folder / "witness.json")
    if clip["identity"]["sample_frames"]/clip["identity"]["sample_rate_hz"] > 30:
        raise AVError("Auditory observer clips must be at most 30 s; split a longer listening witness")
    request = read_json(request_file)
    if not isinstance(request,dict) or set(request)-{"request_id","question","source_sha256","stage","text","context","character_names","expected_emotion"}:
        raise AVError("Unknown observer request fields; credentials/configuration do not belong in prompts or evidence")
    text_value(request.get("request_id"), "request ID")
    text_value(request.get("question"), "observer question")
    if request.get("stage", 1) not in {1,2}:
        raise AVError("Observer stage must be 1 or 2")
    if request.get("stage", 1) == 1 and any(request.get(k) for k in ("text", "context", "character_names", "expected_emotion")):
        raise AVError("Stage 1 must minimize supplied context; use logged stage 2 for interpretation")
    if request.get("source_sha256") != clip["source_sha256"]:
        raise AVError("Observer question is bound to a different source")
    backend = backend or MockObserver()
    status = "NOT_CONFIGURED"
    deps = [file_record(folder / "run.json"), file_record(folder / "audio.wav"), file_record(request_file)]
    if capability:
        from .analysis_preflight import capability_receipt
        receipt = capability_receipt(read_json(capability))
        if receipt.get("backend_identity") != backend.identity:
            raise AVError("Capability probe belongs to another adapter/model revision")
        status = receipt["status"]
        deps.append(file_record(capability))
    is_mock = backend.identity.get("type") == "mock"
    if not is_mock and status != "PROBE_PASSED":
        raise AVError("Real auditory backend requires a matching passed semantic probe")
    if not is_mock and backend.identity.get("route_type") not in {"local", "human"}:
        raise AVError("Hosted media submission requires a separately selected and authorized provider adapter")
    prompt = NEUTRAL_PROMPT + "\nQuestion: " + request["question"]
    actual_request = dict(request, prompt=prompt)
    supplied = {k: request[k] for k in ("text", "context", "character_names", "expected_emotion") if k in request}
    started = utc_now()
    raw = backend.observe(actual_request, dict(clip, execution_audio_path=str(folder / "audio.wav")), supplied or None)
    if not isinstance(raw,str) or len(raw.encode("utf-8"))>1048576:
        raise AVError("Auditory adapter response exceeds the 1 MiB receipt budget")
    validation_error = None
    try:
        parsed = validate_response(raw, clip["identity"]["sample_frames"]/clip["identity"]["sample_rate_hz"])
    except AVError as exc:
        parsed, validation_error = None, str(exc)
    with output_transaction(output, [folder, request_file] + ([capability] if capability else [])) as stage:
        report = {"schema": "ave.auditory-observation.v1", "observer_type": "mock" if is_mock else "model_audio",
            "backend_identity": backend.identity, "request_id": request["request_id"], "source_sha256": clip["source_sha256"],
            "source_interval_seconds": clip["source_interval_seconds"], "clip_sha256": clip["artifact_sha256"],
            "clip_to_source_mapping": clip["review_mapping"], "transformations": clip["transformations"],
            "sample_rate_hz": clip["identity"]["sample_rate_hz"], "channels": clip["identity"]["channels"],
            "prompt": prompt, "prompt_sha256": digest(prompt), "stage": request.get("stage", 1), "supplied_context": supplied,
            "raw_response": raw, "parsed": parsed, "validation_status": "INVALID" if validation_error else "VALID",
            "validation_error": validation_error, "capability_status": status,
            "started_at": started, "completed_at": utc_now(), "confidence_calibration": "UNCALIBRATED_REVIEWER_JUDGMENT",
            "scope": "Attributed auditory witness; never the coordinating model's own hearing"}
        write_json(stage / "observation.json", report)
        return finish_run(stage, "observer-observe", deps, metadata={"result_file": "observation.json", "validation_status": report["validation_status"]})


def score_probe(predictions, answers):
    """One-sided binomial test; acceptance also needs accuracy and enough trials."""
    if len(answers) < 12 or len(predictions) != len(answers) or any(a not in {0,1} for a in answers):
        raise AVError("Capability probe requires at least 12 balanced binary trials")
    if abs(sum(answers)-len(answers)/2) > 1:
        raise AVError("Probe answer distribution must be balanced")
    correct = sum(p == a for p,a in zip(predictions, answers))
    n = len(answers)
    p = sum(math.comb(n,k) for k in range(correct,n+1)) / 2**n
    return {"trials": n, "correct": correct, "accuracy": correct/n, "chance_one_sided_p": p,
            "passed": correct/n >= .8 and p <= .01,
            "scope": "Evidence of input influence on these contrasts; not validation of acting or emotional interpretation"}


def make_probe(output, *, trials=24, seed=9271):
    import numpy as np
    import wave
    if type(trials) is not int or not 12 <= trials <= 200 or trials % 2:
        raise AVError("Require an even 12..200 semantic probe trials")
    rng = random.Random(seed)
    pairs = []
    truth = []
    answers = [0]*(trials//2)+[1]*(trials//2)
    rng.shuffle(answers)
    with output_transaction(output) as stage:
        private = stage / "adjudication"
        clips = stage / "observer-input"
        private.mkdir(); clips.mkdir()
        rate = 16000
        t = np.arange(rate) / rate
        for i, answer in enumerate(answers):
            family = "pitch_direction" if i % 2 == 0 else "signal_presence"
            if family == "pitch_direction":
                choices = [.2*np.sin(2*np.pi*(220*t + 110*t*t)), .2*np.sin(2*np.pi*(440*t - 110*t*t))]
                question = "Which clip has rising pitch? Return the zero-based index only."
            else:
                choices = [.2*np.sin(2*np.pi*330*t), np.zeros_like(t)]
                question = "Which clip contains a tone rather than digital silence? Return the zero-based index only."
            ordered = choices if answer == 0 else choices[::-1]
            names = []
            for samples in ordered:
                name = "%032x.wav" % rng.getrandbits(128)
                with wave.open(str(clips / name), "wb") as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
                    f.writeframes((samples*32767).astype("<i2").tobytes())
                names.append(name)
            pairs.append({"trial_id": "trial-%03d" % i, "clips": names, "question": question})
            truth.append({"trial_id": pairs[-1]["trial_id"], "answer": answer, "family": family})
        write_json(clips / "questions.json", {"trials": pairs, "policy": "Only this directory belongs in observer context"})
        write_json(private / "answers.json", {"trials": truth, "seed": seed, "not_for_observer": True})
        return finish_run(stage, "observer-probe-fixtures", [], metadata={"result_file": "observer-input/questions.json", "trials": trials,
            "scope": "Synthetic nonlexical capability fixtures; no real auditory backend was tested"})


def import_human_review(review_import_run, output):
    """Bridge existing performance-review-import without substituting an empty form."""
    folder = Path(review_import_run).resolve()
    verify_run(folder)
    report = read_json(folder / "observations.json")
    if report.get("schema") != "ave.performance-review.observations.v1":
        raise AVError("Require a validated existing performance review import")
    with output_transaction(output, [folder]) as stage:
        write_json(stage / "human-witnesses.json", {"schema": "ave.human-auditory-witnesses.v1", "parent_manifest_sha256": sha256(folder / "run.json"),
            "observer_type": "human_audio", "records": report.get("records", []),
            "scope": "Existing source-bound human declarations retained; no new listening or model perception is asserted"})
        return finish_run(stage, "observer-human-import", [file_record(folder / "run.json"), file_record(folder / "observations.json")],
                          metadata={"result_file": "human-witnesses.json"})
