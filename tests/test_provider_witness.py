"""Generated audio only: conversion, provenance, and authorized collection."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.audio_witness import audio_witness, provider_witness
from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.inventory import verify_run
from avevidence.observer_execution import observe_request
from avevidence.observer_requests import load_witness, wav_identity
from avevidence.providers.openai_audio import OpenAIAudioObserver
from avevidence.providers.openai_common import HostedError
from test_hosted_observer import KEY, Transport


class ProviderWitness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ave-provider-witness-")
        cls.root = Path(cls.temp.name).resolve()
        cls.source = cls.root/"generated.wav"
        with wave.open(str(cls.source), "wb") as stream:
            stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(8000)
            stream.writeframes(b"\x00\x10\x00\x08"*8000*2)
        cls.native = cls.root/"native"
        audio_witness(cls.source, cls.native, start=.25, end=1.25)
        cls.provider = cls.root/"provider"
        provider_witness(cls.native, cls.provider)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def request(self, **changes):
        value = {"request_id": "generated-collection", "question": "Describe audible changes in the clip.",
                 "source_sha256": sha256(self.source), "task_profile": "SOUNDSCAPE", **changes}
        path = self.root/(self.id().split('.')[-1]+"-stage"+str(value.get("stage", 1))+"-request.json")
        write_json(path, value)
        return path

    def backend(self, **options):
        transport = Transport()
        return OpenAIAudioObserver(transport=transport, credential_env="AVE_TEST_KEY", allow_remote_media="openai", **options), transport

    def test_pcm16_source_clock_and_frames_preserved(self):
        clip = load_witness(self.provider)
        self.assertEqual(clip["witness_kind"], "PROVIDER_SUBMISSION_WITNESS")
        self.assertEqual(clip["wav_format"]["bits_per_sample"], 16)
        self.assertEqual(clip["wav_format"]["sample_frames"], 8000)
        self.assertEqual(clip["wav_format"]["channels"], 2)
        self.assertEqual(clip["actual_source_interval_seconds"], [.25, 1.25])
        self.assertEqual(clip["duration_seconds"], 1.)

    def test_forensic_original_immutable_and_conversion_hashes(self):
        before = sha256(self.native/"audio.wav")
        receipt = read_json(self.provider/"transformation.json")
        self.assertEqual(receipt["input_audio_sha256"], before)
        self.assertEqual(receipt["output_audio_sha256"], sha256(self.provider/"audio.wav"))
        self.assertNotEqual(receipt["input_audio_sha256"], receipt["output_audio_sha256"])
        self.assertFalse(receipt["byte_identity_claimed"])
        self.assertEqual(receipt["decoded_sample_verification"], "PASS")
        self.assertLessEqual(receipt["max_sample_error"], 1/32768)
        verify_run(self.native); verify_run(self.provider)

    def test_refuses_requantization(self):
        with self.assertRaises(AVError):
            provider_witness(self.provider, self.root/"requantized")

    def test_duration_and_size_ceiling(self):
        for name, opts in (("short", {"max_duration_seconds": .5}), ("small", {"max_bytes": 100})):
            with self.assertRaises(AVError):
                provider_witness(self.native, self.root/name, **opts)

    def test_malformed_format_refused(self):
        path = self.root/"malformed.wav"
        path.write_bytes(b"not-a-wave"*10)
        with self.assertRaises(AVError):
            wav_identity(path)

    def test_experimental_collects_without_semantic_probe(self):
        backend, transport = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            observe_request(self.request(), self.root/"experimental", witness_run=self.provider,
                backend=backend, observation_lane="EXPERIMENTAL", request_budget_usd=.05, budget_usd=.05)
        result = read_json(self.root/"experimental"/"observation.json")
        self.assertEqual(result["validation_status"], "VALID")
        self.assertEqual(result["task_capability_status"], "UNQUALIFIED")
        self.assertEqual(result["admission_dimensions"]["transport"], "ACCEPTED")
        self.assertTrue(result["admission_dimensions"]["test_double"])
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("base64", json.dumps(result))

    def test_default_live_lane_requires_task_qualification(self):
        backend, transport = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}), self.assertRaisesRegex(HostedError, "TASK_NOT_QUALIFIED"):
            observe_request(self.request(), self.root/"unqualified", witness_run=self.provider,
                backend=backend, observation_lane="QUALIFIED")
        self.assertFalse(transport.calls)

    def test_experimental_upload_authorization_required(self):
        backend, transport = self.backend()
        backend.allow_remote_media = None
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}), self.assertRaisesRegex(HostedError, "HOSTED_MEDIA_NOT_AUTHORIZED"):
            observe_request(self.request(), self.root/"unauthorized", witness_run=self.provider,
                backend=backend, observation_lane="EXPERIMENTAL")
        self.assertFalse(transport.calls)

    def test_experimental_budget_guard_cannot_be_bypassed(self):
        backend, transport = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            observe_request(self.request(), self.root/"budget", witness_run=self.provider,
                backend=backend, observation_lane="EXPERIMENTAL", request_budget_usd=0)
        result = read_json(self.root/"budget"/"observation.json")
        self.assertEqual(result["execution_status"], "BUDGET_GUARD")
        self.assertFalse(transport.calls)

    def test_experimental_refuses_forensic_float_submission(self):
        backend, transport = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}), self.assertRaisesRegex(AVError, "PCM16"):
            observe_request(self.request(), self.root/"float", witness_run=self.native,
                backend=backend, observation_lane="EXPERIMENTAL")
        self.assertFalse(transport.calls)

    def test_contextual_run_retains_stage1_raw_and_source_clock(self):
        backend, _ = self.backend()
        first = self.root/"stage1-collection"
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            observe_request(self.request(), first, witness_run=self.provider, backend=backend, observation_lane="EXPERIMENTAL")
        original = (first/"observation.json").read_bytes()
        request = self.request(stage=2, stage1_run=str(first), canonical_text="An independently supplied source line.",
                               stage1_discrepancies=["Review the first quoted word."], context_evidence_ids=["txt"])
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            observe_request(request, self.root/"stage2-collection", witness_run=self.provider,
                backend=backend, observation_lane="EXPERIMENTAL")
        second = read_json(self.root/"stage2-collection"/"observation.json")
        self.assertEqual(original, (first/"observation.json").read_bytes())
        self.assertEqual(second["stage1_parent"]["observation_sha256"], sha256(first/"observation.json"))
        self.assertEqual(second["source_interval_seconds"], [.25, 1.25])

    def test_stage1_expected_emotion_is_rejected(self):
        backend, transport = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}), self.assertRaisesRegex(AVError, "Stage 1"):
            observe_request(self.request(expected_emotion="acceptance"), self.root/"leading", witness_run=self.provider,
                backend=backend, observation_lane="EXPERIMENTAL")
        self.assertFalse(transport.calls)

    def test_provider_and_admission_extensions_match_schemas(self):
        import jsonschema
        schemas = Path(__file__).resolve().parents[1]/"schemas"
        jsonschema.validate(read_json(self.provider/"transformation.json"),
                            read_json(schemas/"provider-audio-transformation.schema.json"))
        backend, _ = self.backend()
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            observe_request(self.request(), self.root/"schema-collection", witness_run=self.provider,
                            backend=backend, observation_lane="EXPERIMENTAL")
        obs = read_json(self.root/"schema-collection"/"observation.json")
        jsonschema.validate(obs["admission_dimensions"], read_json(schemas/"auditory-admission.schema.json"))
