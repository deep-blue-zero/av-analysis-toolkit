"""Locally generated, blinded signals and injected zero-cost transports."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from avevidence.auditory_observer import make_probe
from avevidence.common import AVError, read_json, sha256
from avevidence.observer_execution import probe_backend
from avevidence.providers.openai_audio import OpenAIAudioObserver
from avevidence.providers.openai_common import HostedError
from test_hosted_observer import KEY, SemanticTransport, Transport


class JSONSemanticTransport(SemanticTransport):
    def __call__(self, payload, key, timeout):
        result = super().__call__(payload, key, timeout)
        raw = result["choices"][0]["message"]["content"]
        result["choices"][0]["message"]["content"] = json.dumps({"choice": int(raw)})
        return result


class ProbeV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ave-probe-v2-")
        cls.root = Path(cls.temp.name)
        cls.fixtures = cls.root/"fixtures"
        make_probe(cls.fixtures, trials=24, protocol="v2")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def probe(self, transport, name):
        backend = OpenAIAudioObserver(transport=transport, credential_env="AVE_TEST_KEY", allow_remote_media="openai")
        with patch.dict(os.environ, {"AVE_TEST_KEY": KEY}):
            probe_backend(self.fixtures, self.root/name, backend=backend)
        return read_json(self.root/name/"capability.json")

    def test_balanced_positions_and_reverse_pairs_in_each_family(self):
        questions = read_json(self.fixtures/"observer-input"/"questions.json")
        truth = read_json(self.fixtures/"adjudication"/"answers.json")["trials"]
        self.assertEqual(questions["schema"], "ave.blinded-audio-probe.v2")
        for family in ("pitch_direction", "signal_presence"):
            family_rows = [r for r in truth if r["family"] == family]
            self.assertEqual([r["answer"] for r in family_rows].count(0), 6)
            self.assertEqual([r["answer"] for r in family_rows].count(1), 6)
        pairs = {}
        for q, answer in zip(questions["trials"], truth):
            pairs.setdefault(answer["pair_id"], []).append(q)
            self.assertNotIn("answer", q)
            self.assertNotIn("pair_id", q)
        for rows in pairs.values():
            a, b = [[sha256(self.fixtures/"observer-input"/name) for name in row["clips"]] for row in rows]
            self.assertEqual(a, b[::-1])

    def test_json_choice_success_is_input_influence_only(self):
        transport = JSONSemanticTransport()
        record = self.probe(transport, "success")
        self.assertEqual(record["status"], "PROBE_PASSED")
        self.assertEqual(record["task_qualification"], "NONE_INPUT_INFLUENCE_ONLY")
        self.assertEqual(record["overall_failure_rate"], 0)
        self.assertTrue(all(f["passed"] for f in record["paired_results"].values()))
        self.assertTrue(all(call[0]["max_completion_tokens"] == 128 for call in transport.calls))

    def test_malformed_responses_count_as_failures(self):
        record = self.probe(Transport(raw='{"choice": false}'), "malformed")
        self.assertEqual(record["status"], "PROBE_FAILED")
        self.assertEqual(record["overall_failure_rate"], 1.)
        self.assertEqual({r["outcome"] for r in record["outcomes"]}, {"MALFORMED_RESPONSE"})
        responses = read_json(self.root/"malformed"/"probe-responses.json")
        self.assertTrue(all(r["raw_response"] == '{"choice": false}' for r in responses["trials"]))

    def test_wrong_answers_differ_from_contract_failures(self):
        record = self.probe(Transport(raw='{"choice": 0}'), "constant")
        self.assertEqual(record["probe_result"]["correct"], 12)
        self.assertEqual({r["outcome"] for r in record["outcomes"]}, {"CORRECT", "INCORRECT_PERCEPTUAL_JUDGMENT"})
        self.assertEqual(record["status"], "PROBE_FAILED")

    def test_transport_failure_is_separate_and_not_retried(self):
        transport = Transport(error=HostedError("PROVIDER_REQUEST_FAILED", "Synthetic outage"))
        record = self.probe(transport, "transport")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(record["outcomes"][0]["outcome"], "TRANSPORT_FAILURE")
        self.assertEqual(record["not_attempted_trials"], 23)
        self.assertEqual(record["overall_failure_rate"], 1.)

    def test_new_protocol_cannot_rewrite_historical_failure(self):
        historical = self.root/"historical-failure.json"
        historical.write_text('{"status":"PROBE_FAILED","correct":4,"trials":12}', encoding="utf-8")
        before = sha256(historical)
        self.probe(JSONSemanticTransport(), "separate-new-run")
        self.assertEqual(sha256(historical), before)
        self.assertEqual(read_json(historical)["status"], "PROBE_FAILED")

    def test_protocol_size_requires_balanced_pairs(self):
        with self.assertRaises(AVError):
            make_probe(self.root/"invalid", trials=14, protocol="v2")
