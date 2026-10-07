"""Atomic attachment of synthetic observer records; no media uploads or API calls."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.auditory_claims import claim_delta
from avevidence.common import AVError, file_record, finish_run, read_json, sha256, write_json
from avevidence.evidence_claims import validate_claims
from avevidence.inventory import verify_run


class AtomicAuditoryAttachment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic-source.bin"
        self.source.write_bytes(b"A generated unit-test source identity, not auditory evidence.")
        self.source_hash = sha256(self.source)
        self.config = self.root / "claims.json"
        self.data = {"schema": "ave.claims.v1", "evidence": [], "claims": [
            self.claim("first", [10, 10.5]), self.claim("second", [11, 11.5])]}
        write_json(self.config, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def claim(self, cid, interval, *, stream=2):
        return {"id": cid, "statement": "Synthetic delivery question for " + cid,
                "kind": "delivery", "required_modalities": ["audio"],
                "locators": [{"source_sha256": self.source_hash,
                              "clock": "original_pts_minus_source_origin",
                              "stream_index": stream, "intervals_seconds": [interval]}],
                "evidence_ids": [], "counterevidence_ids": [], "inference": None,
                "interpretation": None, "alternatives": [], "confidence": "unknown"}

    def observer_run(self, *, spans=None, name="observer"):
        spans = spans or [[.1, .4], [1.1, 1.4]]
        parsed = {"observations": [
            {"start_s": a, "end_s": b, "description": "Distinct synthetic observation %d." % index}
            for index, (a, b) in enumerate(spans)],
            "alternatives": [], "interference": [], "abstentions": [], "confidence": "low"}
        folder = self.root / name
        folder.mkdir()
        write_json(folder / "observation.json", {
            "schema": "ave.auditory-observation.v1", "observer_type": "model_audio",
            "validation_status": "VALID", "capability_status": "PROBE_PASSED",
            "backend_identity": {"type": "model_audio", "route_type": "local",
                                 "model_revision": "synthetic-unit-fixture", "adapter_revision": "1"},
            "source_sha256": self.source_hash, "stream_index": 2,
            "source_interval_seconds": [10, 12], "duration_seconds": 2,
            "parsed": parsed, "raw_response": json.dumps(parsed)})
        finish_run(folder, "observer-observe", [file_record(self.source)],
                   metadata={"atomic_observation_files": ["observation.json"]})
        return folder

    def decisions(self, claims=None, *, disposition="SUPPORT"):
        path = self.root / ("decisions-" + disposition + ".json")
        write_json(path, {"decisions": [
            {"claim_id": cid, "disposition": disposition,
             "reviewer": "Synthetic coordinator", "reason": "Explicit synthetic attachment test.",
             "deterministic_agreement": "NOT_REVIEWED",
             "analytical_consequence": "No real performance is analyzed."}
            for cid in (claims or ["first", "second"])]})
        return path

    def test_two_disjoint_claims_attach_only_their_atomic_statements(self):
        observed = self.observer_run()
        before = (observed / "observation.json").read_bytes()
        output = self.root / "delta"
        claim_delta(self.config, [observed], output, decisions=self.decisions())
        updated = read_json(output / "claims-with-auditory-witnesses.json")
        claims, witnesses, _ = validate_claims(updated, output)
        self.assertEqual(len(witnesses), 2)
        self.assertEqual(len({e["artifact"]["sha256"] for e in witnesses.values()}), 1)
        for index, claim in enumerate(claims):
            self.assertEqual(len(claim["evidence_ids"]), 1)
            evidence = witnesses[claim["evidence_ids"][0]]
            self.assertEqual(evidence["observation_index"], index)
            self.assertEqual(evidence["statement"], "Distinct synthetic observation %d." % index)
            self.assertEqual(evidence["locator"]["intervals_seconds"],
                             [[10 + index + .1, 10 + index + .4]])
            self.assertEqual(claim["status"], "OPEN")
        self.assertEqual((observed / "observation.json").read_bytes(), before)
        verify_run(output)

    def test_selecting_one_claim_preserves_other_observation_without_attachment(self):
        output = self.root / "one-claim"
        claim_delta(self.config, [self.observer_run()], output, claim_ids=["first"],
                    decisions=self.decisions(["first"]))
        updated = read_json(output / "claims-with-auditory-witnesses.json")
        self.assertEqual(len(updated["evidence"]), 2)
        self.assertEqual(len(updated["claims"][0]["evidence_ids"]), 1)
        self.assertEqual(updated["claims"][1]["evidence_ids"], [])
        self.assertNotIn("pending_auditory_evidence_ids", updated["claims"][1])

    def test_boundary_crossing_is_retained_unclipped_and_cannot_support_or_contradict(self):
        observed = self.observer_run(spans=[[.4, .7]])
        output = self.root / "boundary"
        claim_delta(self.config, [observed], output, claim_ids=["first"])
        updated = read_json(output / "claims-with-auditory-witnesses.json")
        evidence = updated["evidence"][0]
        self.assertEqual(evidence["locator"]["intervals_seconds"], [[10.4, 10.7]])
        self.assertEqual(updated["claims"][0]["pending_auditory_evidence_ids"], [])
        row = read_json(output / "AUDITORY_CLAIM_DELTA.json")["claims"][0]
        self.assertEqual(row["new_auditory_evidence_ids"], [])
        self.assertFalse(row["unadmitted_results"][0]["admitted_as_auditory_evidence"])
        for disposition in ("SUPPORT", "CONTRADICT"):
            with self.subTest(disposition=disposition):
                with self.assertRaisesRegex(AVError, "affirmative admitted"):
                    claim_delta(self.config, [observed], self.root / disposition,
                                claim_ids=["first"], decisions=self.decisions(["first"], disposition=disposition))

    def test_atom_selector_statement_and_timing_are_verified_against_raw_response(self):
        output = self.root / "selector"
        claim_delta(self.config, [self.observer_run()], output, decisions=self.decisions())
        original = read_json(output / "claims-with-auditory-witnesses.json")
        for selector in (True, False, None, -1, 2, .5, "0"):
            with self.subTest(selector=selector):
                changed = copy.deepcopy(original)
                changed["evidence"][0]["observation_index"] = selector
                with self.assertRaisesRegex(AVError, "valid observation index"):
                    validate_claims(changed, output)
        for key, value in (("statement", "An invented atomic description."),
                           ("locator", dict(original["evidence"][0]["locator"],
                                            intervals_seconds=[[10.2, 10.3]]))):
            with self.subTest(field=key):
                changed = copy.deepcopy(original)
                changed["evidence"][0][key] = value
                with self.assertRaisesRegex(AVError, "retained observation"):
                    validate_claims(changed, output)

    def test_counterevidence_is_checked_for_claim_timing_and_selected_stream(self):
        output = self.root / "counter"
        claim_delta(self.config, [self.observer_run()], output,
                    decisions=self.decisions(disposition="CONTRADICT"))
        original = read_json(output / "claims-with-auditory-witnesses.json")
        for field, value in (("intervals_seconds", [[9, 9.5]]), ("stream_index", 3)):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed["claims"][0]["locators"][0][field] = value
                with self.assertRaises(AVError):
                    validate_claims(changed, output)

    def test_atomic_selector_cannot_bypass_failed_raw_validation_for_interpretation(self):
        output = self.root / "invalid-raw"
        claim_delta(self.config, [self.observer_run()], output, decisions=self.decisions())
        updated = read_json(output / "claims-with-auditory-witnesses.json")
        for claim in updated["claims"]:
            claim["kind"] = "interpretation"
        evidence = updated["evidence"][0]
        raw_path = output / evidence["artifact"]["path"]
        observation = read_json(raw_path)
        observation["raw_response"] = "{}"
        invalid_path = self.root / "invalid-observation.json"
        write_json(invalid_path, observation)
        evidence["artifact"] = {"path": str(invalid_path), "sha256": sha256(invalid_path)}
        with self.assertRaisesRegex(AVError, "requires a validated retained observation"):
            validate_claims(updated, output)

    def test_portable_delta_keeps_exact_full_response_and_valid_atomic_selection(self):
        observed = self.observer_run()
        raw = (observed / "observation.json").read_bytes()
        output = self.root / "portable"
        claim_delta(self.config, [observed], output, decisions=self.decisions())
        moved = self.root / "moved"
        shutil.copytree(output, moved)
        updated = read_json(moved / "claims-with-auditory-witnesses.json")
        _, witnesses, _ = validate_claims(updated, moved)
        for evidence in witnesses.values():
            self.assertFalse(Path(evidence["artifact"]["path"]).is_absolute())
            path = moved / evidence["artifact"]["path"]
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(sha256(path), evidence["artifact"]["sha256"])
        verify_run(moved)


if __name__ == "__main__":
    unittest.main()
