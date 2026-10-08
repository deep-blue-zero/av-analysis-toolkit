"""Synthetic cross-modal/reuse/portable regressions, not real-media validation."""
import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_music_identity as fixtures
from test_music_identity import RID1, RID2, WID1, WID2, cat, digest
from avevidence.common import AVError, read_json, run, sha256, write_json
from avevidence.cross_modal import evidence_assessment, reconcile_scene
from avevidence.event_contracts import CLOCK
from avevidence.inventory import verify_run
from avevidence.music_identity.backends import MusicBrainzNormalizer
from avevidence.music_identity.contracts import validate_result
from avevidence.music_identity.families import group_candidates
from avevidence.music_identity.portability import export_portable, rebind
from avevidence.music_identity.workflow import identify
from avevidence.scene_packets import load_scene_packet, validate_packet
from avevidence.scene_delta import scene_claim_delta
from avevidence.scene_planning import plan_scene_queries


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe required")
class LocalMusicTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    wav = staticmethod(fixtures.WorkflowTests.wav)
    initial = fixtures.WorkflowTests.initial
    review = fixtures.WorkflowTests.review

    def test_exact_reference_preserves_existing_correspondence_ids(self):
        reference = self.root / "generated-reference.wav"
        self.wav(reference, self.samples[:32000])
        folder, raw = self.initial(references=[reference], reference_end=4.)
        compare = raw["reference_comparisons"][0]
        self.assertTrue(compare["match_ids"])
        self.assertTrue(any(m["status"] == "EXACT" for m in raw["correspondences"]))
        self.assertEqual(compare["recording_identity_effect"], "NONE")
        self.assertEqual(raw["adjudication"][0]["status"], "UNRESOLVED")
        verify_run(folder / "reference-1")
        public = self.root / "portable"
        export_portable(folder, public)
        self.assertNotIn(str(self.root), (public / "music-identity.json").read_text(encoding="utf-8"))

    def test_recording_support_requires_credited_full_native_reference(self):
        reference = self.root / "generated-reference.wav"
        self.wav(reference, self.samples[:32000])
        folder, raw = self.initial(references=[reference], reference_end=4.)
        matches = [m for m in raw["correspondences"] if m["status"] == "EXACT"]
        reviewed, result = self.review(folder, raw, level="RECORDING", kinds=("REFERENCE_IDENTITY",),
            match_ids=[m["match_id"] for m in matches], reference_source_sha256=sha256(reference), reference_audio_stream=0)
        self.assertEqual(result["adjudication"][0]["status"], "SUPPORTED")

    def test_recording_support_rejects_wrong_reference_hash(self):
        reference = self.root / "generated-reference.wav"
        self.wav(reference, self.samples[:32000])
        folder, raw = self.initial(references=[reference], reference_end=4.)
        with self.assertRaises(AVError):
            self.review(folder, raw, level="RECORDING", kinds=("REFERENCE_IDENTITY",),
                match_ids=[m["match_id"] for m in raw["correspondences"]], reference_source_sha256=digest("wrong"), reference_audio_stream=0)

    def test_gain_and_recompression_use_existing_reuse_engine(self):
        reference = self.root / "generated-gain.wav"
        self.wav(reference, self.samples[:32000] * .6)
        compressed = self.root / "generated-lossless.mka"
        run(["ffmpeg", "-v", "error", "-i", str(reference), "-c:a", "flac", str(compressed)])
        folder, raw = self.initial(references=[compressed], reference_end=4.)
        self.assertTrue(any(m["status"] == "NEAR_EXACT" for m in raw["correspondences"]))
        self.assertTrue(any("gain_changed" in m["transformations"] for m in raw["correspondences"]))

    def test_same_melody_different_realization_no_waveform_identity(self):
        import numpy as np
        t = np.arange(32000) / 8000
        self.wav(self.source, .15 * np.sin(2 * np.pi * (220 * t + 30 * t * t)))
        reference = self.root / "generated-other-realization.wav"
        x = .05 * np.sin(2 * np.pi * (220 * t + 30 * t * t)) + .17 * np.sin(4 * np.pi * (220 * t + 30 * t * t))
        self.wav(reference, x)
        folder, raw = self.initial(references=[reference], reference_end=4.)
        self.assertFalse(any(m["status"] == "EXACT" for m in raw["correspondences"]))
        self.assertEqual(raw["reference_comparisons"][0]["ordered_chroma"]["recording_identity_effect"], "NONE")
        self.assertTrue(all(d["status"] == "UNRESOLVED" for d in raw["adjudication"]))

    def test_transformed_search_is_explicit_and_provisional(self):
        from avevidence.reuse.correlate import compare
        import numpy as np
        a = self.samples[:16000, None]
        # Pitch/speed changed source; the existing detector owns all hypotheses and metrics.
        b = np.interp(np.arange(0, len(a), 1.02), np.arange(len(a)), a[:, 0])[:, None]
        rows, search = compare(a, b, 8000, 8000, transforms=True, fragments=False)
        self.assertEqual(search["nonmatch_status"], "UNRESOLVED")
        self.assertFalse(search["score_is_probability"])
        self.assertTrue(all(r["classification"] in {"EXACT", "NEAR_EXACT", "PROBABLE_DERIVATIVE", "POSSIBLE_DERIVATIVE"} for r in rows))

    def test_local_corpus_snapshot_does_not_mutate_ledger(self):
        from avevidence.reuse.workflow import run_scan
        database = self.root / "corpus.sqlite"
        config = self.root / "reuse-config.json"
        write_json(config, {"schema": "ave.reuse.config.v1", "occurrences": [{"source": str(self.source), "id": "ref",
            "audio_stream": 0, "start_s": 0, "end_s": 4, "role": "reference", "unit_kind": "window"}]})
        run_scan(config, self.root / "corpus-run", cache_dir=self.root / "cache", database=database, preview_count=0)
        before = sha256(database)
        query = self.root / "generated-query.wav"
        self.wav(query, self.samples[:32000])
        output = self.root / "identity"
        identify(query, output, start=0, end=4, local_corpus=database)
        result = read_json(output / "music-identity.json")
        self.assertEqual(sha256(database), before)
        self.assertTrue(result["correspondences"])
        self.assertEqual(result["queries"][1]["provider"], "local-reuse")

    def test_known_corpus_window_and_persistent_index_survive_repeat_queries(self):
        from avevidence.reuse.workflow import run_scan
        database, cache = self.root / "corpus.sqlite", self.root / "persistent-cache"
        config = self.root / "reuse-config.json"
        write_json(config, {"schema": "ave.reuse.config.v1", "occurrences": [{"source": str(self.source), "id": "ref",
            "audio_stream": 0, "start_s": 0, "end_s": 4, "role": "reference", "unit_kind": "window"}]})
        run_scan(config, self.root / "corpus-run", cache_dir=cache, database=database, preview_count=0)
        before = sha256(database)
        folder, raw = self.initial(local_corpus=database, reuse_cache=cache)
        again, repeat = self.initial("repeat", local_corpus=database, reuse_cache=cache)
        self.assertEqual(sha256(database), before)
        self.assertTrue(any(c["lookup_basis"] == "LOCAL_CORPUS_SOURCE_IDENTITY" for c in raw["candidates"]))
        self.assertEqual(raw["candidates"], repeat["candidates"])
        self.assertTrue((cache / "reuse-fingerprints.sqlite").is_file())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe required")
class SceneMusicTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    wav = staticmethod(fixtures.WorkflowTests.wav)
    initial = fixtures.WorkflowTests.initial
    review = fixtures.WorkflowTests.review

    def packet(self, music_folder, source, *, proposition="composition_identity", musical_status="SUPPORTED"):
        witness = self.root / "generated-dialogue.json"
        if not witness.exists():
            write_json(witness, {"text": "Generated literal dialogue unrelated to music identity"})
        def ref(name, path):
            return {"id": name, "artifact": {"path": str(path), "sha256": sha256(path)},
                "locator": {"source_sha256": source["sha256"], "stream_index": source["audio_stream"], "clock": CLOCK,
                            "intervals_seconds": [source["interval_seconds"]]}}
        packet = {"schema": "ave.scene-packet.v1", "scene_id": "generated-music-event",
            "source": {"sha256": source["sha256"], "audio_stream": source["audio_stream"], "clock": CLOCK},
            "interval_seconds": source["interval_seconds"], "narrative_boundary": {"basis": "synthetic_event", "description": "Generated music/text event"},
            "question": "What does the music identity add to this scene?", "evidence": {"MID": [ref("mid", music_folder / "music-identity.json")],
                "TXT": [{**ref("txt", witness), "authority": "canonical_text"}]},
            "observations": [{"id": "music-observation", "proposition": proposition, "statement": "Generated composition attribution",
                "status": musical_status, "evidence_ids": ["mid"]}, {"id": "text-observation", "proposition": "wording",
                "statement": "Generated literal dialogue", "status": "SUPPORTED", "evidence_ids": ["txt"]}],
            "claims": [{"id": "music-claim", "proposition": proposition, "statement": "Generated music identity",
                "status": musical_status, "observation_ids": ["music-observation"]}, {"id": "text-claim", "proposition": "wording",
                "statement": "Unrelated literal dialogue", "status": "SUPPORTED", "observation_ids": ["text-observation"]}],
            "dependencies": [{"from": "music-observation", "to": "music-claim", "relation": "SUPPORTS", "reason": "Scoped musical premise"},
                             {"from": "text-observation", "to": "text-claim", "relation": "SUPPORTS", "reason": "Separate literal premise"}],
            "conflicts": [], "open_questions": [],
            "adjudication": {"reviewer": "Generated scene reviewer", "decisions": {"music-observation": {"state": musical_status, "reason": "Scoped identity evidence"},
                "music-claim": {"state": musical_status, "reason": "Scoped identity evidence"}, "text-observation": {"state": "SUPPORTED", "reason": "Admitted text"},
                "text-claim": {"state": "SUPPORTED", "reason": "Admitted text"}}}}
        return packet

    def assess(self, folder, source, proposition):
        ref = {"id": "mid", "channel": "MID", "artifact": {"path": str(folder / "music-identity.json"), "sha256": sha256(folder / "music-identity.json")},
            "locator": {"source_sha256": source["sha256"], "stream_index": source["audio_stream"], "clock": CLOCK, "intervals_seconds": [source["interval_seconds"]]}}
        return evidence_assessment(ref, {"path": str(folder / "music-identity.json")}, proposition, source["interval_seconds"])

    def test_twilight_credit_vs_nonmatch_not_cross_level_contradiction(self):
        import numpy as np
        reference = self.root / "generated-nonoverlapping-excerpt.wav"
        self.wav(reference, np.random.default_rng(271).normal(0, .15, 32000))
        folder, raw = self.initial(references=[reference], reference_end=4.)
        self.assertFalse(any(m["status"] in {"EXACT", "NEAR_EXACT"} for m in raw["correspondences"]))
        reviewed, result = self.review(folder, raw)
        self.assertTrue(self.assess(reviewed, result["source"], "composition_identity")["adequate_for_support"])
        self.assertFalse(self.assess(reviewed, result["source"], "recording_identity")["adequate_for_support"])
        packet = self.packet(reviewed, result["source"])
        validate_packet(packet)
        self.assertEqual(packet["conflicts"], [])

    def test_multiple_beat_it_covers_support_composition_not_master(self):
        folder, raw = self.initial()
        # Catalogue normalization produces a family proposal, then an explicit synthetic listening review adjudicates composition.
        normalizer = MusicBrainzNormalizer(["musicbrainz"], transport=lambda method, url, *a: json.dumps(cat(RID2 if RID2 in url else RID1)).encode())
        a, aa = normalizer.query(RID1, raw["source"])
        b, bb = normalizer.query(RID2, raw["source"])
        self.assertEqual(len(group_candidates(aa + bb)), 1)
        self.assertEqual(group_candidates(aa + bb)[0]["recording_identity_status"], "UNRESOLVED")
        reviewed, result = self.review(folder, raw, kinds=("SCOPED_IDENTITY_REVIEW",),
            actual_audio_inspection_declared=True, comparison_basis="Generated shared melody/lyrics, distinct cover realizations")
        self.assertTrue(self.assess(reviewed, result["source"], "composition_identity")["adequate_for_support"])
        self.assertFalse(self.assess(reviewed, result["source"], "recording_identity")["adequate_for_support"])

    def test_strong_family_supported_only_by_separate_scoped_adjudication(self):
        from avevidence.music_identity.review import adjudicate
        from avevidence.music_identity.fingerprint import parse_fpcalc
        import os
        fake = {**parse_fpcalc('{"duration":4,"fingerprint":"abcDEF_123456"}', 4.),
                "implementation": {}, "command": [], "witness_sha256": digest("witness")}
        def transport(method, url, *args):
            if "acoustid" in url:
                return json.dumps({"status": "ok", "results": [{"id": "mock-hit", "score": .92,
                    "recordings": [{"id": RID1, "title": "Beat It"}, {"id": RID2, "title": "Beat It"}]}]}).encode()
            return json.dumps(cat(RID2 if RID2 in url else RID1, title="Beat It")).encode()
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": "generated-test-client-key"}), patch("avevidence.music_identity.fingerprint.calculate", return_value=fake):
            folder, raw = self.initial(providers=["acoustid"], allow_external_lookup=["acoustid", "musicbrainz"],
                normalize_musicbrainz=True, catalogue_cache=self.root / "catalogue-cache", transport=transport)
        family = raw["families"][0]
        self.assertEqual(len(raw["families"]), 1)
        self.assertEqual(family["status"], "CANDIDATE")
        evidence = self.root / "generated-listening-declaration.json"
        write_json(evidence, {"source": raw["source"], "scope": "Generated review contract; no actual listening"})
        config = self.root / "family-review.json"
        write_json(config, {"schema": "ave.music-identity.review.v1", "reviewer": "Generated reviewer", "source": raw["source"],
            "evidence": [{"evidence_id": "scoped-listening", "kind": "SCOPED_IDENTITY_REVIEW", "level": "COMPOSITION",
                "candidate_ids": family["candidate_ids"], "source": raw["source"], "actual_inspection_declared": True,
                "actual_audio_inspection_declared": True, "comparison_basis": "Generated lyric/melody comparison against several cover controls",
                "statement": "Generated composition-review contract", "artifact": {"path": evidence.name, "sha256": sha256(evidence)}}],
            "decisions": [{"level": "COMPOSITION", "status": "SUPPORTED", "candidate_ids": family["candidate_ids"],
                "family_ids": [family["family_id"]], "evidence_ids": ["scoped-listening"], "reason": "Explicit scoped synthetic review"}]})
        reviewed = self.root / "reviewed"
        adjudicate(folder, config, reviewed)
        result = read_json(reviewed / "music-identity.json")
        self.assertEqual(result["families"][0]["status"], "SUPPORTED")
        self.assertEqual(result["adjudication"][0]["status"], "UNRESOLVED")
        self.assertTrue(self.assess(reviewed, result["source"], "composition_identity")["adequate_for_support"])

    def test_negative_lookup_remains_open_without_originality_claim(self):
        folder, raw = self.initial()
        assessment = self.assess(folder, raw["source"], "composition_identity")
        self.assertEqual(assessment["identity_status"], "UNRESOLVED")
        self.assertFalse(assessment["adequate_for_support"])

    def test_unvalidated_model_name_cannot_close_identity(self):
        folder, raw = self.initial()
        self.assertFalse(self.assess(folder, raw["source"], "recording_identity")["adequate_for_support"])

    def test_mid_cannot_establish_emotion_intent_or_delivery(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        for proposition in ("emotion", "interpretation", "delivery", "motion"):
            with self.subTest(proposition=proposition):
                self.assertFalse(self.assess(reviewed, result["source"], proposition)["competent"])

    def test_scene_locator_wrong_stream_rejected(self):
        folder, raw = self.initial()
        packet = self.packet(folder, raw["source"])
        packet["evidence"]["MID"][0]["locator"]["stream_index"] = 1
        with self.assertRaises(AVError):
            validate_packet(packet)

    def test_mid_cannot_borrow_identity_from_another_file_in_run(self):
        folder, raw = self.initial()
        packet = self.packet(folder, raw["source"])
        path = folder / "run.json"
        packet["evidence"]["MID"][0]["artifact"] = {"path": str(path), "sha256": sha256(path)}
        saved = self.root / "wrong-artifact.json"
        write_json(saved, packet)
        with self.assertRaises(AVError):
            load_scene_packet(saved)

    def test_music_plan_carries_selected_stream_no_submission(self):
        folder, raw = self.initial()
        packet = self.packet(folder, raw["source"], musical_status="OPEN")
        path = self.root / "scene.json"
        write_json(path, packet)
        output = self.root / "plan"
        plan_scene_queries(path, output)
        report = read_json(output / "query-plan.json")
        music = [r for r in report["requests"] if r["route"] == "MUSIC_IDENTITY"]
        self.assertTrue(music)
        self.assertEqual(music[0]["audio_stream"], 0)
        self.assertFalse(music[0]["api_submission_authorized"])

    def test_bilibili_enrichment_preserves_unrelated_text_reading(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        before, after = self.root / "before.json", self.root / "after.json"
        p1 = self.packet(folder, raw["source"], musical_status="OPEN")
        p2 = self.packet(reviewed, result["source"])
        write_json(before, p1); write_json(after, p2)
        r1, r2 = self.root / "before-reconciled", self.root / "after-reconciled"
        reconcile_scene(before, r1); reconcile_scene(after, r2)
        b = read_json(r1 / "reconciliation.json"); a = read_json(r2 / "reconciliation.json")
        bc, ac = {r["id"]: r for r in b["claims"]}, {r["id"]: r for r in a["claims"]}
        self.assertEqual(bc["text-claim"], ac["text-claim"])
        self.assertNotEqual(bc["music-claim"]["state"], ac["music-claim"]["state"])
        decisions = self.root / "delta-decisions.json"
        write_json(decisions, {"reviewer": "Generated delta reviewer", "decisions": {"music-claim": {
            "disposition": "STRENGTHEN", "reason": "New scoped music identity adjudication", "what_survived": "Literal scene context",
            "what_changed": "Composition identity now has competent scoped support", "remaining_open_questions": ["Exact recording identity remains unresolved"]}}})
        delta_folder = self.root / "delta"
        scene_claim_delta(before, after, delta_folder, decisions=decisions)
        delta = read_json(delta_folder / "claim-delta.json")
        changes = {r["claim_id"]: r for r in delta["claims"]}
        self.assertEqual(changes["text-claim"]["disposition"], "PRESERVE")
        self.assertEqual(changes["music-claim"]["disposition"], "STRENGTHEN")
        import jsonschema
        schemas = Path(__file__).resolve().parents[1] / "schemas"
        jsonschema.validate(a, read_json(schemas / "scene-reconciliation.schema.json"))
        jsonschema.validate(delta, read_json(schemas / "scene-claim-delta.schema.json"))
        # The earlier packet remains unchanged even after the enriched reconciliation.
        self.assertEqual(read_json(before), p1)

    def test_json_schemas_cover_private_and_public_results(self):
        import jsonschema
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        root = Path(__file__).resolve().parents[1] / "schemas"
        schema = read_json(root / "music-identity.schema.json")
        jsonschema.validate(raw, schema)
        jsonschema.validate(result, schema)
        public = self.root / "portable"
        export_portable(reviewed, public)
        jsonschema.validate(read_json(public / "music-identity.json"), schema)
        jsonschema.validate(read_json(public / "aliases.json"), read_json(root / "music-identity-aliases.schema.json"))
        jsonschema.validate(self.packet(reviewed, result["source"]), read_json(root / "scene-packet.schema.json"))

    def test_scoped_adjudication_rebinding_requires_evidence_hashes(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        public = self.root / "portable"
        export_portable(reviewed, public)
        with self.assertRaises(AVError):
            rebind(public, self.source, self.root / "rebound-missing")
        bindings = {}
        for e in result["evidence"]:
            bindings["artifact-" + e["artifact"]["sha256"][:24]] = str(reviewed / e["artifact"]["path"])
        mapping = self.root / "evidence-bindings.json"
        write_json(mapping, bindings)
        rebind(public, self.source, self.root / "rebound", evidence_bindings=mapping)
        self.assertTrue(self.assess(self.root / "rebound", result["source"], "composition_identity")["adequate_for_support"])
