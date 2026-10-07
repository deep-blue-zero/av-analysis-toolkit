"""Synchronous text-output audio observer; HTTP details stay inside the adapter."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import threading

from ..common import AVError, sha256
from .openai_common import (HostedError, MODEL, authorize, estimate_cost, http_transport, model_matches, pricing_for_model)

PROMPT_REVISION = "auditory-neutral-json-v1"
ADAPTER_REVISION = "openai-audio-sync-1"
SINGLE_SCHEMA = ('Return ONLY JSON with exactly these keys: observations (array of objects with start_s, end_s, description), '
    'alternatives (array of strings), interference (array of strings), abstentions (array of strings), '
    'confidence (high, medium, low, or unknown). Times are seconds relative to the provided clip, starting at 0. '
    'No markdown, extra keys, invented certainty, speaker names or narrative guesses. Empty observations are allowed when abstaining. '
    'Be concise: at most three timed observations and two short items in each notes array.')
COMPARISON_SCHEMA = ('Return ONLY JSON with exactly these keys: clips (array of objects with label and observation), '
    'comparison (array of strings), alternatives (array of strings), interference (array of strings), '
    'abstentions (array of strings), confidence (high, medium, low, or unknown). '
    'Each observation has exactly observations, alternatives, interference, abstentions, confidence. '
    'Each timed observation has exactly start_s, end_s, description. Include every supplied clip label once. '
    'Use each clip\'s own seconds starting at 0; do not concatenate timelines. No markdown or extra keys. '
    'Be concise: at most two timed observations per clip and two short items in each notes array.')


class OpenAIAudioObserver:
    def __init__(self, *, model=MODEL, credential_env="OPENAI_API_KEY", allow_remote_media=None,
                 max_output_tokens=600, timeout_s=60, session=None, transport=None):
        # Explicit priced model selection only, never automatic fallback.
        self.pricing = pricing_for_model(model)
        if not isinstance(credential_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", credential_env):
            raise AVError("Credential configuration accepts only an environment-variable name")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4000:
            raise AVError("Require a bounded 1..4000 text-output token budget")
        if type(timeout_s) not in (float, int) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 60:
            raise AVError("Require a request timeout of at most 60 s")
        if transport is not None and not callable(transport):
            raise AVError("Injected test transport must be callable; invalid transport never falls back to live HTTP")
        self.identity = {"type": "model_audio", "backend_id": "openai-audio", "provider": "openai", "route_type": "hosted",
            "model_revision": model, "adapter_revision": ADAPTER_REVISION, "api_route": "/v1/chat/completions",
            "prompt_revision": PROMPT_REVISION, "execution_mode": "TEST_DOUBLE" if transport is not None else "HOSTED_LIVE"}
        from ..auditory_observer import NEUTRAL_PROMPT
        from ..auditory_profiles import PROFILE_REVISION, TASK_PROMPTS
        self.identity["task_profile_revision"] = PROFILE_REVISION
        self.identity["prompt_sha256"] = hashlib.sha256((NEUTRAL_PROMPT+SINGLE_SCHEMA+COMPARISON_SCHEMA+
            json.dumps(TASK_PROMPTS, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()
        self.identity["adapter_sha256"] = sha256(__file__)
        self.identity["transport_sha256"] = sha256(Path(__file__).with_name("openai_common.py"))
        self.credential_env = credential_env
        self.allow_remote_media = allow_remote_media
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.session = session
        self.transport = transport if transport is not None else http_transport
        self.last_receipt = None
        self.admitted_capability = None
        self.admitted_returned_model = None
        self._operation_lock = threading.Lock()

    @contextmanager
    def execution_guard(self):
        if not self._operation_lock.acquire(blocking=False):
            raise HostedError("BUDGET_GUARD", "This synchronous backend is already in use; concurrent reuse is refused")
        try:
            yield
        finally:
            self.session = None
            self.admitted_capability = None
            self.admitted_returned_model = None
            self._operation_lock.release()

    def precheck(self):
        authorize(self.allow_remote_media, self.credential_env)
        if self.session is None:
            raise HostedError("BUDGET_GUARD", "A run-local budget session is required before submission")

    def response_instruction(self, comparison=False):
        return COMPARISON_SCHEMA if comparison else SINGLE_SCHEMA

    def estimate_cost(self, request, source_bound_clip, optional_text=None):
        clips = source_bound_clip.get("clips", [source_bound_clip])
        text = request["prompt"] + "\n" + self.response_instruction(len(clips) > 1)
        if optional_text:
            text += "\nExplicit stage 2 context:\n" + json.dumps(optional_text, ensure_ascii=False, allow_nan=False)
        return estimate_cost([c["duration_seconds"] for c in clips], text, max_output_tokens=self.max_output_tokens,
                             model=self.identity["model_revision"], request=request)

    def _complete(self, request, clips, *, optional_text=None, probe=False):
        self.last_receipt = None
        key = authorize(self.allow_remote_media, self.credential_env)
        if self.session is None:
            raise HostedError("BUDGET_GUARD", "A run-local budget session is required before submission")
        from ..auditory_profiles import request_policy, validate_audio_bounds
        policy = request_policy(request)
        validate_audio_bounds([c["duration_seconds"] for c in clips], policy)
        if policy["explicit_request_budget_required"] and self.session.request_limit > policy["section_budget_usd"]:
            raise HostedError("BUDGET_GUARD", "Configured request budget exceeds the explicit musical-section ceiling")
        text = request["prompt"]
        text += "\nReturn the zero-based clip index (0 or 1) only; no explanation." if probe else "\n" + self.response_instruction(len(clips) > 1)
        if optional_text:
            text += "\nExplicit stage 2 context:\n" + json.dumps(optional_text, ensure_ascii=False, allow_nan=False)
        maximum = 16 if probe else self.max_output_tokens
        estimate = estimate_cost([c["duration_seconds"] for c in clips], text, max_output_tokens=maximum,
                                 model=self.identity["model_revision"], request=request)
        summaries = [{k: c[k] for k in ("label", "clip_sha256", "duration_seconds")}
                     | {k: c[k] for k in ("source_sha256", "source_interval_seconds", "stream_index", "transformations") if k in c}
                     for c in clips]
        # Base64 exists only in memory while constructing and sending this request.
        content = [{"type": "text", "text": text}]
        for c in clips:
            path = Path(c["execution_audio_path"])
            if path.stat().st_size > policy["max_audio_bytes"]:
                raise AVError("Provider input audio bytes no longer match the source-bound witness")
            audio_bytes = path.read_bytes()
            if hashlib.sha256(audio_bytes).hexdigest() != c["clip_sha256"]:
                raise AVError("Provider input audio bytes no longer match the source-bound witness")
            content.append({"type": "text", "text": "Clip " + c["label"] + "; independent time origin 0 seconds."})
            content.append({"type": "input_audio", "input_audio": {"data": base64.b64encode(audio_bytes).decode("ascii"), "format": "wav"}})
        try:
            ticket = self.session.reserve(estimate, request_id=request["request_id"], identity=self.identity,
                                          clips=summaries, purpose="semantic_probe" if probe else "auditory_observation")
        except HostedError as exc:
            self.last_receipt = {"authorization": {"provider":"openai","remote_media_authorized":True},
                "estimate":estimate, "usage":None, "status":exc.code, "raw_response":None,
                "submission_attempted":False, "planned_audio":summaries, "submitted_audio":[], "execution_mode":self.identity["execution_mode"]}
            raise
        payload = {"model": self.identity["model_revision"], "modalities": ["text"], "store": False,
                   "max_completion_tokens": maximum, "messages": [{"role": "user", "content": content}]}
        usage, returned_model, raw, failure = None, None, None, None
        failure_details = {}
        try:
            envelope = self.transport(payload, key, self.timeout_s)
            if not isinstance(envelope, dict):
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider envelope is not an object")
            usage = envelope.get("usage")
            value = envelope.get("model")
            returned_model = value if isinstance(value, str) and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.-]+", value) else None
            choices = envelope.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider did not return one text completion")
            message = choices[0].get("message")
            raw = message.get("content") if isinstance(message, dict) else None
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1048576:
                raw = None
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider did not return bounded text")
            if key in raw:
                raw = raw.replace(key, "[REDACTED_CREDENTIAL]")
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider text echoed a credential; retained text was redacted")
            echoed_audio = False
            for part in content:
                if part["type"] == "input_audio" and part["input_audio"]["data"] in raw:
                    raw = raw.replace(part["input_audio"]["data"], "[REDACTED_AUDIO_DATA]")
                    echoed_audio = True
            if echoed_audio:
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider text echoed audio data; retained text was redacted")
            if not model_matches(self.identity["model_revision"], returned_model):
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider did not report the configured audio model")
            if not probe and returned_model != self.admitted_returned_model:
                raise HostedError("BACKEND_NOT_PROBED", "Provider model identity changed since the recorded probe; run a new probe")
            if choices[0].get("finish_reason") != "stop" or message.get("audio") or message.get("tool_calls") or message.get("refusal"):
                raise HostedError("PROVIDER_RESPONSE_INVALID", "Text completion was truncated, refused or contained unexpected output")
        except HostedError as exc:
            # Do not trust exception messages from an injected HTTP client;
            # SDKs can include the entire authenticated request in them.
            message = "Provider returned no admissible observation; no automatic retry or fallback"
            if re.fullmatch(r"PROVIDER_(REQUEST_FAILED|RATE_LIMITED): Provider returned HTTP [0-9]{3}", str(exc)):
                message = str(exc).split(": ",1)[1]
            # Injected clients remain untrusted even when they use HostedError.
            for name in ("http_status", "provider_error_code", "provider_error_type", "provider_error_param"):
                value = exc.details.get(name)
                if name == "http_status":
                    if type(value) is int and 100 <= value <= 599:
                        failure_details[name] = value
                elif (isinstance(value, str) and len(value) <= 128
                      and re.fullmatch(r"[A-Za-z0-9_./\[\]-]+", value)
                      and key not in value and not value.startswith("sk-")):
                    failure_details[name] = value
            failure = HostedError(exc.code, message)
        except Exception:
            # An injected client may put secrets in exception text. Never repeat it.
            failure = HostedError("PROVIDER_REQUEST_FAILED", "Provider transport failed; no automatic retry or fallback")
        settled = self.session.settle(ticket, usage=usage, status=failure.code if failure else "REQUEST_ACCEPTED", returned_model=returned_model)
        self.last_receipt = {"authorization": {"provider": "openai", "remote_media_authorized": True},
            "operation_id": ticket["operation_id"], "estimate": estimate, "usage": settled,
            "returned_model": returned_model, "raw_response": raw, "status": failure.code if failure else "REQUEST_ACCEPTED",
            "failure_details": failure_details,
            "submission_attempted": True,
            "retention_request": {"store": False, "scope": "Requests no stored completion; not a zero-retention guarantee"},
            "submitted_audio": summaries, "audio_policy": policy, "task_capability_status": "UNQUALIFIED",
            "execution_mode": self.identity["execution_mode"]}
        if failure:
            raise failure
        return raw

    def observe(self, request, source_bound_clip, optional_text=None):
        if self.admitted_capability != self.identity:
            raise HostedError("BACKEND_NOT_PROBED", "Observe through the source-bound runner with a matching semantic capability receipt")
        return self._complete(request, source_bound_clip.get("clips", [source_bound_clip]), optional_text=optional_text)

    def probe_capability(self, fixture, configuration):
        raw = self._complete({"request_id": fixture["trial_id"], "prompt": fixture["question"]}, fixture["clips"], probe=True)
        return {"raw_response": raw, "prediction": int(raw.strip()) if raw.strip() in {"0", "1"} else None,
                "provider_receipt": self.last_receipt}
