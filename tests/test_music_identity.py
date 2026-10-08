"""Generated music/credit contracts and mocked catalogues; no real-song recognition."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.common import AVError, finish_run, output_transaction, probe_source, read_json, sha256, write_json
from avevidence.event_contracts import CLOCK
from avevidence.music_identity.backends import AcoustIDBackend, MusicBrainzNormalizer, normalize_musicbrainz, strict_response
from avevidence.music_identity.contracts import candidate, query_receipt, validate_result
from avevidence.music_identity.families import group_candidates
from avevidence.music_identity.fingerprint import doctor, parse_fpcalc
from avevidence.music_identity.metadata import embedded_metadata
from avevidence.music_identity.portability import assert_portable, export_portable, plan, rebind, verify_portable
from avevidence.music_identity.review import adjudicate, decision_support
from avevidence.music_identity.workflow import identify, load_music_run

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


SCOPE = {"alias": "generated-source", "display_name": "generated-source", "sha256": digest("generated"),
         "audio_stream": 0, "clock": CLOCK, "interval_seconds": [0., 4.]}
RID1 = "10000000-0000-4000-8000-000000000001"
RID2 = "10000000-0000-4000-8000-000000000002"
WID1 = "20000000-0000-4000-8000-000000000001"
WID2 = "20000000-0000-4000-8000-000000000002"
REL1 = "30000000-0000-4000-8000-000000000001"
REL2 = "30000000-0000-4000-8000-000000000002"


def cat(recording=RID1, works=(WID1,), title="Generated composition", releases=()):
    return {"id": recording, "title": title, "artist-credit": [{"name": "Generated performer"}], "isrcs": [],
        "relations": [{"target-type": "work", "type": "performance", "attributes": ["cover"],
                       "work": {"id": wid, "title": title}} for wid in works],
        "releases": [{"id": r, "title": "Generated release", "release-group": {"id": r}} for r in releases]}


def c(recording=RID1, works=(WID1,), title="Beat It", provider="musicbrainz", basis="CATALOGUE_RELATION"):
    return candidate(provider=provider, title=title, recording_id=recording, work_ids=works,
        basis=basis, query_id="test-query", interval=[0., 4.])


class MetadataTests(unittest.TestCase):
    def tags(self, fmt=None, stream=None):
        return embedded_metadata({"probe": {"format": {"tags": fmt or {}}}}, {"tags": stream or {}}, SCOPE)

    def test_valid_allowlist_and_unknown_omitted(self):
        q, candidates = self.tags({"TITLE": "Generated song", "ARTIST": "Generated performer", "comment": "private"})
        self.assertEqual(candidates[0]["lookup_basis"], "METADATA_DECLARED")
        self.assertEqual(candidates[0]["status"], "CANDIDATE")
        self.assertNotIn("comment", json.dumps(q))

    def test_conflicting_format_and_stream_preserved(self):
        q, candidates = self.tags({"title": "First"}, {"title": "Second"})
        self.assertEqual(len(candidates), 2)
        self.assertIn("conflicting:title", q["missing_fields"])

    def test_absent_metadata_is_unresolved(self):
        q, candidates = self.tags()
        self.assertEqual((q["status"], candidates), ("UNRESOLVED", []))

    def test_malformed_metadata_not_converted_to_facts(self):
        q, candidates = self.tags({"title": ["broken"], "artist": "x\nsecret"})
        self.assertFalse(candidates)
        self.assertTrue(q["missing_fields"])

    def test_missing_fields_are_null(self):
        q, candidates = self.tags({"title": "Only a title"})
        self.assertIsNone(candidates[0]["artist"])
        self.assertIsNone(candidates[0]["musicbrainz_recording_id"])

    def test_unknown_musicbrainz_id_not_invented(self):
        _, candidates = self.tags({"title": "Name", "musicbrainz_trackid": "bad-uuid"})
        self.assertIsNone(candidates[0]["musicbrainz_recording_id"])


class FingerprintTests(unittest.TestCase):
    def test_parsing_and_fingerprint_hash(self):
        result = parse_fpcalc('{"duration":4,"fingerprint":"abcDEF_123456"}', 4.1)
        self.assertEqual(result["duration_seconds"], 4)
        self.assertEqual(len(result["fingerprint_sha256"]), 64)

    def test_duration_mismatch_rejected(self):
        with self.assertRaises(AVError):
            parse_fpcalc('{"duration":30,"fingerprint":"abcDEF_123456"}', 4.)

    def test_malformed_outputs_rejected(self):
        for raw in ('garbage', '{"fingerprint":"validtext"}', '{"duration":NaN,"fingerprint":"validtext"}',
                    '{"duration":4,"fingerprint":"!!broken!!"}', '{"duration":true,"fingerprint":"validtext"}'):
            with self.subTest(raw=raw), self.assertRaises(AVError):
                parse_fpcalc(raw, 4.)

    def test_empty_or_excessively_short_fingerprint_rejected(self):
        with self.assertRaises(AVError):
            parse_fpcalc('{"duration":4,"fingerprint":""}', 4.)

    def test_duplicate_fpcalc_keys_rejected(self):
        with self.assertRaises(AVError):
            parse_fpcalc('{"duration":30,"duration":4,"fingerprint":"abcDEF_123456"}', 4.)

    def test_missing_executable_is_optional(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(doctor()["available"])


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.context = {"source": copy.deepcopy(SCOPE), "fingerprint": parse_fpcalc('{"duration":4,"fingerprint":"abcDEF_123456"}', 4.)}
        self.calls = []

    def transport(self, response):
        def send(method, url, headers, body):
            self.calls.append((method, url, headers, body))
            return json.dumps(response).encode()
        return send

    def test_network_off_without_explicit_authorization(self):
        backend = AcoustIDBackend(transport=self.transport({"status": "ok", "results": []}))
        with self.assertRaises(AVError):
            backend.query(self.context)
        self.assertFalse(self.calls)

    def test_fingerprint_only_and_secret_not_in_receipt(self):
        key = "generated-test-client-key"
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": key}):
            q, candidates = AcoustIDBackend(["acoustid"], transport=self.transport({"status": "ok", "results": []})).query(self.context)
        self.assertEqual(q["status"], "UNRESOLVED")
        self.assertFalse(candidates)
        self.assertEqual(q["submission_class"], "DERIVED_FINGERPRINT_LOOKUP")
        self.assertFalse(q["media_uploaded"])
        self.assertNotIn(key, json.dumps(q))
        from urllib.parse import parse_qs
        body = parse_qs(self.calls[0][3].decode())
        self.assertEqual(set(body), {"client", "fingerprint", "duration", "meta"})

    def test_multiple_cover_candidates_are_not_promoted(self):
        response = {"status": "ok", "results": [{"id": "lookup-hit", "score": .95,
            "recordings": [{"id": RID1, "title": "Beat It", "artists": [{"name": "Cover one"}]},
                           {"id": RID2, "title": "Beat It", "artists": [{"name": "Cover two"}]}]}]}
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": "generated-test-client-key"}):
            q, candidates = AcoustIDBackend(["acoustid"], transport=self.transport(response)).query(self.context)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(c["status"] == "CANDIDATE" and not c["score_is_probability"] for c in candidates))

    def test_echoed_credential_rejected_without_persistence(self):
        with self.assertRaises(AVError):
            strict_response(b'{"echo":"generated-test-client-key"}', "generated-test-client-key")

    def test_json_escaped_credential_echo_rejected(self):
        key = "generated-test-client-key"
        escaped = "".join("\\u%04x" % ord(char) for char in key)
        with self.assertRaises(AVError):
            strict_response(('{"results":[{"title":"' + escaped + '"}]}').encode(), key)
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": key}):
            receipt, candidates = AcoustIDBackend(["acoustid"], transport=lambda *a:
                ('{"status":"ok","results":[{"recordings":[{"title":"' + escaped + '"}]}]}').encode()).query(self.context)
        self.assertEqual(receipt["status"], "UNRESOLVED")
        self.assertFalse(candidates)
        self.assertNotIn(key, json.dumps(receipt))

    def test_excessive_json_depth_rejected_safely(self):
        raw = ('{"nested":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode()
        with self.assertRaises(AVError):
            strict_response(raw)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(AVError):
            strict_response(b'{"status":"ok","status":"error"}')

    def test_bad_results_fail_safely(self):
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": "generated-test-client-key"}):
            receipt, candidates = AcoustIDBackend(["acoustid"], transport=self.transport({"status": "ok", "results": "bad"})).query(self.context)
        self.assertEqual(receipt["status"], "UNRESOLVED")
        self.assertFalse(candidates)
        self.assertIn("response_sha256", receipt)

    def test_musicbrainz_separate_authorization(self):
        with self.assertRaises(AVError):
            MusicBrainzNormalizer(["acoustid"], transport=self.transport(cat())).query(RID1, SCOPE)
        self.assertFalse(self.calls)

    def test_cached_musicbrainz_no_second_request(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = MusicBrainzNormalizer(["musicbrainz"], temp, self.transport(cat(releases=(REL1, REL2))))
            q1, c1 = backend.query(RID1, SCOPE)
            q2, c2 = backend.query(RID1, SCOPE)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(q2["external_request_performed"])
        self.assertEqual(c1, c2)
        self.assertEqual(len(q1["normalized_metadata"][0]["releases"]), 2)

    def test_recording_uuid_mismatch_rejected(self):
        with self.assertRaises(AVError):
            normalize_musicbrainz(cat(RID2), RID1)

    def test_missing_work_stays_missing(self):
        normalized = normalize_musicbrainz(cat(works=()), RID1)
        self.assertEqual(normalized["works"], [])

    def test_explicit_cover_work_relation_retained(self):
        n = normalize_musicbrainz(cat(), RID1)
        self.assertEqual(n["works"][0]["attributes"], ["cover"])

    def test_provider_title_conflicts_preserved(self):
        q1, a = MusicBrainzNormalizer(["musicbrainz"], transport=self.transport(cat(title="First title"))).query(RID1, SCOPE)
        q2, b = MusicBrainzNormalizer(["musicbrainz"], transport=self.transport(cat(RID2, title="Other title"))).query(RID2, SCOPE)
        self.assertNotEqual(a[0]["title"], b[0]["title"])
        self.assertNotEqual(q1["query_id"], q2["query_id"])

    def test_transport_failure_retains_unresolved_receipt(self):
        def fail(*args):
            raise AVError("Provider failed")
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": "generated-test-client-key"}):
            q, candidates = AcoustIDBackend(["acoustid"], transport=fail).query(self.context)
        self.assertEqual(q["status"], "UNRESOLVED")
        self.assertFalse(candidates)
        self.assertNotIn("generated-test-client-key", json.dumps(q))

    def test_bad_musicbrainz_cache_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / (RID1 + ".json")
            write_json(path, {"schema": "ave.musicbrainz.cache.v1", "recording_id": RID1, "response": cat(),
                "retrieved_at": "2026-10-07T00:00:00+00:00", "response_sha256": digest("different")})
            with self.assertRaises(AVError):
                MusicBrainzNormalizer(["musicbrainz"], temp, self.transport(cat())).query(RID1, SCOPE)
        self.assertFalse(self.calls)

    def test_musicbrainz_failed_requests_still_rate_limited(self):
        for response in (AVError("Transport failed"), b"malformed-json"):
            with self.subTest(response=type(response).__name__):
                transport_patch = patch("avevidence.music_identity.backends.http_transport")
                with transport_patch as send, patch("avevidence.music_identity.backends.time.monotonic",
                    side_effect=[10., 10.1, 10.2, 10.3]), patch("avevidence.music_identity.backends.time.sleep") as pause:
                    if isinstance(response, Exception):
                        send.side_effect = response
                    else:
                        send.return_value = response
                    backend = MusicBrainzNormalizer(["musicbrainz"])
                    first, _ = backend.query(RID1, SCOPE)
                    second, _ = backend.query(RID2, SCOPE)
                    self.assertEqual(first["status"], "UNRESOLVED")
                    self.assertEqual(second["status"], "UNRESOLVED")
                    self.assertEqual(send.call_count, 2)
                    self.assertAlmostEqual(pause.call_args_list[1].args[0], 1.)


class FamilyTests(unittest.TestCase):
    def test_multiple_covers_same_work_family_recording_unresolved(self):
        families = group_candidates([c(), c(RID2)])
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0]["recording_identity_status"], "UNRESOLVED")
        self.assertEqual(families[0]["status"], "CANDIDATE")
        self.assertEqual(families[0]["score_aggregation"], "NONE")

    def test_same_title_unrelated_works_stay_separate(self):
        self.assertEqual(len(group_candidates([c(), c(RID2, works=(WID2,))])), 2)

    def test_medley_multiple_scoped_work_families(self):
        families = group_candidates([c(works=(WID1, WID2))])
        self.assertEqual(len(families), 2)
        self.assertTrue(all(f["composition_scope"] == "PARTIAL_OR_MEDLEY" and f["proposed_composition_status"] == "CANDIDATE" for f in families))

    def test_weak_title_only_never_supported(self):
        families = group_candidates([c(works=()), c(RID2, works=())])
        self.assertEqual(families[0]["basis"], "WEAK_TITLE_HEURISTIC")
        self.assertEqual(families[0]["proposed_composition_status"], "CANDIDATE")

    def test_exact_and_cover_family_not_recording_votes(self):
        families = group_candidates([c(), c(RID2)])
        self.assertEqual(len(families[0]["candidate_ids"]), 2)
        self.assertEqual(families[0]["recording_identity_status"], "UNRESOLVED")

    def test_no_title_no_work_remains_isolated(self):
        self.assertEqual(group_candidates([c(works=(), title=None)])[0]["basis"], "UNLINKED_CANDIDATE")

    def test_embedded_work_tag_does_not_create_strong_family(self):
        family = group_candidates([c(provider="embedded-metadata", basis="METADATA_DECLARED")])[0]
        self.assertNotEqual(family["basis"], "SHARED_MUSICBRAINZ_WORK")
        self.assertEqual(family["proposed_composition_status"], "CANDIDATE")

    def test_catalogue_recording_link_joins_original_lookup_candidate(self):
        lookup = c(provider="acoustid", basis="DERIVED_FINGERPRINT_CANDIDATE", works=())
        families = group_candidates([lookup, c()])
        self.assertEqual(len(families), 1)
        self.assertIn(lookup["candidate_id"], families[0]["candidate_ids"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe required")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory(prefix="ave-music-generated-")
        self.root = Path(self.temp.name)
        self.source = self.root / "generated-source.wav"
        sr = 8000
        t = np.arange(sr * 6) / sr
        rng = np.random.default_rng(221)
        self.samples = .13 * np.sin(2 * np.pi * (220 * t + 45 * t ** 2)) + .07 * np.sin(2 * np.pi * 734 * t) + rng.normal(0, .03, len(t))
        self.wav(self.source, self.samples)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def wav(path, x):
        import numpy as np
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(8000)
            target.writeframes(np.rint(x * 32767).astype("<i2").tobytes())

    def initial(self, name="identity", **options):
        folder = self.root / name
        identify(self.source, folder, start=0, end=4, **options)
        return folder, read_json(folder / "music-identity.json")

    def review(self, folder, raw, level="COMPOSITION", status="SUPPORTED", kinds=("SOURCE_VISIBLE_CREDIT", "OFFICIAL_CREDIT"), **extra):
        evidence = []
        for i, kind in enumerate(kinds):
            path = self.root / ("generated-credit-" + str(i) + ".json")
            if not path.exists():
                write_json(path, {"credit": "Generated music attribution", "evidence_class": kind, "synthetic_contract_only": True})
            evidence.append({"evidence_id": "credit-" + str(i), "kind": kind, "artifact": {"path": path.name, "sha256": sha256(path)},
                "source": raw["source"], "level": level, "candidate_ids": ["declared-song"], "actual_inspection_declared": True,
                "statement": "Generated source/official credit contract; not a real inspection", **extra})
        config = self.root / "review.json"
        write_json(config, {"schema": "ave.music-identity.review.v1", "reviewer": "Generated test reviewer", "source": raw["source"],
            "declared_candidates": [{"label": "declared-song", "level": level, "title": "Generated composition"}],
            "evidence": evidence, "decisions": [{"level": level, "status": status, "candidate_ids": ["declared-song"],
                "evidence_ids": [e["evidence_id"] for e in evidence], "reason": "Generated source and credit agreement"}]})
        output = self.root / "reviewed"
        adjudicate(folder, config, output)
        return output, read_json(output / "music-identity.json")

    def test_default_run_network_free_all_levels_unresolved(self):
        with patch("avevidence.music_identity.backends.http_transport", side_effect=AssertionError("Network forbidden")):
            folder, raw = self.initial()
        self.assertEqual(raw["network_activity"], {"external_requests": 0, "media_uploads": 0, "paid_calls": 0})
        self.assertTrue(all(d["status"] == "UNRESOLVED" for d in raw["adjudication"]))
        self.assertEqual(load_music_run(folder)[1], raw)

    def test_explicit_opt_in_required_before_probe(self):
        with patch("avevidence.music_identity.workflow.probe_source", side_effect=AssertionError("Must not probe")), self.assertRaises(AVError):
            identify(self.source, self.root / "blocked", providers=["acoustid"])

    def test_long_interval_needs_refinement(self):
        with patch("avevidence.music_identity.workflow.probe_source", return_value={**probe_source(self.source), "duration_seconds": 300.}), self.assertRaises(AVError):
            identify(self.source, self.root / "long", start=0, end=121)

    def test_ambiguous_selected_stream_is_refused(self):
        probe = probe_source(self.source)
        probe["streams"].append({**probe["streams"][0], "index": 1})
        with patch("avevidence.music_identity.workflow.probe_source", return_value=probe), self.assertRaises(AVError):
            identify(self.source, self.root / "ambiguous")

    def test_missing_fpcalc_still_yields_metadata_and_no_lookup(self):
        with patch("avevidence.music_identity.fingerprint.calculate", side_effect=AVError("Optional Chromaprint unavailable")):
            folder, raw = self.initial(fingerprint=True, providers=["acoustid"], allow_external_lookup=["acoustid"])
        self.assertEqual(raw["network_activity"]["external_requests"], 0)
        self.assertEqual(raw["queries"][-1]["status"], "NOT_TESTED")

    def test_mock_external_lookup_and_no_secret_in_run(self):
        fake = {**parse_fpcalc('{"duration":4,"fingerprint":"abcDEF_123456"}', 4.), "implementation": {}, "command": [], "witness_sha256": digest("witness")}
        with patch.dict(os.environ, {"ACOUSTID_API_KEY": "generated-test-client-key"}), patch("avevidence.music_identity.fingerprint.calculate", return_value=fake):
            folder, raw = self.initial(providers=["acoustid"], allow_external_lookup=["acoustid"],
                transport=lambda *a: b'{"status":"ok","results":[]}')
        self.assertEqual(raw["network_activity"]["external_requests"], 0)
        self.assertTrue(raw["queries"][-1]["test_double"])
        for p in folder.rglob("*.json"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("generated-test-client-key", text)
            self.assertNotIn("input_audio", text)

    def test_source_credit_supports_composition_without_recording_match(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        self.assertEqual(result["adjudication"][3]["status"], "SUPPORTED")
        self.assertEqual(result["adjudication"][0]["status"], "UNRESOLVED")
        self.assertEqual(read_json(folder / "music-identity.json"), raw)

    def test_composition_credit_cannot_establish_master_recording(self):
        folder, raw = self.initial()
        with self.assertRaises(AVError):
            self.review(folder, raw, level="RECORDING")

    def test_duplicate_credit_artifact_not_independent_support(self):
        folder, raw = self.initial()
        from avevidence.music_identity.review import decision_support
        reviewed, result = self.review(folder, raw)
        result["evidence"][1]["artifact"]["sha256"] = result["evidence"][0]["artifact"]["sha256"]
        self.assertFalse(decision_support(result, result["adjudication"][3])[0])

    def test_namespaced_evidence_id_uses_portable_snapshot_filename(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw)
        config = read_json(self.root / "review.json")
        for e in config["evidence"]:
            e["evidence_id"] = "namespaced:" + e["evidence_id"]
        for d in config["decisions"]:
            d["evidence_ids"] = ["namespaced:" + eid for eid in d["evidence_ids"]]
        next_config = self.root / "namespaced-review.json"
        write_json(next_config, config)
        output = self.root / "namespaced-reviewed"
        adjudicate(folder, next_config, output)
        from avevidence.music_identity.workflow import load_music_run
        _, final = load_music_run(output)
        self.assertTrue(all(":" not in e["artifact"]["path"] for e in final["evidence"]))

    def test_model_or_catalogue_guess_cannot_adjudicate_without_evidence(self):
        folder, raw = self.initial()
        with self.assertRaises(AVError):
            self.review(folder, raw, kinds=())

    def test_arrangement_support_needs_scoped_audio_comparison(self):
        folder, raw = self.initial()
        reviewed, result = self.review(folder, raw, level="ARRANGEMENT", kinds=("SCOPED_IDENTITY_REVIEW",),
            actual_audio_inspection_declared=True, comparison_basis="Generated source/reference phrase comparison")
        self.assertEqual(result["adjudication"][2]["status"], "SUPPORTED")

    def test_performance_identity_requires_separate_basis(self):
        folder, raw = self.initial()
        with self.assertRaises(AVError):
            self.review(folder, raw, level="PERFORMANCE", kinds=("SCOPED_IDENTITY_REVIEW",), actual_audio_inspection_declared=True)

    def test_portable_no_absolute_paths_valid_rebinding(self):
        folder, raw = self.initial()
        portable = self.root / "portable"
        export_portable(folder, portable)
        for path in portable.rglob("*.json"):
            self.assertNotIn(str(self.root), path.read_text(encoding="utf-8"))
            self.assertNotIn("generated-source.wav", path.read_text(encoding="utf-8"))
        self.assertEqual(verify_portable(portable)[0]["source"], raw["source"])
        rebound = self.root / "rebound"
        rebind(portable, self.source, rebound)
        self.assertEqual(load_music_run(rebound)[1]["source"], raw["source"])

    def test_rebinding_wrong_source_refused(self):
        folder, _ = self.initial()
        portable = self.root / "portable"
        export_portable(folder, portable)
        wrong = self.root / "different.wav"
        self.wav(wrong, self.samples * .5)
        with self.assertRaises(AVError):
            rebind(portable, wrong, self.root / "rebound")

    def test_portable_tamper_detected(self):
        folder, _ = self.initial()
        portable = self.root / "portable"
        export_portable(folder, portable)
        (portable / "music-identity.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(AVError):
            verify_portable(portable)

    def test_export_private_metadata_fails_closed(self):
        for value in ({"title": "C:/SyntheticPrivate/private.wav"}, {"input_audio": {"data": "payload"}},
                      {"client_api_key": "generated"}, {"text": "data:" + "audio/wav;base64," + "AAAA"}):
            with self.subTest(value=value), self.assertRaises(AVError):
                assert_portable(value)

    def test_neighbor_plan_no_upload_and_source_bound(self):
        folder, raw = self.initial()
        output = self.root / "plan"
        plan(folder, output, padding_seconds=1)
        result = read_json(output / "music-plan.json")
        self.assertFalse(result["api_submission_authorized"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["requests"][0]["source"]["interval_seconds"], [0., 5.])

    def test_new_run_never_overwrites_existing(self):
        folder, _ = self.initial()
        with self.assertRaises(AVError):
            identify(self.source, folder, start=0, end=4)

    def test_query_scope_tamper_rejected(self):
        folder, raw = self.initial()
        raw["queries"][0]["source"]["audio_stream"] = 2
        # Dictionaries shared in-memory are not accepted from serialized artifact tampering.
        raw["source"]["audio_stream"] = 0
        with self.assertRaises(AVError):
            validate_result(raw)

    def test_candidate_cannot_be_automatically_promoted(self):
        folder, raw = self.initial()
        query = raw["queries"][0]
        row = candidate(provider="embedded-metadata", title="Generated", basis="METADATA_DECLARED",
                        query_id=query["query_id"], interval=raw["source"]["interval_seconds"])
        query["candidate_ids"] = [row["candidate_id"]]
        raw["candidates"] = [row]
        row["status"] = "SUPPORTED"
        with self.assertRaises(AVError):
            validate_result(raw)

    def test_fingerprint_binding_mismatch_rejected(self):
        folder, raw = self.initial()
        query = raw["queries"][0]
        row = candidate(provider="embedded-metadata", title="Generated", basis="METADATA_DECLARED",
            query_id=query["query_id"], interval=raw["source"]["interval_seconds"], fingerprint_sha256=digest("wrong"))
        query["candidate_ids"] = [row["candidate_id"]]
        raw["candidates"] = [row]
        with self.assertRaises(AVError):
            validate_result(raw)

    def auditory_fixture(self):
        from avevidence.common import file_record
        probe = probe_source(self.source)
        clip = self.root / "generated-auditory-clip.wav"
        self.wav(clip, self.samples[:32000])
        observation_run = self.root / "generated-auditory-contract"
        with output_transaction(observation_run, [self.source]) as stage:
            write_json(stage / "observation.json", {"schema": "ave.auditory-observation.v1", "stage": 1,
                "source_sha256": probe["sha256"], "stream_index": 0, "source_interval_seconds": [0., 4.], "clip_sha256": sha256(clip),
                "validation_status": "VALID", "observer_type": "model_audio", "task_profile": "MUSIC_STRUCTURE",
                "backend_identity": {"model_revision": "generated-contract-fixture"},
                "observation": "Generated fixture: this resembles Beat It", "scope": "Synthetic attribution contract; no actual observer or perception"})
            finish_run(stage, "auditory-observe", [probe, file_record(clip)], metadata={"result_file": "observation.json"})
        guesses = self.root / "guesses.json"
        write_json(guesses, {"observation_run": observation_run.name, "candidates": [
            {"title": "Beat It", "artist": None, "level": "COMPOSITION", "record_pointer": "/observation"}]})
        return observation_run, guesses

    def test_auditory_name_remains_candidate_catalogue_unresolved(self):
        observation_run, guesses = self.auditory_fixture()
        before = sha256(observation_run / "observation.json")
        folder, raw = self.initial(auditory_candidates=guesses)
        c = raw["candidates"][0]
        self.assertEqual(c["lookup_basis"], "AUDITORY_IDENTITY_CANDIDATE")
        self.assertEqual(c["status"], "CANDIDATE")
        self.assertEqual(c["title"], "Beat It")
        self.assertIsNone(c["musicbrainz_recording_id"])
        self.assertEqual(raw["adjudication"][3]["status"], "UNRESOLVED")
        self.assertEqual(sha256(observation_run / "observation.json"), before)

    def test_auditory_title_not_in_raw_response_rejected(self):
        observation_run, guesses = self.auditory_fixture()
        data = read_json(guesses)
        data["candidates"][0]["title"] = "Unmentioned song"
        edited = self.root / "edited-guesses.json"
        write_json(edited, data)
        with self.assertRaises(AVError):
            self.initial(auditory_candidates=edited)
