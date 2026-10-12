"""Monetary section ceilings at the provider boundary, with no live transport."""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.common import AVError, sha256
from avevidence.providers.openai_audio import OpenAIAudioObserver
from avevidence.providers.openai_common import BudgetSession, HostedError, MODEL


class MusicalSectionBudget(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / "synthetic.wav"
        with wave.open(str(self.audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\0\0" * 8000)
        self.env = patch.dict(os.environ, {
            "AVE_TEST_BUDGET_KEY": "synthetic-test-credential-never-valid-for-a-provider"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = patch("avevidence.providers.openai_audio.http_transport",
                          side_effect=AssertionError("Live HTTP is forbidden in budget tests"))
        self.http.start()
        self.addCleanup(self.http.stop)
        self.calls = []
        self.usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                      "prompt_tokens_details": {"audio_tokens": 50},
                      "completion_tokens_details": {"audio_tokens": 0}}

    def transport(self, payload, key, timeout):
        self.calls.append(copy.deepcopy(payload))
        raw = json.dumps({"observations": [], "alternatives": [], "interference": [],
                          "abstentions": ["Synthetic budget fixture only."], "confidence": "unknown"})
        return {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {"content": raw}}],
                "usage": copy.deepcopy(self.usage)}

    def backend(self, *, request_limit=.30, run_limit=.60, queue_limit=.60):
        session = BudgetSession(self.root / ("run-%d" % len(list(self.root.glob("run-*")))),
                                budget_usd=run_limit, request_budget_usd=request_limit,
                                queue_budget_usd=queue_limit)
        backend = OpenAIAudioObserver(credential_env="AVE_TEST_BUDGET_KEY", allow_remote_media="openai",
                                     session=session, transport=self.transport)
        # This is an adapter unit test, not a capability receipt or source claim.
        backend.admitted_capability = backend.identity
        backend.admitted_returned_model = MODEL
        return backend

    def request(self, ceiling=.30):
        return {"request_id": "synthetic-budget-test", "prompt": "Describe only this synthetic fixture.",
                "task_profile": "PERFORMANCE_MUSIC", "audio_mode": "coherent_section",
                "allow_long_section": True, "section_budget_usd": ceiling}

    def clip(self):
        return {"label": "A", "clip_sha256": sha256(self.audio), "duration_seconds": 1.,
                "execution_audio_path": str(self.audio)}

    def test_equal_decimal_section_ceiling_allows_mock_submission_and_settlement(self):
        backend = self.backend()
        self.assertEqual(backend.session.request_limit, Decimal("0.30"))
        result = backend.observe(self.request(), self.clip())
        self.assertEqual(json.loads(result)["observations"], [])
        self.assertEqual(len(self.calls), 1)
        receipt = backend.last_receipt
        self.assertEqual(receipt["execution_mode"], "TEST_DOUBLE")
        self.assertEqual(receipt["audio_policy"]["section_budget_usd"], .30)
        self.assertEqual(receipt["usage"]["request_limit_usd"], .30)
        self.assertEqual(receipt["usage"]["run_limit_usd"], .60)
        self.assertEqual(receipt["usage"]["queue_limit_usd"], .60)
        summary = backend.session.summary()
        self.assertEqual(summary["completed_operations"], 1)
        self.assertGreater(summary["actual_cost_usd"], 0)
        self.assertFalse(summary["billing_unresolved"])

    def test_equal_and_lower_decimal_limits_use_the_same_monetary_representation(self):
        for request_limit, ceiling in ((.10, .10), (.20, .20), (.30, .30), (.60, .60),
                                       (.25, .30), (1, 1.), (1., 1)):
            with self.subTest(request_limit=request_limit, ceiling=ceiling):
                backend = self.backend(request_limit=request_limit, run_limit=2., queue_limit=2.)
                before = len(self.calls)
                backend.observe(self.request(ceiling), self.clip())
                self.assertEqual(len(self.calls), before + 1)

    def test_limits_above_the_section_ceiling_are_refused_before_accounting(self):
        for request_limit in (.31, .30000000000000004):
            with self.subTest(request_limit=request_limit):
                backend = self.backend(request_limit=request_limit)
                with self.assertRaisesRegex(HostedError, "explicit musical-section ceiling"):
                    backend.observe(self.request(), self.clip())
                self.assertFalse(backend.session.local.exists())
        self.assertEqual(self.calls, [])

    def test_section_ceiling_does_not_raise_request_run_or_queue_limits(self):
        for option in ("request_limit", "run_limit", "queue_limit"):
            with self.subTest(option=option):
                backend = self.backend(**{option: .000001})
                with self.assertRaisesRegex(HostedError, "BUDGET_GUARD"):
                    backend.observe(self.request(), self.clip())
                self.assertFalse(backend.session.local.exists())
        self.assertEqual(self.calls, [])

    def test_equal_budget_does_not_bypass_audio_bounds_or_witness_identity(self):
        backend = self.backend()
        clip = self.clip()
        clip["duration_seconds"] = 121.
        with self.assertRaisesRegex(AVError, "explicit clip, section or comparison bounds"):
            backend.observe(self.request(), clip)
        clip = self.clip()
        clip["clip_sha256"] = "0" * 64
        with self.assertRaisesRegex(AVError, "no longer match the source-bound witness"):
            backend.observe(self.request(), clip)
        self.assertFalse(backend.session.local.exists())
        self.assertEqual(self.calls, [])

    def test_equal_budget_does_not_bypass_explicit_upload_authorization(self):
        backend = self.backend()
        backend.allow_remote_media = None
        with self.assertRaisesRegex(HostedError, "HOSTED_MEDIA_NOT_AUTHORIZED"):
            backend.observe(self.request(), self.clip())
        self.assertFalse(backend.session.local.exists())
        self.assertEqual(self.calls, [])

    def test_unresolved_usage_blocks_the_next_request_at_an_equal_ceiling(self):
        backend = self.backend()
        self.usage = None
        backend.observe(self.request(), self.clip())
        self.assertTrue(backend.session.summary()["billing_unresolved"])
        with self.assertRaisesRegex(HostedError, "Unresolved provider usage"):
            backend.observe(self.request(), self.clip())
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(backend.last_receipt["submission_attempted"])


if __name__ == "__main__":
    unittest.main()
