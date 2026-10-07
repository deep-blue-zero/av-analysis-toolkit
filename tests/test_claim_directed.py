"""Adversarial evidence gates and real media integration for claim-directed analysis."""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

from avevidence.common import AVError, read_json, sha256, write_json


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifact = self.root/"witness.txt"
        self.artifact.write_text("Observed source-bound evidence", encoding="utf-8")
        self.hash = sha256(self.artifact)
        self.loc = {"source_sha256": self.hash, "clock": "original_pts_minus_source_origin", "stream_index": 0, "intervals_seconds": [[1,2]]}
        self.evidence = {"id": "e1", "layer": "measured", "modality": "audio", "locator": self.loc,
            "statement": "The mixed-signal energy is below the declared threshold.", "quality_flags": ["SUBTITLE_BOUNDARY_ONLY"],
            "artifact": {"path": str(self.artifact), "sha256": self.hash}, "boundary_origin": "subtitle_boundary"}
        self.claim = {"id": "c1", "statement": "The soundtrack has a quiet region.", "kind": "mixed_energy",
            "required_modalities": ["audio"], "locators": [self.loc], "evidence_ids": ["e1"], "counterevidence_ids": [],
            "inference": None, "interpretation": None, "alternatives": ["Quiet ambient sound remains."], "confidence": "medium"}

    def tearDown(self): self.temp.cleanup()

    def validate(self, **changes):
        from avevidence.evidence_claims import validate_claims
        claim = dict(self.claim, **changes)
        return validate_claims({"schema": "ave.claims.v1", "claims": [claim], "evidence": [self.evidence]}, self.root)[0][0]

    def test_computed_result_provisional_without_adjudication(self):
        result = self.validate()
        self.assertEqual(result["status"], "PROVISIONAL")
        self.assertEqual(result["blocking_flags"], [])

    def test_subtitle_gap_cannot_be_promoted_to_speech_latency(self):
        result = self.validate(kind="speech_latency")
        self.assertEqual(result["status"], "OPEN")
        with self.assertRaises(AVError):
            self.validate(kind="speech_latency", adjudication={"status": "SUPPORTED", "reviewer": "test-reviewer", "reason": "subtitle arithmetic"})

    def test_measurement_cannot_establish_emotional_delivery(self):
        self.assertEqual(self.validate(kind="delivery")["status"], "OPEN")

    def test_stills_cannot_establish_motion(self):
        self.evidence.update(layer="observed", modality="visual_stills", reviewer="test-reviewer", inspection_status="REVIEW_DECLARED")
        self.evidence["locator"] = {k:v for k,v in self.loc.items() if k != "intervals_seconds"}
        self.evidence["locator"]["points_seconds"] = [1.2]
        result = self.validate(kind="motion", required_modalities=["motion"])
        self.assertIn("MOTION_NOT_VERIFIED", result["blocking_flags"])
        self.assertEqual(result["status"], "OPEN")

    def test_interpretation_does_not_count_as_direct_evidence(self):
        self.evidence["layer"] = "interpretation"
        self.assertEqual(self.validate()["missing_modalities"], ["audio"])

    def test_mismatched_source_and_changed_artifact_refused(self):
        original = self.evidence["locator"]
        self.evidence["locator"] = dict(original, source_sha256=sha256(__file__))
        with self.assertRaises(AVError): self.validate()
        self.evidence["locator"] = original
        self.artifact.write_text("changed", encoding="utf-8")
        with self.assertRaises(AVError): self.validate()

    def test_missing_separate_interpretation_field_refused(self):
        del self.claim["interpretation"]
        with self.assertRaises(AVError): self.validate()

    def test_conflicting_modalities_do_not_silently_close(self):
        self.assertEqual(self.validate(required_modalities=["audio", "motion"])["status"], "OPEN")

    def test_unknown_flag_refused(self):
        with self.assertRaises(AVError): self.validate(quality_flags=["EMOTION_CERTIFIED"])

    def test_mixed_pitch_cannot_become_character_f0_without_isolation(self):
        self.evidence["quality_flags"]=[]
        result=self.validate(kind="pitch")
        self.assertIn("SPEAKER_ISOLATION_NOT_ESTABLISHED",result["blocking_flags"])

    def test_still_evidence_cannot_verify_an_edit_cut(self):
        result=self.validate(kind="editing")
        self.assertEqual(result["status"],"OPEN")

    def test_mora_and_timing_require_actual_reading_and_boundary_authority(self):
        from avevidence.speech_segments import mora_count,timing_comparison
        self.assertEqual(mora_count("きょう がっこう"),6)
        with self.assertRaises(AVError): mora_count("今日学校")
        result=timing_comparison({"end_s":1.,"boundary_origin":"subtitle_boundary"},{"start_s":2.,"boundary_origin":"subtitle_boundary"})
        self.assertIsNone(result["speech_response_latency_s"])
        self.assertEqual(result["boundary_interval_s"],1.)

    def test_capability_acceptance_is_not_perception(self):
        from avevidence.analysis_preflight import capability_receipt
        receipt = {"interface": "fixture", "model_revision": "1", "adapter_revision": "1", "status": "REQUEST_ACCEPTED"}
        self.assertEqual(capability_receipt(receipt)["status"], "REQUEST_ACCEPTED")
        with self.assertRaises(AVError): capability_receipt(dict(receipt, status="PROBE_PASSED"))

    def test_probe_above_chance_is_bounded_and_not_emotion_validation(self):
        from avevidence.auditory_observer import score_probe
        answers = [0,1]*12
        self.assertTrue(score_probe(answers,answers)["passed"])
        self.assertFalse(score_probe([0]*24,answers)["passed"])
        with self.assertRaises(AVError): score_probe([0]*12,[0]*12)

    def test_observer_malformed_json_not_repaired(self):
        from avevidence.auditory_observer import validate_response
        with self.assertRaises(AVError): validate_response("```json\n{}\n```", 1.)
        with self.assertRaises(AVError): validate_response('{"observations":[],"confidence":"high"}', 1.)
        with self.assertRaises(AVError): validate_response(json.dumps({"observations": [{"start_s":0,"end_s":2,"description":"outside"}],
            "confidence":"high","alternatives":[],"abstentions":[],"interference":[]}), 1.)

    def test_source_relocation_requires_allowed_root_and_hash(self):
        from avevidence.analysis_preflight import resolve_source
        self.assertEqual(resolve_source(self.hash,{self.hash:str(self.artifact)},allowed_roots=[self.root]),self.artifact)
        with self.assertRaises(AVError): resolve_source(self.hash,{self.hash:str(self.artifact)},allowed_roots=[self.root/"unrelated"])

    def test_musical_nonmatch_cannot_merge_baseline_identity(self):
        from avevidence.musical_relations import musical_relations
        row = {"id":"r1","a":self.loc,"b":self.loc,"relation":"LYRIC_RECURRENCE","status":"SUPPORTED",
            "reviewer":"reviewer","reason":"Same written lyric.","evidence":[self.evidence["artifact"]]}
        config = self.root/"relations.json"; write_json(config,{"relations":[row]})
        musical_relations(config,self.root/"accepted")
        self.assertEqual(read_json(self.root/"accepted"/"musical-relations.json")["relations"][0]["baseline_identity_effect"],"NONE")
        row["relation"] = "SAME_RECORDED_PERFORMANCE"
        config.write_text(json.dumps({"relations":[row]}),encoding="utf-8")
        with self.assertRaises(AVError): musical_relations(config,self.root/"rejected")

    def test_recording_relation_requires_exact_source_spans_even_with_accepted_edge(self):
        from avevidence.musical_relations import musical_relations
        from avevidence.common import output_transaction,finish_run
        folder=self.root/"reuse-graph"
        edge={"match_id":"match-1","whole_unit_equivalence":True,"identity_decision":"same_performance",
            "a":{"source_sha256":self.hash,"source_start_s":1,"source_end_s":2,"sample_rate_hz":16000},
            "b":{"source_sha256":self.hash,"source_start_s":3,"source_end_s":4,"sample_rate_hz":16000}}
        with output_transaction(folder) as stage:
            write_json(stage/"graph.json",{"schema":"ave.reuse.graph.v1","matches":[edge]})
            finish_run(stage,"reuse-graph",[])
        b=dict(self.loc,intervals_seconds=[[3,4]])
        row={"id":"r1","a":self.loc,"b":b,"relation":"SAME_RECORDED_PERFORMANCE","status":"SUPPORTED",
            "reviewer":"reviewer","reason":"Accepted exact reuse edge.","reuse_match_id":"match-1",
            "evidence":[{"path":str(folder/"graph.json"),"sha256":sha256(folder/"graph.json")}]}
        config=self.root/"recording-relation.json";write_json(config,{"relations":[row]})
        musical_relations(config,self.root/"relation-accepted")
        row["b"]=dict(b,intervals_seconds=[[5,6]])
        config.write_text(json.dumps({"relations":[row]}),encoding="utf-8")
        with self.assertRaises(AVError): musical_relations(config,self.root/"wrong-fragment")

    def test_blind_export_does_not_forward_original_context(self):
        from avevidence.analysis_benchmark import blind_export
        config = self.root/"blind.json"
        write_json(config,{"future_members":5,"title":"Known title", "prior_analysis":"Expected thesis",
            "utterances":[{"cue_id":1,"speaker":"Kanon","text":"Kanon speaks","start_s":0,"end_s":1}]})
        blind_export(config,self.root/"blind")
        text = (self.root/"blind"/"analyst-context"/"source-001.json").read_text(encoding="utf-8")
        self.assertNotIn("Kanon",text); self.assertNotIn("Known title",text); self.assertNotIn("Expected thesis",text)

    def test_baselines_count_an_underlying_performance_once(self):
        from avevidence.vocal_baselines import build_baselines
        db=self.root/"reuse.sqlite";db.write_bytes(b"mocked snapshot input")
        snapshot={"revision":2,"performances":[{"performance_id":"p1","occurrence_ids":["o1","o2"]}],
            "occurrences":[{"occurrence_id":o,"vocal_status":"verified_voice","speaker":"Person-1",
                "quality":"isolated_voice_asset","unit_kind":"performance"} for o in ("o1","o2")]}
        row={"segment_type":"non_singing_dialogue","quality_flags":[],"quality_reviewed":True,
            "measurement_artifact":self.evidence["artifact"],"features":{"f0_median_hz":220}}
        config=self.root/"baseline-config.json"
        write_json(config,{"rows":[dict(row,occurrence_id=o) for o in ("o1","o2")]})
        with patch("avevidence.reuse.graph.snapshot",return_value=snapshot):
            build_baselines(config,db,self.root/"baseline",minimum=3)
        report=read_json(self.root/"baseline"/"baselines.json")
        self.assertEqual(report["baselines"][0]["independent_performances"],1)
        self.assertEqual(report["baselines"][0]["features"]["f0_median_hz"]["status"],"OPEN")
        self.assertEqual(report["excluded"][0]["reason"],"DUPLICATE_RECORDED_PERFORMANCE")

    def test_ablation_excludes_prior_verdicts_and_respects_layers(self):
        from avevidence.analysis_benchmark import modality_ablation
        config = self.root/"claims.json"
        write_json(config,{"schema":"ave.claims.v1","claims":[self.claim],"evidence":[self.evidence]})
        modality_ablation(config,self.root/"ablation")
        self.assertEqual(read_json(self.root/"ablation"/"A_text"/"input.json")["evidence"],[])
        later = read_json(self.root/"ablation"/"C_plus_signal"/"input.json")
        self.assertEqual(len(later["evidence"]),1)
        self.assertNotIn("interpretation",later["questions"][0])


class Queries(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.meta = {"clock":"original_pts_minus_source_origin","sample_rate_hz":1000,"configuration":{"hop_ms":10,"floor":75,"ceiling":600},
            "segments":[{"sample_start":0,"sample_end":1000,"source_start_seconds":0,"source_end_seconds":1}]}
        self.audio = {"identity":{"sample_rate_hz":1000},"source_sha256":sha256(__file__),"stream_index":0,"transform":{"channel":"mean"}}
        centers = np.arange(20,981,10)
        self.tracks = {"time_s":centers/1000,"sample_start":centers-20,"sample_end":centers+20,"segment_id":np.zeros(len(centers),dtype=int),
            "rms_dbfs":np.full(len(centers),-60.),"pitch_hz":np.full(len(centers),220.),"pitch_strength":np.full(len(centers),.9)}

    def query(self, a=.2,b=.4,**kwargs):
        from avevidence.evidence_queries import query_tracks
        return query_tracks(self.tracks,self.meta,self.audio,a,b,**kwargs)

    def test_center_and_contained_window_policies_differ(self):
        centers = self.query()
        contained = self.query(selection="contained_rms_windows")
        self.assertGreater(centers["contour_rows"],contained["contour_rows"])
        self.assertEqual(centers["energy"]["fraction_below_threshold"],1.)

    def test_gap_and_outside_coverage_are_refused(self):
        with self.assertRaises(AVError): self.query(b=1.1)
        self.meta["segments"][0]["source_end_seconds"] = .3
        with self.assertRaises(AVError): self.query()

    def test_octave_jump_and_music_like_f0_do_not_enter_baseline(self):
        self.tracks["pitch_hz"][25:] = 440
        row = self.query(a=.1,b=.9)
        self.assertIn("F0_DISCONTINUITY",row["quality_flags"])
        self.assertFalse(row["speaker_baseline_eligible"])

    def test_unvoiced_pitch_is_missing_not_zero_hz(self):
        self.tracks["pitch_hz"][:] = float("nan")
        row = self.query()
        self.assertIsNone(row["pitch"]["median_hz"])
        self.assertIn("LOW_F0_CONFIDENCE",row["quality_flags"])

    def test_subtitle_boundary_not_given_phonetic_accuracy(self):
        row = self.query(boundary_origin="subtitle_boundary")
        self.assertIn("SUBTITLE_BOUNDARY_ONLY",row["quality_flags"])
        self.assertIsNone(row["boundary_accuracy_s"])

    def test_ordered_chroma_distinguishes_pooling_from_correspondence(self):
        import numpy as np
        from avevidence.musical_relations import ordered_chroma_candidate
        a = np.eye(12)
        same = ordered_chroma_candidate(a,a)
        shuffled = ordered_chroma_candidate(a,a[::-1])
        self.assertLess(same["ordered_dtw_mean_cosine_distance"],shuffled["ordered_dtw_mean_cosine_distance"])
        self.assertEqual(same["recording_identity_effect"],"NONE")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg required")
class NativeMedia(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wav = self.root/"source.wav"
        t = np.arange(32000)/16000
        tone = (.2*np.sin(2*np.pi*220*t)*32767).astype("<i2")
        signal = np.stack([tone,-tone],axis=1)
        with wave.open(str(self.wav),"wb") as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(16000); out.writeframes(signal.tobytes())

    def tearDown(self): self.temp.cleanup()

    def test_audio_only_preflight_no_visual_failure(self):
        from avevidence.analysis_preflight import preflight
        preflight(self.wav,self.root/"preflight")
        row = read_json(self.root/"preflight"/"capabilities.json")
        self.assertFalse(row["modalities"]["video"])
        self.assertEqual(row["capabilities"]["visual_extraction"]["status"],"NOT_PRESENT")
        self.assertEqual(row["capabilities"]["audio_decodable"]["status"],"PROBE_PASSED")
        self.assertEqual(row["capabilities"]["direct_audio_perception"]["status"],"NOT_TESTED")

    def test_unknown_duration_recovery_is_opt_in_bounded_and_audio_only(self):
        from avevidence.common import probe_source
        from avevidence.performance import performance
        source = probe_source(self.wav)
        source["duration_seconds"] = None; source["duration_basis"] = "UNAVAILABLE"
        with patch("avevidence.performance.probe_source",return_value=source):
            with self.assertRaises(AVError): performance(self.wav,self.root/"refused",cache_dir=self.root/"cache",progress=False)
            with self.assertRaises(AVError): performance(self.wav,self.root/"unbounded",cache_dir=self.root/"cache",coverage_mode="decoded",progress=False)
            performance(self.wav,self.root/"recovered",cache_dir=self.root/"cache",coverage_mode="decoded",coverage_end=1.,channel="left",progress=False)
        report = read_json(self.root/"recovered"/"performance.json")
        self.assertIsNone(report["coverage"]["original_duration_seconds"])
        self.assertLessEqual(report["coverage"]["intervals_seconds"][0][1],1.)
        self.assertEqual(report["cues"],[])

    def test_native_channel_preservation_and_antiphase_warning(self):
        from avevidence.audio_witness import audio_witness
        audio_witness(self.wav,self.root/"preserve",start=.2,end=.8)
        row = read_json(self.root/"preserve"/"witness.json")
        self.assertEqual(row["identity"]["channels"],2)
        self.assertEqual(row["identity"]["sample_rate_hz"],16000)
        self.assertTrue(row["channel_inspection"]["cancellation_warning"])
        audio_witness(self.wav,self.root/"left",start=.2,end=.8,channel="left")
        self.assertEqual(read_json(self.root/"left"/"witness.json")["identity"]["channels"],1)

    def test_decoded_byte_budget_rejects_truncation(self):
        from avevidence.audio import _decode
        from avevidence.common import probe_source,select_stream
        source = probe_source(self.wav)
        with self.assertRaises(AVError): _decode(source,select_stream(source,"audio"),self.root,"bounded",max_output_bytes=5000)

    def test_normalization_retains_original_event_identity_and_blank_ledger(self):
        from avevidence.subtitles import parse_subtitles
        ass = self.root/"raw.ass"
        ass.write_text("[Events]\nFormat: Layer, Start, End, Style, Name, Text\n"
            "Dialogue: 0,0:00:00.10,0:00:00.30,Default,,\n"
            "Dialogue: 0,0:00:00.15,0:00:00.40,Default,,hello\n"
            "Dialogue: 0,bad,0:00:00.60,Default,,malformed\n"
            "Dialogue: 0,0:00:00.30,0:00:00.80,Default,,preview\n",encoding="utf-8")
        original = sha256(ass)
        parse_subtitles(ass,self.root/"normalized",normalize=True,exclude_cue_ids=[4])
        ledger = read_json(self.root/"normalized"/"normalization.json")
        self.assertEqual(ledger["raw_event_count"],4)
        self.assertEqual(ledger["excluded_count"],3)
        self.assertEqual(read_json(self.root/"normalized"/"subtitles.json")["rows"][0]["cue_index"],2)
        self.assertEqual(sha256(ass),original)
        self.assertEqual((self.root/"normalized"/"original.ass").read_bytes(),ass.read_bytes())

    def test_mock_observer_never_becomes_auditory_evidence(self):
        from avevidence.audio_witness import audio_witness
        from avevidence.auditory_observer import observe_clip
        audio_witness(self.wav,self.root/"witness",start=.2,end=.8)
        request = self.root/"request.json"
        write_json(request,{"request_id":"r1","question":"Describe the sound.","source_sha256":sha256(self.wav)})
        observe_clip(self.root/"witness",request,self.root/"mock")
        report = read_json(self.root/"mock"/"observation.json")
        self.assertEqual(report["observer_type"],"mock")
        self.assertEqual(report["capability_status"],"NOT_CONFIGURED")
        self.assertEqual(report["parsed"]["observations"],[])
        leak = read_json(request); leak["expected_emotion"] = "fear"
        request.write_text(json.dumps(leak),encoding="utf-8")
        with self.assertRaises(AVError): observe_clip(self.root/"witness",request,self.root/"leaked")

    def test_corrupted_cached_query_is_refused(self):
        from avevidence.performance import performance
        from avevidence.evidence_queries import contour_query
        performance(self.wav,self.root/"run",cache_dir=self.root/"cache",channel="right",progress=False)
        contour_query(self.root/"run",self.root/"query",start=.2,end=.8)
        (self.root/"run"/"contours.npz").write_bytes(b"bad")
        with self.assertRaises(AVError): contour_query(self.root/"run",self.root/"bad-query",start=.2,end=.8)

    def test_blinded_probe_labels_have_no_answer_in_metadata(self):
        from avevidence.auditory_observer import make_probe
        make_probe(self.root/"probe",trials=12)
        questions = read_json(self.root/"probe"/"observer-input"/"questions.json")
        self.assertNotIn("answer",json.dumps(questions))
        self.assertNotIn("seed",json.dumps(questions))
        self.assertTrue((self.root/"probe"/"adjudication"/"answers.json").is_file())

    def test_video_only_external_audio_and_still_admission(self):
        from test_visual import make_video
        from avevidence.analysis_preflight import preflight
        video=make_video(self.root,name="silent",levels=[10]*30)
        preflight(video,self.root/"video-preflight")
        state=read_json(self.root/"video-preflight"/"capabilities.json")
        self.assertTrue(state["modalities"]["video"]);self.assertFalse(state["modalities"]["audio"])
        preflight(video,self.root/"av-preflight",external_audio=self.wav)
        self.assertIn("UNMAPPED_EXTERNAL_SOURCE",read_json(self.root/"av-preflight"/"capabilities.json")["quality_flags"])
        still_manifest=self.root/"stills.json"
        write_json(still_manifest,{"parent_source_sha256":sha256(self.wav),"clock":"original_pts_minus_source_origin","rows":[]})
        preflight(self.wav,self.root/"audio-stills",screenshots_manifest=still_manifest)
        self.assertFalse(read_json(self.root/"audio-stills"/"capabilities.json")["modalities"]["video"])

    def test_continuous_video_and_cut_candidates_do_not_claim_motion_review(self):
        from test_visual import make_video
        from avevidence.visual_temporal import motion_window
        video=make_video(self.root,name="cut",levels=[10]*10+[240]*10+[10]*10)
        motion_window(video,self.root/"motion",start=.2,end=2.8)
        row=read_json(self.root/"motion"/"visual-temporal.json")
        self.assertTrue((self.root/"motion"/"continuous"/"clip.mkv").is_file())
        self.assertGreaterEqual(len(row["cut_candidates"]),2)
        self.assertIn("MOTION_NOT_VERIFIED",row["quality_flags"])

    def test_voice_quality_refuses_unreviewed_mix(self):
        from avevidence.audio_witness import audio_witness
        from avevidence.speech_segments import segment_features
        audio_witness(self.wav,self.root/"witness",start=.2,end=.8,channel="left")
        config=self.root/"segment.json";write_json(config,{"source_sha256":sha256(self.wav),"source_class":"mixed_episode_audio"})
        segment_features(self.root/"witness",config,self.root/"features")
        row=read_json(self.root/"features"/"segment-features.json")
        self.assertIsNone(row["HNR_db"]);self.assertIsNone(row["jitter_local"])

    def test_derived_separation_stays_secondary_and_shows_difference(self):
        from avevidence.audio_witness import audio_witness
        from avevidence.derived_audio import admit_separation
        audio_witness(self.wav,self.root/"witness",start=.2,end=.8,channel="left")
        raw=read_json(self.root/"witness"/"witness.json")
        config=self.root/"separation-config.json"
        write_json(config,{"parent_clip_sha256":raw["artifact_sha256"],"model":"synthetic-identity","model_version":"1",
            "model_sha256":sha256(__file__),"parameters":{"identity_fixture":True}})
        admit_separation(self.root/"witness",self.root/"witness"/"audio.wav",config,self.root/"separation")
        result=read_json(self.root/"separation"/"separation.json")
        self.assertAlmostEqual(result["F0_median_change_semitones"],0,places=6)
        self.assertEqual(result["preference"],"NO_AUTOMATIC_PROMOTION")

    def test_high_level_handoff_does_not_fabricate_a_deep_reading(self):
        from avevidence.deep_read import deep_read
        deep_read(self.wav,self.root/"deep-read",episode_isolated=True)
        self.assertEqual(read_json(self.root/"deep-read"/"analysis-plan.json")["stages"]["final_deep_reading"],"NOT_GENERATED_BY_MEDIA_TOOL")

    def test_audio_clipping_with_nonzero_origin_preserves_source_clock(self):
        from avevidence.common import run
        from avevidence.audio_witness import audio_witness
        shifted=self.root/"shifted.mka"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.wav,"-c:a","pcm_s16le","-output_ts_offset","5",shifted])
        audio_witness(shifted,self.root/"shifted-witness",start=.2,end=.8)
        report=read_json(self.root/"shifted-witness"/"witness.json")
        self.assertAlmostEqual(report["source_identity"]["origin_seconds"],5.,places=4)
        self.assertAlmostEqual(report["review_mapping"]["segments"][0]["parent_start_seconds"],.2,places=4)

    def test_game_asset_mapping_refuses_wrong_identity(self):
        from avevidence.game_evidence import bind_game_events
        locator={"source_sha256":sha256(__file__),"clock":"original_pts_minus_source_origin","intervals_seconds":[[10,12]]}
        row={"event_id":"event-1","type":"voice_asset","gameplay_locator":locator,"boundary_origin":"manual_scene_boundary",
            "canonical_dialogue_id":"line-1","speaker":"Person-1","voice_asset":{"path":str(self.wav),"sha256":sha256(self.wav)},
            "asset_to_gameplay_mapping":{"parent_source_sha256":locator["source_sha256"],"artifact_sha256":sha256(self.wav),
                "segments":[{"parent_start_seconds":10,"parent_end_seconds":12,"derivative_start_seconds":0,"derivative_end_seconds":2}]}}
        config=self.root/"game.json";write_json(config,{"events":[row]})
        bind_game_events(config,self.root/"game")
        row["asset_to_gameplay_mapping"]["artifact_sha256"]=sha256(__file__)
        config.write_text(json.dumps({"events":[row]}),encoding="utf-8")
        with self.assertRaises(AVError): bind_game_events(config,self.root/"bad-game")


if __name__ == "__main__": unittest.main()
