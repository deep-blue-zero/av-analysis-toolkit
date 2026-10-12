"""Synthetic claim-delta and deep-read routing; no paid or real-media judgments."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.auditory_claims import claim_delta, validate_model_observation
from avevidence.auditory_observer import observe_clip
from avevidence.common import AVError, finish_run, read_json, sha256, write_json
from avevidence.deep_read import deep_read
from avevidence.evidence_claims import validate_claims
from avevidence.observer_execution import observe_request
from avevidence.inventory import verify_run
import test_hosted_observer as hosted_fixtures
from test_hosted_observer import KEY, Transport, observation


class LocalProtocolFixture:
    """Scripted unit-test fixture for the pre-existing local backend protocol."""
    identity = {"type":"model_audio","route_type":"local","model_revision":"synthetic-unit-fixture","adapter_revision":"1"}
    def observe(self, request, clip, optional_text=None):
        if "clips" not in clip:
            return json.dumps(observation())
        return json.dumps({"clips":[{"label":c["label"],"observation":observation()} for c in clip["clips"]],
                          "comparison":["A synthetic comparison."], "alternatives":[],"interference":[],"abstentions":[],"confidence":"low"})


class ClaimAndDeepRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Shared synthetic audio only; using the fixture class does not run its tests.
        hosted_fixtures.HostedContracts.setUpClass()
        fixtures = hosted_fixtures.HostedContracts
        cls.source, cls.a, cls.b = fixtures.source, fixtures.a, fixtures.b
        cls.fixtures = fixtures.fixtures

    @classmethod
    def tearDownClass(cls):
        hosted_fixtures.HostedContracts.tearDownClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ,{"AVE_TEST_OPENAI_KEY":KEY}); self.env.start()
        self.hash = sha256(self.source)
        self.loc = {"source_sha256":self.hash,"clock":"original_pts_minus_source_origin","stream_index":0,"intervals_seconds":[[.2,.8],[1.2,1.8]]}
        self.claim = {"id":"c1","statement":"The audible delivery differs in these intervals.","kind":"delivery",
            "required_modalities":["audio"],"locators":[self.loc],"evidence_ids":[],"counterevidence_ids":[],
            "inference":None,"interpretation":None,"alternatives":["The difference may be interference."],"confidence":"unknown",
            "question":"PRIVATE narrative character hypothesis, not to be sent in the neutral pass."}
        self.data = {"schema":"ave.claims.v1","claims":[self.claim],"evidence":[]}
        self.config = self.root/"claims.json"; write_json(self.config,self.data)
        self.request = self.root/"request.json"
        write_json(self.request,{"request_id":"r1","question":"Describe audible articulation and pace.","stage":1,
            "clips":[{"label":"A","witness_run":str(self.a)},{"label":"B","witness_run":str(self.b)}]})
        self.local = LocalProtocolFixture()
        self.capability = self.root/"local-capability.json"
        write_json(self.capability,{"interface":"synthetic-test","model_revision":self.local.identity["model_revision"],"adapter_revision":"1",
            "backend_identity":self.local.identity,"status":"PROBE_PASSED","semantic_probe_passed":True,"fixture_sha256":sha256(self.request)})

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def local_observation(self, name="observed"):
        """Manufacture retained v1 receipts to exercise the historical reader.

        This is no longer a fresh, probe-only routine collection. New execution
        needs a task qualification; manufactured records validate compatibility
        and must never be cited as an actual backend/perception benchmark.
        """
        from avevidence.observer_requests import load_request, public_clip, source_observations
        path = self.root/name
        path.mkdir()
        request, clips, _, _ = load_request(self.request)
        raw = self.local.observe(request, {"clips": clips})
        parsed = json.loads(raw)
        common = {"observer_type": "model_audio", "backend_identity": self.local.identity,
                  "validation_status": "VALID", "capability_status": "PROBE_PASSED", "raw_response": raw,
                  "task_profile": "SPEECH_PERFORMANCE", "stage": 1}
        parent = {**common, "schema": "ave.auditory-comparison.v1", "clips": [public_clip(c) for c in clips], "parsed": parsed}
        write_json(path/"observation.json", parent)
        names = []
        for clip, row in zip(clips, parsed["clips"]):
            atom = {**common, "schema": "ave.auditory-observation.v1", "parsed": row["observation"],
                    **{k: clip[k] for k in ("source_sha256", "stream_index", "source_interval_seconds", "clip_sha256", "duration_seconds")},
                    "clip_to_source_mapping": clip["review_mapping"],
                    "source_observations": source_observations(row["observation"], clip),
                    "comparison_projection": {"label": clip["label"], "parent_file": "observation.json", "parent_sha256": sha256(path/"observation.json")}}
            filename = "clip-"+clip["label"]+"-observation.json"
            write_json(path/filename, atom); names.append(filename)
        finish_run(path, "observer-observe", [], metadata={"atomic_observation_files": names,
                    "fixture_scope": "Manufactured historical reader records, no actual observer qualification"})
        return path

    def test_new_local_execution_cannot_inherit_legacy_probe_qualification(self):
        with self.assertRaisesRegex(AVError, "TASK_NOT_QUALIFIED"):
            observe_request(self.request, self.root/"new-local", backend=self.local, capability=self.capability)

    def decisions(self, disposition, status=None):
        decision = {"claim_id":"c1","disposition":disposition,"reviewer":"Synthetic coordinator fixture",
            "reason":"Explicit unit-test disposition of a synthetic witness.", "deterministic_agreement":"NOT_REVIEWED",
            "analytical_consequence":"Synthetic test only; no claim about a real performance."}
        if status:
            decision["adjudication"] = {"status":status,"reviewer":"Synthetic coordinator fixture","reason":"Explicit synthetic adjudication."}
        path = self.root/"decisions.json"; write_json(path,{"decisions":[decision]}); return path

    def test_new_witness_is_attached_without_automatic_verdict_promotion(self):
        observed = self.local_observation()
        old = sha256(self.config)
        claim_delta(self.config,[observed],self.root/"delta")
        delta = read_json(self.root/"delta"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(delta["before_status"],"OPEN"); self.assertEqual(delta["after_status"],"OPEN")
        self.assertEqual(delta["disposition"],"AWAITING_ADJUDICATION")
        self.assertEqual(len(delta["new_auditory_evidence_ids"]),2)
        updated = read_json(self.root/"delta"/"claims-with-auditory-witnesses.json")
        self.assertEqual(updated["claims"][0]["evidence_ids"],[])
        self.assertEqual(sha256(self.config),old)
        self.assertEqual((self.root/"delta"/"before-claims.json").read_bytes(),self.config.read_bytes())
        verify_run(self.root/"delta")

    def test_support_requires_explicit_adjudication_to_become_supported(self):
        observed = self.local_observation()
        claim_delta(self.config,[observed],self.root/"support-open",decisions=self.decisions("SUPPORT"))
        row = read_json(self.root/"support-open"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(row["after_status"],"OPEN")
        # A second explicit decision is a separate input, never a retry of the model.
        (self.root/"decisions.json").unlink()
        claim_delta(self.config,[observed],self.root/"support-accepted",decisions=self.decisions("SUPPORT","SUPPORTED"))
        row = read_json(self.root/"support-accepted"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(row["after_status"],"SUPPORTED")

    def test_contradiction_goes_to_counterevidence_and_requires_its_own_verdict(self):
        observed = self.local_observation()
        claim_delta(self.config,[observed],self.root/"counter",decisions=self.decisions("CONTRADICT","CONTRADICTED"))
        updated = read_json(self.root/"counter"/"claims-with-auditory-witnesses.json")
        self.assertEqual(len(updated["claims"][0]["counterevidence_ids"]),2)
        self.assertEqual(updated["claims"][0]["evidence_ids"],[])
        self.assertEqual(read_json(self.root/"counter"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]["after_status"],"CONTRADICTED")

    def test_missing_motion_cannot_be_closed_by_auditory_evidence(self):
        self.data["claims"][0]["required_modalities"] = ["audio","motion"]
        self.config.write_text(json.dumps(self.data),encoding="utf-8")
        with self.assertRaisesRegex(AVError,"SUPPORTED claim lacks"):
            claim_delta(self.config,[self.local_observation()],self.root/"bad-promotion",decisions=self.decisions("SUPPORT","SUPPORTED"))

    def test_constraint_and_failure_to_discriminate_preserve_open_debt(self):
        observed = self.local_observation()
        for i, disposition in enumerate(("CONSTRAIN","FAIL_TO_DISCRIMINATE")):
            if (self.root/"decisions.json").exists(): (self.root/"decisions.json").unlink()
            claim_delta(self.config,[observed],self.root/("case-%d" % i),decisions=self.decisions(disposition))
            row = read_json(self.root/("case-%d" % i)/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
            self.assertEqual(row["after_status"],"OPEN"); self.assertEqual(row["disposition"],disposition)

    def test_comparison_projection_cannot_change_its_description_or_clock(self):
        observed = self.local_observation()
        original = observed/"clip-A-observation.json"
        obs, covered, _ = validate_model_observation(original)
        self.assertEqual(len(covered),1)
        self.assertAlmostEqual(covered[0][0],.3); self.assertAlmostEqual(covered[0][1],.6)
        obs["parsed"]["observations"][0]["description"] = "Invented replacement"
        path = observed/"tampered.json"; write_json(path,obs)
        with self.assertRaises(AVError): validate_model_observation(path)

    def test_evidence_cannot_expand_a_short_observation_to_the_whole_clip(self):
        observed = self.local_observation()
        path = observed/"clip-A-observation.json"
        evidence = {"id":"ao1","layer":"auditory_observation","modality":"audio","observer_type":"model_audio",
            "locator":dict(self.loc,intervals_seconds=[[.2,.8]]),"statement":"Overextended test description", "quality_flags":[],
            "reviewer":"Synthetic fixture","inspection_status":"REVIEW_DECLARED","artifact":{"path":str(path),"sha256":sha256(path)}}
        config = copy.deepcopy(self.data); config["evidence"]=[evidence]; config["claims"][0]["evidence_ids"]=["ao1"]
        with self.assertRaisesRegex(AVError,"actual timed observations"): validate_claims(config,self.root)

    def test_prior_reading_freeze_is_byte_exact_and_never_forwarded(self):
        reading = self.root/"prior.md"; reading.write_bytes(b"\xef\xbb\xbfPrior interpretation\r\n")
        claim_delta(self.config,[self.local_observation()],self.root/"frozen",prior_readings=[reading])
        self.assertEqual((self.root/"frozen"/"prior-reading-00.md").read_bytes(),reading.read_bytes())
        self.assertEqual(read_json(self.root/"frozen"/"AUDITORY_CLAIM_DELTA.json")["frozen_prior_readings"][0]["sha256"],sha256(reading))

    def test_mock_receipts_produce_diagnostic_delta_without_audio_evidence(self):
        single = self.root/"single.json"; write_json(single,{"request_id":"r2","question":"Describe audible sound.","source_sha256":self.hash})
        observe_clip(self.a,single,self.root/"mock")
        claim_delta(self.config,[self.root/"mock"],self.root/"mock-delta")
        row = read_json(self.root/"mock-delta"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(row["disposition"],"FAIL_TO_DISCRIMINATE"); self.assertEqual(row["new_auditory_evidence_ids"],[])
        self.assertEqual(row["after_status"],"OPEN"); self.assertTrue(row["unadmitted_results"])
        with self.assertRaisesRegex(AVError,"affirmative admitted"):
            claim_delta(self.config,[self.root/"mock"],self.root/"mock-support",decisions=self.decisions("SUPPORT","SUPPORTED"))

    def test_deep_read_hosted_preparation_is_offline_and_context_minimized(self):
        output = self.root/"prepared"
        with patch.dict(os.environ,{"OPENAI_API_KEY":""}):
            deep_read(self.source,output,claims=self.config,auditory_observer="openai-audio",prepare_observer_witnesses=True)
        plan = read_json(output/"hosted-auditory-plan.json")
        self.assertEqual(plan["status"],"HOSTED_PREPARED_NOT_SUBMITTED")
        self.assertFalse(plan["execution_authorized"])
        request = read_json(output/plan["requests"][0]["request_file"])
        self.assertNotIn("PRIVATE",request["question"])
        self.assertEqual(request["stage"],1)
        self.assertEqual(len(request["clips"]),2)
        routes = [r["route"] for r in read_json(output/"claims"/"claim-audit.json")["requests"]]
        self.assertEqual(routes,["HOSTED_PROBE_REQUIRED"])
        self.assertFalse((output/"hosted-observations").exists())
        verify_run(output)

    def test_broad_debt_intervals_require_refinement_and_are_never_sent(self):
        self.data["claims"][0]["locators"][0]["intervals_seconds"]=[[0,1000]]
        self.config.write_text(json.dumps(self.data),encoding="utf-8")
        deep_read(self.source,self.root/"broad",claims=self.config,auditory_observer="openai-audio",prepare_observer_witnesses=True)
        plan = read_json(self.root/"broad"/"hosted-auditory-plan.json")
        self.assertEqual(plan["requests"][0]["status"],"REFINEMENT_REQUIRED")
        self.assertFalse((self.root/"broad"/"observer-requests").exists())

    def test_hosted_execution_requires_all_explicit_gates(self):
        with self.assertRaisesRegex(AVError,"HOSTED_MEDIA_NOT_AUTHORIZED"):
            deep_read(self.source,self.root/"no-auth",claims=self.config,auditory_observer="openai-audio",execute_observer=True)
        with self.assertRaises(AVError):
            deep_read(self.source,self.root/"human-instead",claims=self.config,auditory_observer="human",execute_observer=True)
        with patch.dict(os.environ,{"OPENAI_API_KEY":KEY}):
            with self.assertRaisesRegex(AVError,"BACKEND_NOT_PROBED"):
                deep_read(self.source,self.root/"no-probe",claims=self.config,auditory_observer="openai-audio",
                    execute_observer=True,allow_remote_media="openai")

    def test_original_positional_mock_cli_and_named_comparison_cli_work(self):
        from avevidence.cli import main
        single = self.root/"single.json"; write_json(single,{"request_id":"r2","question":"Describe audible sound.","source_sha256":self.hash})
        with patch("sys.stdout",new=io.StringIO()):
            status = main(["observer","observe",str(self.a),str(single),str(self.root/"legacy-cli")])
            named = main(["observer","observe","--request",str(self.request),"--output",str(self.root/"named-cli")])
        self.assertEqual(status,0); self.assertEqual(named,0)
        self.assertEqual(read_json(self.root/"legacy-cli"/"observation.json")["observer_type"],"mock")

    def test_explicit_deep_read_execution_uses_one_queue_and_preserves_verdicts(self):
        from avevidence.providers.openai_audio import OpenAIAudioObserver
        backend = OpenAIAudioObserver(credential_env="AVE_TEST_OPENAI_KEY",allow_remote_media="openai",transport=Transport())
        cap = hosted_fixtures.HostedContracts.capability(self,backend)
        deep_read(self.source,self.root/"executed",claims=self.config,auditory_observer="openai-audio",execute_observer=True,
            observer_receipt=cap,observer_backend=backend,allow_remote_media="openai",credential_env="AVE_TEST_OPENAI_KEY")
        self.assertEqual(len(backend.transport.calls),1)
        self.assertTrue((self.root/"executed"/"observer-queue-usage.jsonl").is_file())
        delta = read_json(self.root/"executed"/"auditory-claim-delta"/"AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(delta["after_status"],"OPEN")
        # This was an injected transport, so its result is diagnostic, not AO.
        self.assertEqual(delta["new_auditory_evidence_ids"],[])
        verify_run(self.root/"executed")

    def test_delta_auditory_receipts_survive_moving_the_output_folder(self):
        import shutil
        claim_delta(self.config,[self.local_observation()],self.root/"delta")
        moved = self.root/"moved-delta"
        shutil.copytree(self.root/"delta",moved)
        updated = read_json(moved/"claims-with-auditory-witnesses.json")
        _, evidence, _ = validate_claims(updated,moved)
        self.assertTrue(all("UNVALIDATED_AUDITORY_ROUTE" not in e["quality_flags"] for e in evidence.values()))
        self.assertTrue(all(not Path(e["artifact"]["path"]).is_absolute() for e in evidence.values()))


if __name__ == "__main__": unittest.main()
