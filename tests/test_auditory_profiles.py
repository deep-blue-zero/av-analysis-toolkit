"""Task, context and coherent-section safeguards with synthetic audio only."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

import test_hosted_observer as _hosted
from avevidence.auditory_profiles import (TASK_PROFILES, capability_profile, profile_prompt,
    request_policy, scoped_human_review)
from avevidence.audio_witness import audio_witness
from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.observer_execution import estimate_observation, observe_request
from avevidence.observer_requests import load_request, wav_identity
from avevidence.performance_sections import performance_sections
from avevidence.providers.openai_common import HostedError, estimate_cost


class AuditoryProfiles(unittest.TestCase):
    setUpClass = classmethod(_hosted.HostedContracts.setUpClass.__func__)
    tearDownClass = classmethod(_hosted.HostedContracts.tearDownClass.__func__)
    setUp = _hosted.HostedContracts.setUp
    tearDown = _hosted.HostedContracts.tearDown
    backend = _hosted.HostedContracts.backend
    request = _hosted.HostedContracts.request
    capability = _hosted.HostedContracts.capability
    run_observer = _hosted.HostedContracts.run_observer

    def long_witness(self, seconds=40):
        source = self.root/"synthetic-long.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
            stream.writeframes(b"\0\0"*8000*seconds)
        witness = self.root/"section-witness"
        audio_witness(source, witness, start=0, end=seconds)
        return source, witness

    def section_request(self, source, **changes):
        return self.request(source_sha256=sha256(source), task_profile="PERFORMANCE_MUSIC",
            audio_mode="coherent_section", allow_long_section=True, section_budget_usd=.20, **changes)

    def test_legacy_default_profile_preserves_neutral_prompt_and_bounds(self):
        from avevidence.auditory_observer import NEUTRAL_PROMPT
        self.assertEqual(profile_prompt(), NEUTRAL_PROMPT)
        self.assertEqual(request_policy()["max_clip_seconds"], 30)
        _, clips, _, _ = load_request(self.request(), self.a)
        self.assertEqual(len(clips), 1)

    def test_six_profiles_have_distinct_questions_and_unqualified_capabilities(self):
        report = capability_profile(self.backend().identity)
        self.assertEqual(len(TASK_PROFILES), 6)
        self.assertEqual(len({profile_prompt(p) for p in TASK_PROFILES}), 6)
        self.assertTrue(all(v == {"status":"UNQUALIFIED", "test_status":"NOT_TESTED"} for v in report["tasks"].values()))
        self.assertFalse(report["authorizes_hosted_submission"])

    def test_unknown_task_or_section_on_speech_is_refused(self):
        for changes in ({"task_profile":"AUTO"}, {"task_profile":"SPEECH_PERFORMANCE", "audio_mode":"coherent_section", "allow_long_section":True, "section_budget_usd":.2}):
            with self.assertRaises(AVError):
                load_request(self.request(**changes), self.a)

    def test_nonverbal_and_soundscape_observations_have_independent_profiles(self):
        _, record, _ = self.run_observer(request=self.request(task_profile="SOUNDSCAPE"))
        self.assertEqual(record["task_profile"], "SOUNDSCAPE")
        self.assertIn("Obscured speech need not prevent", record["prompt"])
        self.assertEqual(record["evidence_class"], "AO_STAGE1")
        self.assertEqual(record["task_capability_status"], "UNQUALIFIED")

    def test_av_sync_prompt_does_not_claim_audio_establishes_visual_events(self):
        self.assertIn("Audio alone does not establish", profile_prompt("AV_SYNC"))

    def test_stage1_rejects_every_new_context_field(self):
        for key in ("canonical_text", "speaker_mapping", "scene_context", "stage1_discrepancies", "reinspection_question", "context_evidence_ids"):
            with self.subTest(key=key), self.assertRaises(AVError):
                load_request(self.request(**{key:"context"}), self.a)

    def test_stage2_retains_canonical_discrepancy_context_and_stage1_bytes(self):
        first_request = self.request(task_profile="SPEECH_PERFORMANCE")
        self.run_observer(request=first_request, name="stage1")
        first_path = self.root/"stage1"/"observation.json"
        before = first_path.read_bytes()
        second = self.request(stage=2, stage1_run="stage1", task_profile="SPEECH_PERFORMANCE",
            canonical_text="ノレア", speaker_mapping={"A":"source-established speaker"},
            scene_context="Bounded local source context", stage1_discrepancies=["Earlier lexical guess differs"],
            reinspection_question="Describe delivery independently of the lexical correction", context_evidence_ids=["TXT1"])
        _, record, backend = self.run_observer(request=second, name="stage2")
        self.assertEqual(first_path.read_bytes(), before)
        self.assertEqual(record["evidence_class"], "AO_STAGE2")
        self.assertEqual(record["stage1_parent"]["observation_sha256"], sha256(first_path))
        self.assertEqual(record["supplied_context"]["canonical_text"], "ノレア")
        self.assertIn("ノレア", backend.transport.calls[0][0]["messages"][0]["content"][0]["text"])

    def test_stage2_cannot_change_profile_or_audio(self):
        self.run_observer(request=self.request(task_profile="SPEECH_PERFORMANCE"), name="stage1")
        with self.assertRaises(AVError):
            self.run_observer(request=self.request(stage=2, stage1_run="stage1", task_profile="SOUNDSCAPE"), name="stage2")

    def test_identical_audio_cannot_move_stage2_to_another_source_interval(self):
        source, _ = self.long_witness(3)
        first, second = self.root/"silence-A", self.root/"silence-B"
        audio_witness(source, first, start=.2, end=.8)
        audio_witness(source, second, start=1.2, end=1.8)
        self.assertEqual(sha256(first/"audio.wav"), sha256(second/"audio.wav"))
        backend = self.backend()
        observe_request(self.request(source_sha256=sha256(source), task_profile="SOUNDSCAPE"),
            self.root/"stage1", witness_run=first, backend=backend, capability=self.capability(backend))
        request = self.request(source_sha256=sha256(source), stage=2, stage1_run="stage1", task_profile="SOUNDSCAPE")
        backend = self.backend()
        with self.assertRaisesRegex(AVError, "differ from the stage 1 baseline"):
            observe_request(request, self.root/"rebound", witness_run=second, backend=backend, capability=self.capability(backend))
        self.assertEqual(backend.transport.calls, [])

    def test_invalid_stage1_is_preserved_and_does_not_admit_stage2(self):
        self.run_observer(transport=_hosted.Transport(raw="malformed"), name="bad")
        before = (self.root/"bad"/"observation.json").read_bytes()
        with self.assertRaises(AVError):
            self.run_observer(request=self.request(stage=2, stage1_run="bad"), name="second")
        self.assertEqual((self.root/"bad"/"observation.json").read_bytes(), before)

    def test_coherent_section_requires_explicit_opt_in_and_budget(self):
        for missing in ("allow_long_section", "section_budget_usd"):
            value = {"task_profile":"PERFORMANCE_MUSIC", "audio_mode":"coherent_section", "allow_long_section":True, "section_budget_usd":.2}
            del value[missing]
            with self.assertRaises(AVError):
                request_policy(value)

    def test_long_witness_is_refused_by_legacy_policy_and_accepted_explicitly(self):
        source, witness = self.long_witness()
        with self.assertRaises(AVError):
            load_request(self.request(source_sha256=sha256(source)), witness)
        _, clips, _, _ = load_request(self.section_request(source), witness)
        self.assertEqual(clips[0]["duration_seconds"], 40)
        self.assertEqual(wav_identity(witness/"audio.wav")["sample_frames"], 320000)

    def test_section_comparison_is_never_silently_concatenated(self):
        with self.assertRaises(AVError):
            load_request(self.request(comparison=True, task_profile="PERFORMANCE_MUSIC", audio_mode="coherent_section", allow_long_section=True, section_budget_usd=.2))

    def test_section_estimate_is_offline_and_does_not_relax_default_estimator(self):
        source, witness = self.long_witness()
        backend = self.backend()
        result = estimate_observation(self.section_request(source), witness_run=witness, backend=backend)
        self.assertEqual(result["estimate"]["audio_seconds"], 40)
        self.assertEqual(result["estimate"]["audio_policy"]["max_audio_bytes"], 128*1024*1024)
        self.assertEqual(backend.transport.calls, [])
        with self.assertRaises(AVError):
            estimate_cost([40], "question")

    def test_single_embedded_witness_allows_portable_offline_section_preparation(self):
        source, witness = self.long_witness()
        request = self.section_request(source, witness_run=witness.name)
        _, clips, _, _ = load_request(request)
        self.assertEqual(clips[0]["witness_run"], str(witness.resolve()))
        with self.assertRaises(AVError):
            load_request(request, witness)

    def test_malformed_stage2_context_is_refused_before_submission(self):
        for changes in ({"canonical_text": ["words"]}, {"speaker_mapping": ["speaker"]}, {"context_evidence_ids": "TXT1"}, {"stage1_discrepancies": [False]}, {"context_evidence_ids":["TXT1","TXT1"]}):
            with self.subTest(changes=changes), self.assertRaises(AVError):
                load_request(self.request(stage=2, **changes), self.a)

    def test_section_ceiling_refuses_spend_without_raising_operator_budget(self):
        source, witness = self.long_witness()
        backend = self.backend()
        request = self.section_request(source)
        value = read_json(request); value["section_budget_usd"] = .001
        request = self.root/"narrow-section-request.json"; write_json(request, value)
        manifest = observe_request(request, self.root/"section-result", witness_run=witness, backend=backend,
            capability=self.capability(backend), request_budget_usd=.20)
        self.assertEqual(manifest["metadata"]["execution_status"], "BUDGET_GUARD")
        self.assertEqual(backend.transport.calls, [])

    def test_explicit_section_never_raises_run_request_or_queue_budgets(self):
        source, witness = self.long_witness()
        for option in ("request_budget_usd", "budget_usd", "queue_budget_usd"):
            with self.subTest(option=option):
                backend = self.backend()
                manifest = observe_request(self.section_request(source), self.root/option,
                    witness_run=witness, backend=backend, capability=self.capability(backend), **{option:.001})
                self.assertEqual(manifest["metadata"]["execution_status"], "BUDGET_GUARD")
                self.assertEqual(backend.transport.calls, [])

    def test_section_model_call_is_synthetic_attributed_and_accounted(self):
        source, witness = self.long_witness()
        backend = self.backend()
        manifest = observe_request(self.section_request(source), self.root/"section", witness_run=witness,
            backend=backend, capability=self.capability(backend), request_budget_usd=.10)
        record = read_json(self.root/"section"/"observation.json")
        self.assertEqual(manifest["metadata"]["execution_status"], "OBSERVATION_VALID")
        self.assertEqual(record["audio_policy"]["max_clip_seconds"], 120)
        self.assertEqual(record["observer_type"], "mock")
        self.assertEqual(record["task_capability_status"], "UNQUALIFIED")
        self.assertEqual(record["provider_receipt"]["usage"]["request_limit_usd"], .10)

    def test_section_duration_and_byte_limits_fail_before_submission(self):
        value = {"task_profile":"PERFORMANCE_MUSIC", "audio_mode":"coherent_section", "allow_long_section":True, "section_budget_usd":.2}
        with self.assertRaises(AVError):
            estimate_cost([120.1], "question", request=value)
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_size = 128*1024*1024+1
            with self.assertRaises(AVError):
                wav_identity(self.a/"audio.wav", max_bytes=128*1024*1024)

    def test_failed_probe_history_remains_failed_and_human_review_is_not_global(self):
        failed = {"status":"PROBE_FAILED", "correct":8, "trials":24}
        reviewed = {"schema":"ave.scoped-human-auditory-review.v1", "global_backend_qualification":False, "judgment":"broadly_accurate"}
        frozen = copy.deepcopy(failed)
        report = capability_profile(self.backend().identity, historical_probes=[failed], scoped_reviews=[reviewed])
        self.assertEqual(failed, frozen)
        self.assertEqual(report["historical_probes"][0]["status"], "PROBE_FAILED")
        self.assertTrue(all(v["status"] == "UNQUALIFIED" for v in report["tasks"].values()))

    def test_self_declared_task_capabilities_cannot_admit_a_provider(self):
        from avevidence.observer_execution import require_capability
        backend = self.backend()
        value = capability_profile(backend.identity, historical_probes=[{"status":"PROBE_PASSED"}])
        value["tasks"]["PERFORMANCE_MUSIC"]["status"] = "VALIDATED"
        path = self.root/"self-declared-capability.json"; write_json(path, value)
        with self.assertRaises(AVError):
            require_capability(path, backend)
        self.assertEqual(backend.transport.calls, [])

    def test_malformed_capability_history_is_rejected_without_type_errors(self):
        for value in ([{"status":[]}], {"status":"PROBE_FAILED"}):
            with self.assertRaises(AVError):
                capability_profile(self.backend().identity, historical_probes=value)

    def review_config(self, record, **changes):
        value = {"reviewer_type":"owner", "reviewer":"Synthetic reviewer", "scope":"Only this supplied clip's delivery", "judgment":"broadly_accurate", "exceptions":[], "date":"2026-10-06",
            "inspected_intervals":[{"source_sha256":record["source_sha256"], "clock":"original_pts_minus_source_origin", "stream_index":record["stream_index"], "intervals_seconds":[record["source_interval_seconds"]]}]}
        value.update(changes); path=self.root/"human-input.json"; write_json(path,value); return path

    def test_scoped_human_review_binds_hash_and_leaves_model_response_unchanged(self):
        _, record, _ = self.run_observer()
        path = self.root/"result"/"observation.json"; before=path.read_bytes()
        scoped_human_review(self.root/"result", self.review_config(record), self.root/"human-review")
        review = read_json(self.root/"human-review"/"human-review.json")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(review["observation_sha256"], sha256(path))
        self.assertFalse(review["global_backend_qualification"])

    def test_scoped_human_review_refuses_expanded_interval(self):
        _, record, _ = self.run_observer()
        config = self.review_config(record); value=read_json(config)
        value["inspected_intervals"][0]["intervals_seconds"]=[[0,2]]
        config=self.root/"expanded-review.json"; write_json(config,value)
        with self.assertRaises(AVError):
            scoped_human_review(self.root/"result", config, self.root/"bad-review")

    def test_performance_sections_retain_one_source_and_lyric_boundary_authority(self):
        lyric=self.root/"lyric.txt"; lyric.write_text("synthetic lyric", encoding="utf-8")
        config=self.root/"performance.json"
        write_json(config,{"schema":"ave.performance-sections.input.v1", "source_path":str(self.source), "source_sha256":sha256(self.source), "audio_stream":0, "performance_interval_seconds":[0,2],
            "sections":[{"id":"verse", "label":"Formal opening", "interval_seconds":[0,1], "boundary_origin":"estimated_boundary", "question":"Describe section buildup", "lyric_ids":["L1"]},
                        {"id":"refrain", "label":"Formal continuation", "interval_seconds":[1,2], "boundary_origin":"musical_review_declared", "question":"Compare ensemble density"}],
            "lyrics":[{"id":"L1", "artifact":{"path":str(lyric), "sha256":sha256(lyric)}, "interval_seconds":[.2,.8], "boundary_origin":"subtitle_boundary"}]})
        performance_sections(config,self.root/"performance")
        report=read_json(self.root/"performance"/"performance-sections.json")
        self.assertEqual(len(report["sections"]),2)
        self.assertEqual(report["source"]["sha256"],sha256(self.source))
        self.assertEqual(report["evidence_references"][0]["boundary_origin"],"subtitle_boundary")
        self.assertEqual(report["paid_calls"],0)
        self.assertFalse(report["source_media_copied"])

    def test_formal_sections_refuse_outside_source_and_absent_lyric(self):
        config=self.root/"performance.json"
        value={"schema":"ave.performance-sections.input.v1", "source_path":str(self.source), "source_sha256":sha256(self.source), "audio_stream":0, "performance_interval_seconds":[0,2],
            "sections":[{"id":"verse", "label":"Opening", "interval_seconds":[0,3], "boundary_origin":"estimated_boundary", "question":"Describe sound"}]}
        write_json(config,value)
        with self.assertRaises(AVError):performance_sections(config,self.root/"bad")
        value["sections"][0]["interval_seconds"]=[0,2];value["sections"][0]["lyric_ids"]=["absent"]
        config=self.root/"absent-lyric.json";write_json(config,value)
        with self.assertRaises(AVError):performance_sections(config,self.root/"bad")
