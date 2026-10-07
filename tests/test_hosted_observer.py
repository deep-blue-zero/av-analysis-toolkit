"""Zero-cost hosted integration, provenance, privacy and spending failure tests.

All transports are injected test doubles. Synthetic audio is generated locally;
no network or paid model is required, and test doubles cannot become AO evidence.
"""
import base64
import copy
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.audio_witness import audio_witness
from avevidence.auditory_observer import make_probe, observe_clip, score_probe, validate_response
from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.observer_execution import estimate_observation, observe_request, probe_backend, require_capability
from avevidence.observer_requests import load_request, wav_identity
from avevidence.providers.openai_audio import OpenAIAudioObserver
from avevidence.providers.openai_common import BudgetSession, HostedError, MODEL, estimate_cost, usage_cost, pricing_for_model

# Injected transport only: deliberately not an OpenAI credential-shaped token.
KEY = "synthetic-test-credential-never-valid-for-a-provider"
USAGE = {"prompt_tokens": 1100, "completion_tokens": 50, "total_tokens": 1150,
         "prompt_tokens_details": {"audio_tokens": 200, "cached_tokens": 0},
         "completion_tokens_details": {"audio_tokens": 0}}


def observation(description="A short steady tone is audible."):
    return {"observations": [{"start_s": .1, "end_s": .4, "description": description}],
            "alternatives": ["The source may be a synthetic signal."], "interference": [], "abstentions": [], "confidence": "low"}


def envelope(raw, *, usage=USAGE, model=MODEL, finish="stop"):
    return {"model": model, "choices": [{"finish_reason": finish, "message": {"content": raw}}], "usage": copy.deepcopy(usage)}


class Transport:
    def __init__(self, raw=None, error=None, usage=USAGE, model=MODEL, finish="stop"):
        self.calls = []
        self.raw, self.error, self.usage, self.model, self.finish = raw, error, usage, model, finish

    def __call__(self, payload, key, timeout):
        self.calls.append((copy.deepcopy(payload), key, timeout))
        if self.error:
            raise self.error
        raw = self.raw
        if raw is None:
            labels = [p["text"].split(";")[0].split()[-1] for p in payload["messages"][0]["content"]
                      if p["type"] == "text" and p["text"].startswith("Clip ")]
            obj = observation() if len(labels) == 1 else {"clips": [{"label": label, "observation": observation()} for label in labels],
                "comparison": ["Both clips contain steady tones."], "alternatives": [], "interference": [], "abstentions": [], "confidence": "low"}
            raw = json.dumps(obj)
        return envelope(raw, usage=self.usage, model=self.model, finish=self.finish)


class SemanticTransport(Transport):
    """Compute the two synthetic contrasts from audio bytes, never answer files."""
    def __call__(self, payload, key, timeout):
        import numpy as np
        self.calls.append((copy.deepcopy(payload), key, timeout))
        parts = payload["messages"][0]["content"]
        samples = []
        for p in parts:
            if p["type"] == "input_audio":
                with wave.open(io.BytesIO(base64.b64decode(p["input_audio"]["data"]))) as stream:
                    samples.append(np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2"))
        if "rising pitch" in parts[0]["text"]:
            def rising(x):
                quarter = len(x)//4
                crossings = lambda y: int(np.sum(np.diff(np.signbit(y)) != 0))
                return crossings(x[-quarter:]) > crossings(x[:quarter])
            index = 0 if rising(samples[0]) else 1
        else:
            index = 0 if np.any(samples[0]) else 1
        return envelope(str(index))


class HostedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.shared_temp = tempfile.TemporaryDirectory()
        cls.shared = Path(cls.shared_temp.name)
        cls.source = cls.shared/"source-with-private-character-name.wav"
        rate = 16000
        t = np.arange(rate*2)/rate
        with wave.open(str(cls.source), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
            f.writeframes((np.sin(2*np.pi*220*t)*8000).astype("<i2").tobytes())
        cls.a, cls.b = cls.shared/"witness-A", cls.shared/"witness-B"
        audio_witness(cls.source, cls.a, start=.2, end=.8)
        audio_witness(cls.source, cls.b, start=1.2, end=1.8)
        cls.fixtures = cls.shared/"fixtures"
        make_probe(cls.fixtures, trials=12)

    @classmethod
    def tearDownClass(cls):
        cls.shared_temp.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"AVE_TEST_OPENAI_KEY": KEY})
        self.env.start()
        self.no_http = patch("avevidence.providers.openai_audio.http_transport", side_effect=AssertionError("Unexpected live transport in offline test"))
        self.no_http.start()

    def tearDown(self):
        self.no_http.stop(); self.env.stop(); self.temporary.cleanup()

    def backend(self, transport=None, **options):
        return OpenAIAudioObserver(credential_env="AVE_TEST_OPENAI_KEY", allow_remote_media="openai", transport=transport or Transport(), **options)

    def request(self, *, comparison=False, **changes):
        value = {"request_id": "test-question-1", "question": "Describe audible pace and articulation.", "stage": 1}
        if comparison:
            value["clips"] = [{"label": "A", "witness_run": str(self.a)}, {"label": "B", "witness_run": str(self.b)}]
        else:
            value["source_sha256"] = sha256(self.source)
        value.update(changes)
        path = self.root/("request-%d.json" % len(list(self.root.glob("request-*.json"))))
        write_json(path, value)
        return path

    def capability(self, backend):
        score = score_probe([0,1]*12, [0,1]*12)
        value = {"schema": "ave.hosted-auditory-capability.v1", "interface": "/v1/chat/completions",
            "model_revision": backend.identity["model_revision"], "adapter_revision": backend.identity["adapter_revision"], "backend_identity": backend.identity,
            "status": "PROBE_PASSED", "semantic_probe_passed": True, "fixture_sha256": sha256(self.fixtures/"run.json"),
            "probe_result": score, "family_results": {"pitch_direction": {"trials":12,"correct":12}, "signal_presence": {"trials":12,"correct":12}},
            "returned_model": backend.identity["model_revision"]}
        path = self.root/("cap-%d.json" % len(list(self.root.glob("cap-*.json"))))
        write_json(path, value)
        return path

    def run_observer(self, *, transport=None, request=None, comparison=False, name="result", **options):
        backend = self.backend(transport)
        path = request or self.request(comparison=comparison)
        manifest = observe_request(path, self.root/name, witness_run=None if comparison else self.a,
            backend=backend, capability=self.capability(backend), **options)
        return manifest, read_json(self.root/name/"observation.json"), backend

    def test_registry_has_explicit_hosted_route_and_no_auto_choice(self):
        from avevidence.providers.registry import create_backend, providers
        self.assertEqual(providers()["default_backend"], "mock")
        self.assertEqual(create_backend().identity["type"], "mock")
        self.assertIn("openai-audio", [r["backend_id"] for r in providers()["providers"]])

    def test_environment_name_only_no_literal_keys_or_model_substitution(self):
        for options in ({"credential_env": KEY}, {"api_key": KEY}, {"model": "gpt-audio-mini"}):
            with self.assertRaises((AVError, TypeError)):
                OpenAIAudioObserver(**options)

    def test_missing_credential_sends_nothing(self):
        transport = Transport()
        backend = self.backend(transport)
        with patch.dict(os.environ, {"AVE_TEST_OPENAI_KEY": ""}):
            with self.assertRaisesRegex(HostedError, "OPENAI_CREDENTIAL_NOT_CONFIGURED"):
                observe_request(self.request(), self.root/"absent", witness_run=self.a, backend=backend, capability=self.capability(backend))
        self.assertEqual(transport.calls, [])

    def test_key_presence_does_not_authorize_remote_media(self):
        backend = self.backend()
        backend.allow_remote_media = None
        with self.assertRaisesRegex(HostedError, "HOSTED_MEDIA_NOT_AUTHORIZED"):
            observe_request(self.request(), self.root/"unauthorized", witness_run=self.a, backend=backend)
        self.assertEqual(backend.transport.calls, [])

    def test_missing_and_failed_probe_never_fall_back(self):
        backend = self.backend()
        with self.assertRaisesRegex(HostedError, "BACKEND_NOT_PROBED"):
            observe_request(self.request(), self.root/"unprobed", witness_run=self.a, backend=backend)
        cap = self.capability(backend)
        value = read_json(cap); value.update(status="PROBE_FAILED", semantic_probe_passed=False)
        cap.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(HostedError, "BACKEND_PROBE_FAILED"):
            observe_request(self.request(), self.root/"failed-probe", witness_run=self.a, backend=backend, capability=cap)
        self.assertEqual(backend.transport.calls, [])

    def test_probe_is_bound_to_model_adapter_prompt_route_and_transport(self):
        backend = self.backend()
        for field in ("model_revision", "adapter_revision", "prompt_revision", "api_route", "execution_mode"):
            cap = self.capability(backend)
            value = read_json(cap); value["backend_identity"][field] = "different"
            cap.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(HostedError, "BACKEND_NOT_PROBED"):
                require_capability(cap, backend)

    def test_probe_statistics_cannot_be_replaced_by_a_pass_label(self):
        backend = self.backend(); cap = self.capability(backend)
        value = read_json(cap); value["probe_result"]["correct"] = 12
        cap.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(HostedError, "BACKEND_PROBE_FAILED"):
            require_capability(cap, backend)

    def test_direct_provider_observe_cannot_skip_probe_admission(self):
        backend = self.backend()
        with self.assertRaisesRegex(HostedError, "BACKEND_NOT_PROBED"):
            backend.observe({"request_id":"r1", "prompt":"Describe sound"}, {})

    def test_request_parts_are_separate_and_output_is_text_only(self):
        transport = Transport()
        _, report, _ = self.run_observer(transport=transport, comparison=True)
        payload = transport.calls[0][0]
        self.assertEqual(payload["model"], MODEL); self.assertEqual(payload["modalities"], ["text"])
        self.assertIs(payload["store"], False); self.assertEqual(payload["max_completion_tokens"],600)
        parts = payload["messages"][0]["content"]
        audio = [p["input_audio"]["data"] for p in parts if p["type"] == "input_audio"]
        self.assertEqual(len(audio),2)
        self.assertEqual(base64.b64decode(audio[0]),(self.a/"audio.wav").read_bytes())
        self.assertEqual(base64.b64decode(audio[1]),(self.b/"audio.wav").read_bytes())
        self.assertEqual(report["validation_status"], "VALID")
        self.assertEqual([c["label"] for c in report["clips"]], ["A","B"])

    def test_stage1_does_not_forward_paths_names_methods_or_narrative_context(self):
        transport = Transport()
        self.run_observer(transport=transport)
        texts = "\n".join(p["text"] for p in transport.calls[0][0]["messages"][0]["content"] if p["type"] == "text")
        self.assertNotIn(self.source.name,texts); self.assertNotIn(str(self.shared),texts)
        self.assertNotIn("test-question-1",texts)
        for field in ("expected_emotion", "character_names", "text", "context"):
            request = self.request(**{field:"Narrative outcome"})
            with self.assertRaises(AVError): load_request(request,self.a)

    def test_stage2_requires_and_preserves_the_stage1_baseline(self):
        self.run_observer(name="stage1")
        original = sha256(self.root/"stage1"/"observation.json")
        request = self.request(stage=2,stage1_run=str(self.root/"stage1"),context="Two explicitly supplied competing readings.")
        _, report, backend = self.run_observer(request=request,name="stage2")
        self.assertEqual(report["supplied_context"]["context"],"Two explicitly supplied competing readings.")
        self.assertEqual(report["stage1_parent"]["observation_sha256"],original)
        self.assertEqual(sha256(self.root/"stage1"/"observation.json"),original)
        text = backend.transport.calls[0][0]["messages"][0]["content"][0]["text"]
        self.assertIn("Explicit stage 2 context",text)

    def test_stage2_without_baseline_is_refused_before_submission(self):
        backend = self.backend()
        with self.assertRaisesRegex(AVError, "stage1_run"):
            observe_request(self.request(stage=2,text="Context"),self.root/"no-baseline",witness_run=self.a,
                backend=backend,capability=self.capability(backend))
        self.assertEqual(backend.transport.calls,[])

    def test_unknown_request_field_cannot_store_credentials(self):
        with self.assertRaises(AVError): load_request(self.request(api_key=KEY),self.a)

    def test_duplicate_request_and_response_keys_are_rejected(self):
        request = self.root/"duplicate.json"
        request.write_text('{"request_id":"a","request_id":"b"}',encoding="utf-8")
        with self.assertRaises(AVError): load_request(request,self.a)
        raw = json.dumps(observation()).replace('"confidence": "low"', '"confidence": "low", "confidence": "high"')
        with self.assertRaises(AVError): validate_response(raw,.6)

    def test_witness_hash_mutation_and_wrong_source_are_refused(self):
        mutated = self.root/"changed-witness"; shutil.copytree(self.a,mutated)
        with (mutated/"audio.wav").open("ab") as f: f.write(b"changed")
        with self.assertRaises(AVError): load_request(self.request(),mutated)
        with self.assertRaises(AVError): load_request(self.request(source_sha256=sha256(__file__)),self.a)

    def test_native_float64_wav_is_admitted_without_converting_samples(self):
        identity = wav_identity(self.a/"audio.wav")
        self.assertEqual(identity["bits_per_sample"],64); self.assertEqual(identity["sample_representation"],"IEEE_FLOAT")
        self.assertEqual(identity["sample_frames"],9600)
        before = sha256(self.a/"audio.wav")
        self.run_observer()
        self.assertEqual(sha256(self.a/"audio.wav"),before)

    def test_declared_clock_rounding_is_retained_but_not_invented(self):
        clone = self.root/"coarse-clock"; shutil.copytree(self.a,clone)
        witness = read_json(clone/"witness.json")
        witness["source_interval_seconds"][1] -= .0005
        witness["identity"].update(timestamp_quantization_tolerance_seconds=.001,max_timestamp_adjustment_seconds=.0005)
        def update_receipt():
            (clone/"witness.json").write_text(json.dumps(witness),encoding="utf-8")
            manifest = read_json(clone/"run.json")
            for artifact in manifest["artifacts"]:
                if artifact["path"] == "witness.json":
                    artifact.update(sha256=sha256(clone/"witness.json"),size_bytes=(clone/"witness.json").stat().st_size)
            (clone/"run.json").write_text(json.dumps(manifest),encoding="utf-8")
        update_receipt()
        _, clips, _, _ = load_request(self.request(),clone)
        self.assertEqual(clips[0]["source_clock_uncertainty_seconds"],.001)
        self.assertAlmostEqual(clips[0]["actual_source_interval_seconds"][1],.8)
        witness["identity"].update(timestamp_quantization_tolerance_seconds=0.,max_timestamp_adjustment_seconds=0.)
        update_receipt()
        with self.assertRaises(AVError): load_request(self.request(),clone)
        witness["identity"]["timestamp_quantization_tolerance_seconds"] = .1
        update_receipt()
        with self.assertRaises(AVError): load_request(self.request(),clone)

    def test_comparison_bounds_are_individual_not_concatenated(self):
        transport = Transport(raw=json.dumps({"clips":[{"label":"A","observation":observation()},
            {"label":"B","observation":dict(observation(),observations=[{"start_s":.7,"end_s":1.,"description":"Outside B"}])}],
            "comparison":[],"alternatives":[],"interference":[],"abstentions":[],"confidence":"low"}))
        manifest, report, _ = self.run_observer(transport=transport,comparison=True)
        self.assertEqual(manifest["metadata"]["execution_status"],"OBSERVATION_INVALID")
        self.assertIsNone(report["parsed"])
        self.assertEqual(manifest["metadata"]["atomic_observation_files"],[])

    def test_invalid_json_schema_and_nonfinite_boundaries_keep_raw_text(self):
        bad = ["not JSON", "```json\n{}\n```", '{"observations":[]}',
               json.dumps(dict(observation(),confidence=[])), json.dumps(dict(observation(),extra="field")),
               json.dumps(dict(observation(),observations=[{"start_s":False,"end_s":.4,"description":"Bad boolean"}]))]
        for i, raw in enumerate(bad):
            _, report, _ = self.run_observer(transport=Transport(raw=raw),name="invalid-%d" % i)
            self.assertEqual(report["raw_response"],raw); self.assertEqual(report["validation_status"],"INVALID")
            self.assertIsNone(report["parsed"])
            self.assertTrue((self.root/("invalid-%d" % i)/"provider-usage.jsonl").is_file())

    def test_truncated_provider_response_is_invalid_with_usage_retained(self):
        _, report, _ = self.run_observer(transport=Transport(finish="length"))
        self.assertEqual(report["execution_status"],"PROVIDER_RESPONSE_INVALID")
        self.assertIsNotNone(report["raw_response"]); self.assertIsNone(report["parsed"])
        self.assertEqual(report["provider_receipt"]["usage"]["provider_reported_usage"],USAGE)

    def test_rate_limit_and_transport_errors_have_no_retry_or_fallback(self):
        for i, error in enumerate([HostedError("PROVIDER_RATE_LIMITED","HTTP 429"), RuntimeError(KEY)]):
            transport = Transport(error=error)
            _, report, _ = self.run_observer(transport=transport,name="failure-%d" % i)
            self.assertEqual(len(transport.calls),1)
            self.assertIsNone(report["parsed"])
            self.assertNotIn(KEY,json.dumps(report))
            self.assertTrue(read_json(self.root/("failure-%d" % i)/"cost-summary.json")["billing_unresolved"])

    def test_http_failure_retains_only_bounded_machine_readable_fields(self):
        import urllib.error
        from avevidence.providers.openai_common import http_transport
        body = json.dumps({"error": {"code": "unsupported_parameter", "type": "invalid_request_error",
            "param": "max_completion_tokens", "message": KEY + " private request body"}}).encode()
        failure = urllib.error.HTTPError("https://api.openai.com/v1/chat/completions",400,"Bad request",{},io.BytesIO(body))
        with patch("avevidence.providers.openai_common.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(HostedError) as caught:
                http_transport({"model": MODEL},KEY,1)
        self.assertEqual(caught.exception.details, {"http_status":400,
            "provider_error_code":"unsupported_parameter", "provider_error_type":"invalid_request_error",
            "provider_error_param":"max_completion_tokens"})
        self.assertNotIn(KEY,str(caught.exception)+json.dumps(caught.exception.details))

    def test_oversized_http_error_body_is_discarded(self):
        import urllib.error
        from avevidence.providers.openai_common import http_transport
        failure = urllib.error.HTTPError("https://api.openai.com/v1/chat/completions",401,"Unauthorized",{},io.BytesIO(b'x'*9000))
        with patch("avevidence.providers.openai_common.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(HostedError) as caught:
                http_transport({"model": MODEL},KEY,1)
        self.assertEqual(caught.exception.details,{"http_status":401})

    def test_injected_failure_details_cannot_leak_credentials_or_payloads(self):
        error = HostedError("PROVIDER_REQUEST_FAILED",KEY,details={"http_status":400,
            "provider_error_code":"invalid_request_error", "provider_error_param":KEY,
            "provider_error_type":"private body with spaces", "Authorization":KEY})
        transport = Transport(error=error)
        _, report, _ = self.run_observer(transport=transport)
        self.assertEqual(report["provider_receipt"]["failure_details"],
            {"http_status":400,"provider_error_code":"invalid_request_error"})
        self.assertNotIn(KEY,json.dumps(report)); self.assertEqual(len(transport.calls),1)
        self.assertTrue(read_json(self.root/"result"/"cost-summary.json")["billing_unresolved"])

    def test_provider_model_change_invalidates_observation_even_after_spending(self):
        _, report, _ = self.run_observer(transport=Transport(model=MODEL+"-changed-snapshot"))
        self.assertEqual(report["execution_status"],"BACKEND_NOT_PROBED")
        self.assertIsNone(report["parsed"])

    def test_no_credential_headers_or_base64_are_persisted(self):
        transport = Transport()
        self.run_observer(transport=transport)
        audio_string = next(p["input_audio"]["data"] for p in transport.calls[0][0]["messages"][0]["content"] if p["type"] == "input_audio")
        for path in (self.root/"result").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(KEY,text); self.assertNotIn("Bearer ",text); self.assertNotIn(audio_string,text)

    def test_unexpected_credential_echo_is_redacted_and_invalid(self):
        _, report, _ = self.run_observer(transport=Transport(raw=json.dumps(observation(KEY))))
        self.assertNotIn(KEY,json.dumps(report))
        self.assertEqual(report["execution_status"],"PROVIDER_RESPONSE_INVALID")

    def test_run_and_request_budget_guards_do_not_send(self):
        for i, options in enumerate([{"budget_usd":0.}, {"request_budget_usd":0.}]):
            transport = Transport()
            _, report, _ = self.run_observer(transport=transport,name="budget-%d" % i,**options)
            self.assertEqual(transport.calls,[]); self.assertEqual(report["execution_status"],"BUDGET_GUARD")

    def test_estimate_needs_neither_key_nor_authorization_and_does_not_submit(self):
        transport = Transport(); backend = self.backend(transport); backend.allow_remote_media=None
        with patch.dict(os.environ,{"AVE_TEST_OPENAI_KEY":""}):
            value = estimate_observation(self.request(comparison=True),backend=backend)
        self.assertIs(value["submitted"],False); self.assertEqual(value["estimate"]["status"],"PLANNING_ESTIMATE")
        self.assertEqual(transport.calls,[])

    def test_unknown_usage_blocks_next_queue_call_and_reservation_is_not_refunded(self):
        queue = self.root/"queue.jsonl"
        _, report, _ = self.run_observer(transport=Transport(usage=None),queue_ledger=queue)
        summary = read_json(self.root/"result"/"cost-summary.json")
        self.assertTrue(summary["billing_unresolved"]); self.assertIsNone(summary["actual_cost_usd"])
        self.assertGreater(summary["committed_or_reserved_usd"],0)
        second = Transport()
        _, report, _ = self.run_observer(transport=second,name="second",queue_ledger=queue)
        self.assertEqual(second.calls,[]); self.assertEqual(report["execution_status"],"BUDGET_GUARD")

    def test_append_only_usage_records_estimate_actual_tokens_and_cost(self):
        self.run_observer()
        rows = [json.loads(line) for line in (self.root/"result"/"provider-usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in rows],["RESERVED","COMPLETED"])
        self.assertEqual(rows[1]["actual_cost_usd"],usage_cost(USAGE))
        self.assertEqual(rows[1]["operation_id"],rows[0]["operation_id"])
        self.assertEqual(rows[1]["clips"][0]["clip_sha256"],sha256(self.a/"audio.wav"))
        self.assertGreater(rows[0]["estimated_cost_usd"],0)

    def test_queue_budget_is_cumulative_and_cannot_be_increased_implicitly(self):
        queue = self.root/"queue.jsonl"
        self.run_observer(queue_ledger=queue,queue_budget_usd=.02)
        second = Transport()
        _, report, _ = self.run_observer(name="second",transport=second,queue_ledger=queue,queue_budget_usd=.02)
        self.assertEqual(report["execution_status"],"BUDGET_GUARD"); self.assertEqual(second.calls,[])
        _, report, _ = self.run_observer(name="changed-limit",transport=second,queue_ledger=queue,queue_budget_usd=.03)
        self.assertEqual(report["execution_status"],"BUDGET_GUARD")

    def test_pending_or_locked_ledger_fails_closed(self):
        session = BudgetSession(self.root/"budget-session")
        est = estimate_cost([1.],"Neutral question",max_output_tokens=16)
        backend = self.backend()
        session.reserve(est,request_id="r1",identity=backend.identity,clips=[],purpose="test")
        with self.assertRaisesRegex(HostedError,"Unresolved"):
            session.reserve(est,request_id="r2",identity=backend.identity,clips=[],purpose="test")
        (Path(str(session.local)+".lock")).write_text("locked",encoding="utf-8")
        with self.assertRaisesRegex(HostedError,"locked"):
            session.reserve(est,request_id="r3",identity=backend.identity,clips=[],purpose="test")

    def test_provider_usage_breakdown_is_required_for_billing_reconciliation(self):
        self.assertIsNotNone(usage_cost(USAGE))
        for value in (None, {}, dict(USAGE,total_tokens=0),dict(USAGE,prompt_tokens=True),dict(USAGE,prompt_tokens_details={})):
            self.assertIsNone(usage_cost(value))

    def test_explicit_pinned_model_keeps_its_own_payload_tariff_and_probe(self):
        model = "gpt-audio-2025-08-28"
        transport = Transport(model=model)
        backend = self.backend(transport, model=model)
        original = self.backend()
        with self.assertRaisesRegex(HostedError, "another"):
            require_capability(self.capability(original), backend)
        estimate = estimate_observation(self.request(), witness_run=self.a, backend=backend)
        self.assertEqual(estimate["estimate"]["pricing_snapshot"]["model"], model)
        observe_request(self.request(), self.root/"pinned", witness_run=self.a,
                        backend=backend, capability=self.capability(backend))
        self.assertEqual(transport.calls[0][0]["model"], model)
        report = read_json(self.root/"pinned"/"observation.json")
        self.assertEqual(report["execution_status"], "OBSERVATION_VALID")
        self.assertEqual(report["provider_receipt"]["usage"]["pricing_snapshot"], pricing_for_model(model))
        self.assertEqual(report["provider_receipt"]["usage"]["actual_cost_usd"], usage_cost(USAGE, model=model))

    def test_accounting_refuses_another_models_estimate_before_submission(self):
        model = "gpt-audio-2025-08-28"
        session = BudgetSession(self.root/"pinned-ledger", model=model)
        backend = self.backend(model=model)
        default_estimate = estimate_cost([1.], "neutral", max_output_tokens=16)
        with self.assertRaisesRegex(HostedError, "tariff differ"):
            session.reserve(default_estimate, request_id="r", identity=backend.identity, clips=[], purpose="test")
        self.assertFalse(session.local.exists())
        pinned_estimate = estimate_cost([1.], "neutral", max_output_tokens=16, model=model)
        with self.assertRaisesRegex(HostedError, "tariff differ"):
            session.reserve(pinned_estimate, request_id="r", identity=self.backend().identity, clips=[], purpose="test")
        self.assertFalse(session.local.exists())

    def test_blinded_pinned_model_probe_retains_matching_receipt_and_costs(self):
        model = "gpt-audio-2025-08-28"
        class PinnedSemanticTransport(SemanticTransport):
            def __call__(self, payload, key, timeout):
                result = super().__call__(payload, key, timeout)
                result["model"] = model
                return result
        transport = PinnedSemanticTransport()
        backend = self.backend(transport, model=model)
        probe_backend(self.fixtures, self.root/"pinned-probe", backend=backend)
        receipt = read_json(self.root/"pinned-probe"/"capability.json")
        self.assertEqual(receipt["status"], "PROBE_PASSED")
        self.assertEqual(receipt["returned_model"], model)
        self.assertEqual(require_capability(self.root/"pinned-probe"/"capability.json", backend), receipt)
        cost = read_json(self.root/"pinned-probe"/"cost-summary.json")
        self.assertEqual(cost["pricing_snapshot"], pricing_for_model(model))
        self.assertEqual(cost["operations"], 12)

    def test_pinned_model_does_not_accept_prefix_or_silently_switch_models(self):
        model = "gpt-audio-2025-08-28"
        for i, returned in enumerate((MODEL, model+"-changed")):
            transport = Transport(raw="0", model=returned)
            backend = self.backend(transport, model=model)
            probe_backend(self.fixtures, self.root/("wrong-model-%d" % i), backend=backend)
            receipt = read_json(self.root/("wrong-model-%d" % i)/"capability.json")
            self.assertEqual(receipt["status"], "PROBE_FAILED")
            self.assertEqual(receipt["completed_trials"], 0)
            self.assertEqual(len(transport.calls), 1)
            with self.assertRaises(HostedError):
                require_capability(self.root/("wrong-model-%d" % i)/"capability.json", backend)

    def test_mixed_model_queue_preserves_cumulative_budget_and_prior_tariffs(self):
        queue = self.root/"mixed-queue.jsonl"
        self.run_observer(queue_ledger=queue, queue_budget_usd=.02)
        model = "gpt-audio-2025-08-28"
        transport = Transport(model=model)
        backend = self.backend(transport, model=model)
        observe_request(self.request(), self.root/"pinned-budget", witness_run=self.a,
            backend=backend, capability=self.capability(backend), queue_ledger=queue, queue_budget_usd=.02)
        report = read_json(self.root/"pinned-budget"/"observation.json")
        self.assertEqual(report["execution_status"], "BUDGET_GUARD")
        self.assertEqual(transport.calls, [])
        rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["pricing_snapshot"]["model"] == MODEL for row in rows))

    def test_blinded_probe_scores_audio_and_never_forwards_adjudication(self):
        transport = SemanticTransport(); backend = self.backend(transport)
        probe_backend(self.fixtures,self.root/"probe",backend=backend)
        receipt = read_json(self.root/"probe"/"capability.json")
        self.assertEqual(receipt["status"],"PROBE_PASSED"); self.assertEqual(receipt["probe_result"]["correct"],12)
        for payload, _, _ in transport.calls:
            texts = " ".join(p["text"] for p in payload["messages"][0]["content"] if p["type"] == "text")
            self.assertNotIn("answer=",texts); self.assertNotIn("adjudication",texts); self.assertNotIn("seed",texts)
            self.assertNotIn(str(self.fixtures),texts)
        # A passing fake transport receipt cannot authorize the actual HTTP route.
        real = OpenAIAudioObserver(credential_env="AVE_TEST_OPENAI_KEY",allow_remote_media="openai")
        with self.assertRaisesRegex(HostedError,"BACKEND_NOT_PROBED"):
            require_capability(self.root/"probe"/"capability.json",real)

    def test_constant_answer_probe_fails(self):
        backend = self.backend(Transport(raw="0"))
        probe_backend(self.fixtures,self.root/"bad-probe",backend=backend)
        self.assertEqual(read_json(self.root/"bad-probe"/"capability.json")["status"],"PROBE_FAILED")

    def test_accounted_truncated_probe_answer_counts_wrong_without_retry(self):
        class OneTruncatedTransport(SemanticTransport):
            def __call__(self, payload, key, timeout):
                result = super().__call__(payload,key,timeout)
                if len(self.calls) == 1:
                    result["choices"][0]["finish_reason"] = "length"
                    result["choices"][0]["message"]["content"] = '{"analysis": ['
                return result
        transport = OneTruncatedTransport()
        probe_backend(self.fixtures,self.root/"probe",backend=self.backend(transport))
        receipt = read_json(self.root/"probe"/"capability.json")
        self.assertEqual(len(transport.calls),12)
        self.assertEqual(receipt["completed_trials"],12)
        self.assertEqual(receipt["probe_result"]["correct"],11)
        self.assertEqual(receipt["status"],"PROBE_PASSED")
        trial = read_json(self.root/"probe"/"probe-responses.json")["trials"][0]
        self.assertTrue(trial["counted_as_incorrect"])
        self.assertEqual(trial["provider_receipt"]["raw_response"],'{"analysis": [')

    def test_unaccounted_truncated_probe_answer_stops_without_retry(self):
        transport = Transport(raw='{"analysis": [',finish="length",usage=None)
        probe_backend(self.fixtures,self.root/"probe",backend=self.backend(transport))
        receipt = read_json(self.root/"probe"/"capability.json")
        self.assertEqual(len(transport.calls),1); self.assertEqual(receipt["completed_trials"],0)
        self.assertEqual(receipt["status"],"PROBE_FAILED")
        self.assertTrue(read_json(self.root/"probe"/"cost-summary.json")["billing_unresolved"])

    def test_probe_dry_run_has_no_credential_requirement_or_false_pass(self):
        backend = self.backend(); backend.allow_remote_media=None
        with patch.dict(os.environ,{"AVE_TEST_OPENAI_KEY":""}):
            manifest = probe_backend(self.fixtures,self.root/"plan",backend=backend,dry_run=True)
        self.assertEqual(manifest["operation"],"observer-probe-estimate")
        self.assertFalse((self.root/"plan"/"capability.json").exists())
        self.assertEqual(backend.transport.calls,[])

    def test_existing_output_is_refused_before_any_submission(self):
        (self.root/"existing").mkdir()
        transport = Transport()
        with self.assertRaises(AVError): self.run_observer(name="existing",transport=transport)
        self.assertEqual(transport.calls,[])

    def test_falsey_or_invalid_transport_cannot_choose_live_http(self):
        class FalseyTransport(Transport):
            def __bool__(self): return False
        transport = FalseyTransport()
        backend = OpenAIAudioObserver(credential_env="AVE_TEST_OPENAI_KEY",allow_remote_media="openai",transport=transport)
        observe_request(self.request(),self.root/"falsey",witness_run=self.a,backend=backend,capability=self.capability(backend))
        self.assertEqual(len(transport.calls),1); self.assertEqual(backend.identity["execution_mode"],"TEST_DOUBLE")
        with self.assertRaises(AVError): OpenAIAudioObserver(transport={})

    def test_backend_cannot_be_reused_concurrently_or_retain_observe_permission(self):
        backend = self.backend()
        with backend.execution_guard():
            with self.assertRaisesRegex(HostedError,"already in use"):
                observe_request(self.request(),self.root/"busy",witness_run=self.a,backend=backend,capability=self.capability(backend))
        observe_request(self.request(),self.root/"idle",witness_run=self.a,backend=backend,capability=self.capability(backend))
        self.assertIsNone(backend.session); self.assertIsNone(backend.admitted_capability)

    def test_valid_production_shaped_receipt_is_checked_without_live_calls(self):
        # HTTP is patched only inside this unit test to exercise serialized live
        # receipt fields. This fixture is not a real capability/acting benchmark.
        from avevidence.auditory_claims import validate_model_observation
        transport = Transport()
        with patch("avevidence.providers.openai_audio.http_transport",new=transport):
            backend = OpenAIAudioObserver(credential_env="AVE_TEST_OPENAI_KEY",allow_remote_media="openai")
            observe_request(self.request(),self.root/"shaped",witness_run=self.a,backend=backend,capability=self.capability(backend))
        path = self.root/"shaped"/"observation.json"
        report, covered, _ = validate_model_observation(path)
        self.assertEqual(report["observer_type"],"model_audio"); self.assertEqual(len(transport.calls),1)
        self.assertAlmostEqual(covered[0][0],.3)
        bad = copy.deepcopy(report); bad["provider_receipt"]["authorization"]["remote_media_authorized"] = False
        malformed = self.root/"unauthorized-receipt.json"; write_json(malformed,bad)
        with self.assertRaises(AVError): validate_model_observation(malformed)
        bad = copy.deepcopy(report); bad["capability_receipt"]["returned_model"] = MODEL+"-stale"
        malformed = self.root/"changed-model.json"; write_json(malformed,bad)
        with self.assertRaises(AVError): validate_model_observation(malformed)


if __name__ == "__main__":
    unittest.main()
