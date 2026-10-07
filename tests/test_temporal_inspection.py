"""Generated 8-bit source-clock and review-contract tests; no perceptual validation."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.common import AVError, run, sha256
from avevidence.visual import build_frame_index
from avevidence.temporal_inspection import (extract_temporal_inspection, load_temporal_inspection,
                                           plan_escalation, validate_temporal_review)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe are required")
class TemporalInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ave-temporal-test-")
        cls.root = Path(cls.temp.name).resolve()
        assert cls.root.parent == Path(tempfile.gettempdir()).resolve()
        raw=cls.root/"source.gray"
        raw.write_bytes(b"".join(bytes([20+(i*3)%220])*32*24 for i in range(72)))
        cls.source=cls.root/"source.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-f","rawvideo","-pixel_format","gray",
             "-video_size","32x24","-framerate","24","-i",raw,"-c:v","ffv1",cls.source])
        cls.index=cls.root/"index"
        build_frame_index(cls.source,cls.index)
        cls.counter=0

    @classmethod
    def tearDownClass(cls):
        assert Path(cls.temp.name).resolve()==cls.root
        cls.temp.cleanup()

    def out(self):
        type(self).counter+=1
        return self.root/f"run-{type(self).counter}"

    def extract(self,profile="TEMPORAL_LOW",**kwargs):
        output=self.out()
        parameters={"question":"Does the generated sequence change order?","start":0,"end":1,
                    "profile":profile,"width":0,"frame_index_run":self.index}
        parameters.update(kwargs)
        extract_temporal_inspection(self.source,output,**parameters)
        return output,json.loads((output/"temporal_report.json").read_text(encoding="utf-8"))

    def declaration(self,output,report,**changes):
        result={"schema":"ave.temporal-review.v1","temporal_run_manifest_sha256":sha256(output/"run.json"),
                "temporal_report_sha256":sha256(output/"temporal_report.json"),"source_sha256":report["source_sha256"],
                "stream_index":report["stream_index"],"reviewer":"Synthetic contract tester",
                "actual_review_declared":True,"inspection_mode":"source_frame_sequence",
                "question_answered":True,"observation":"Synthetic declaration only; this test does not establish actual viewing.",
                "inspected_interval_seconds":report["critical_interval_seconds"],
                "inspected_source_frame_indices":sorted({row["source_frame_index"] for row in report["frame_rows"]})}
        result.update(changes)
        return result

    def test_all_profiles_use_actual_original_frame_indices_and_hashes(self):
        for profile,count in (("TEMPORAL_LOW",2),("TEMPORAL_MEDIUM",8),("TEMPORAL_HIGH",12),("FRAME_COMPLETE_WINDOW",24)):
            with self.subTest(profile=profile):
                output,report=self.extract(profile)
                self.assertEqual(report["unique_frame_count"],count)
                self.assertEqual(report["status"],"GENERATED_NOT_REVIEWED")
                self.assertEqual(report["assessment"]["status"],"OPEN")
                self.assertEqual(report["source_sha256"],sha256(self.source))
                self.assertEqual(report["source_frame_count_in_interval"],24)
                for row in report["frame_rows"]:
                    self.assertTrue(0<=row["source_seconds"]<1)
                    self.assertEqual(row["sha256"],sha256(output/row["path"]))
                    self.assertIsInstance(row["source_pts"],int)
                load_temporal_inspection(output)

    def test_alias_canonicalization_and_uncached_index_are_recorded(self):
        output,report=self.extract("LOW",frame_index_run=None)
        self.assertEqual(report["profile"],"TEMPORAL_LOW")
        self.assertEqual(report["frame_index_reference"]["run_manifest_path"],"frame-index/run.json")
        self.assertTrue((output/"frame-index/frame_index.json").exists())
        load_temporal_inspection(output)

    def test_vfr_and_nonzero_origin_preserve_pts(self):
        source=self.root/"vfr.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.source,"-vf","select='not(eq(n,1)+eq(n,3))'",
             "-fps_mode","vfr","-c:v","ffv1","-output_ts_offset","2.5",source])
        output=self.out()
        extract_temporal_inspection(source,output,question="Inspect the VFR sequence",start=0,end=1,
                                    profile="FRAME_COMPLETE_WINDOW",width=0)
        _,report,_=load_temporal_inspection(output)
        self.assertAlmostEqual(report["origin_seconds"],2.5)
        self.assertEqual(report["unique_frame_count"],22)
        self.assertTrue(all(abs(row["original_pts_seconds"]-row["source_seconds"]-2.5)<1e-8 for row in report["frame_rows"]))
        deltas=[b["source_seconds"]-a["source_seconds"] for a,b in zip(report["frame_rows"],report["frame_rows"][1:])]
        self.assertGreater(max(deltas),min(deltas)*1.5)

    def test_sampling_duplicates_do_not_become_extra_frame_coverage(self):
        source=self.root/"gap.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.source,"-vf","select='not(between(n,5,9))'",
             "-fps_mode","vfr","-c:v","ffv1",source])
        output=self.out()
        extract_temporal_inspection(source,output,question="VFR duplicate selection",start=0,end=1,
                                    profile="TEMPORAL_HIGH",width=0)
        _,report,_=load_temporal_inspection(output)
        self.assertLess(report["unique_frame_count"],report["request_row_count"])
        self.assertEqual(report["source_frame_count_in_interval"],19)

    def test_limits_refuse_before_decode_and_no_packet_is_published(self):
        for params in ({"end":31},{"profile":"FRAME_COMPLETE_WINDOW","end":4},
                       {"profile":"TEMPORAL_HIGH","limits":{"max_frames":1}}):
            output=self.out()
            kwargs={"question":"Bounded question","start":0,"end":1};kwargs.update(params)
            with self.assertRaises(AVError):
                extract_temporal_inspection(self.root/"missing.mkv",output,**kwargs)
            self.assertFalse(output.exists())
        with self.assertRaises(AVError):
            self.extract(limits={"max_pixels":10})
        with self.assertRaises(AVError):
            self.extract(limits={"max_output_bytes":100})
        with self.assertRaises(AVError):
            self.extract(frame_index_run=None,limits={"max_index_source_seconds":1})

    def test_timeout_refusal_does_not_publish_an_artifact(self):
        output=self.out()
        with self.assertRaisesRegex(AVError,"wall-clock"):
            extract_temporal_inspection(self.source,output,question="Timeout test",start=0,end=1,
                                        frame_index_run=self.index,limits={"wall_timeout_seconds":.001})
        self.assertFalse(output.exists())

    def test_existing_output_mutated_frame_and_mutated_index_are_refused(self):
        output,report=self.extract()
        with self.assertRaises(AVError):
            extract_temporal_inspection(self.source,output,question="Again",start=0,end=1)
        (output/report["frame_rows"][0]["path"]).write_bytes(b"changed")
        with self.assertRaises(AVError):
            validate_temporal_review(output,self.declaration(output,report))
        cache=self.root/"bad-index"
        shutil.copytree(self.index,cache)
        (cache/"frame_index.json").write_bytes(b"changed")
        with self.assertRaises(AVError):
            self.extract(frame_index_run=cache)

    def test_changed_source_refuses_cached_admission(self):
        source=self.root/"changed.mkv"
        shutil.copy2(self.source,source)
        source.write_bytes(source.read_bytes()+b"changed")
        with self.assertRaises(AVError):
            extract_temporal_inspection(source,self.out(),question="Source binding",start=0,end=1,frame_index_run=self.index)

    def test_exact_order_needs_review_of_every_critical_source_frame(self):
        output,report=self.extract(question_type="CONTACT_ORDER")
        review=validate_temporal_review(output,self.declaration(output,report))
        self.assertEqual(review["status"],"OPEN")
        full,full_report=self.extract("FRAME_COMPLETE_WINDOW",question_type="CONTACT_ORDER")
        review=validate_temporal_review(full,self.declaration(full,full_report))
        self.assertEqual(review["status"],"ADEQUATE_DECLARED")
        self.assertTrue(review["complete_critical_source_frame_coverage_declared"])
        missing=self.declaration(full,full_report,inspected_source_frame_indices=list(range(23)))
        self.assertEqual(validate_temporal_review(full,missing)["status"],"OPEN")
        declared=self.declaration(full,full_report,actual_review_declared=False)
        self.assertEqual(validate_temporal_review(full,declared)["status"],"OPEN")

    def test_review_binding_unknown_frames_and_missing_interval_fail_safe(self):
        output,report=self.extract("FRAME_COMPLETE_WINDOW")
        for changes in ({"temporal_report_sha256":"0"*64},{"source_sha256":"0"*64},
                        {"inspected_source_frame_indices":[999]},{"inspected_source_frame_indices":[0,0]},
                        {"inspected_source_frame_indices":[1,0]}):
            with self.assertRaises(AVError):
                validate_temporal_review(output,self.declaration(output,report,**changes))
        review=self.declaration(output,report);review.pop("inspected_interval_seconds")
        self.assertEqual(validate_temporal_review(output,review)["status"],"OPEN")

    def test_av_sync_cannot_be_admitted_from_reviewed_images_alone(self):
        output,report=self.extract("FRAME_COMPLETE_WINDOW",question_type="AV_SYNC")
        review=validate_temporal_review(output,self.declaration(output,report))
        self.assertEqual(review["status"],"OPEN")
        self.assertTrue(any("audio" in reason.lower() for reason in review["reasons"]))

    def test_geometry_hdr_refusal_is_preserved(self):
        hdr=self.root/"hdr.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.source,"-c:v","libx264","-pix_fmt","yuv420p",
             "-color_primaries","bt2020","-color_trc","smpte2084","-colorspace","bt2020nc",hdr])
        with self.assertRaisesRegex(AVError,"HDR"):
            extract_temporal_inspection(hdr,self.out(),question="Refusal",start=0,end=1,width=0)

    def test_adaptive_planner_narrows_escalates_stops_and_retains_open(self):
        output,report=self.extract(end=2,critical_interval=[.5,1])
        step=plan_escalation(report)
        self.assertEqual(step["status"],"EXTRACT_PLANNED")
        self.assertEqual(step["next_profile"],"TEMPORAL_MEDIUM")
        self.assertEqual(step["next_interval_seconds"],[.5,1])
        self.assertEqual(plan_escalation(report,remaining_frame_budget=1)["status"],"OPEN_BUDGET")
        self.assertEqual(plan_escalation(report,remaining_seconds_budget=0)["status"],"OPEN_BUDGET")
        full,full_report=self.extract("FRAME_COMPLETE_WINDOW")
        self.assertEqual(plan_escalation(full_report)["status"],"OPEN")
        review=validate_temporal_review(full,self.declaration(full,full_report))
        self.assertEqual(plan_escalation(full_report,review_assessment=review)["status"],"STOP_ADEQUATE")
        altered=copy.deepcopy(full_report);altered["question"]="Another question"
        with self.assertRaises(AVError):
            plan_escalation(altered,review_assessment=review)
        _,low=self.extract()
        self.assertEqual(plan_escalation(low)["status"],"OPEN_NO_NARROWER_WINDOW")

    def test_synchronized_review_records_positive_and_negative_event_pairs(self):
        source=self.root/"with-audio.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.source,
             "-f","lavfi","-i","anullsrc=r=8000:cl=mono","-t","3","-map","0:v","-map","1:a",
             "-c:v","copy","-c:a","pcm_s16le",source])
        output=self.out()
        extract_temporal_inspection(source,output,question="Event synchrony contract",question_type="AV_SYNC",
                                    start=0,end=1,profile="FRAME_COMPLETE_WINDOW",width=0,stream_index=0)
        _,report,_=load_temporal_inspection(output)
        audio={"actual_audio_inspection_declared":True,"source_sha256":report["source_sha256"],
               "stream_index":1,"clock":report["clock"],"inspected_interval_seconds":[0,1],
               "audio_event_source_seconds":.5,"visual_event_source_frame_index":12,"tolerance_seconds":.01}
        declaration=self.declaration(output,report,inspection_mode="synchronized_av",audio_review=audio)
        review=validate_temporal_review(output,declaration)
        self.assertEqual(review["status"],"ADEQUATE_DECLARED")
        self.assertTrue(review["synchrony_event_pair"]["within_declared_tolerance"])
        audio["audio_event_source_seconds"]=.65
        review=validate_temporal_review(output,declaration)
        self.assertEqual(review["status"],"ADEQUATE_DECLARED")
        self.assertFalse(review["synchrony_event_pair"]["within_declared_tolerance"])

    def test_av_sync_audio_scope_and_both_events_must_cover_critical_window(self):
        source=self.root/"critical-sync-audio.mkv"
        run(["ffmpeg","-v","error","-nostdin","-n","-i",self.source,
             "-f","lavfi","-i","anullsrc=r=8000:cl=mono","-t","3","-map","0:v","-map","1:a",
             "-c:v","copy","-c:a","pcm_s16le",source])
        output=self.out()
        critical=[.375,.75]
        extract_temporal_inspection(source,output,question="Critical-window synchrony contract",question_type="AV_SYNC",
                                    start=0,end=1,critical_interval=critical,profile="FRAME_COMPLETE_WINDOW",width=0,stream_index=0)
        _,report,_=load_temporal_inspection(output)
        audio={"actual_audio_inspection_declared":True,"source_sha256":report["source_sha256"],
               "stream_index":1,"clock":report["clock"],"inspected_interval_seconds":critical,
               "audio_event_source_seconds":.5,"visual_event_source_frame_index":12,"tolerance_seconds":.01}
        declaration=self.declaration(output,report,inspection_mode="synchronized_av",inspected_interval_seconds=[0,1],audio_review=audio)
        self.assertEqual(validate_temporal_review(output,declaration)["status"],"ADEQUATE_DECLARED")
        cases=(
            ("unrelated_context_pair",{"inspected_interval_seconds":[0,.1],"audio_event_source_seconds":.05,"visual_event_source_frame_index":0},"cover the critical interval"),
            ("partial_audio_scope",{"inspected_interval_seconds":[.375,.7]},"cover the critical interval"),
            ("audio_event_before",{"inspected_interval_seconds":[0,1],"audio_event_source_seconds":.25},"audio event is outside"),
            ("visual_event_before",{"visual_event_source_frame_index":6},"visual event is outside"),
            ("audio_event_at_end",{"inspected_interval_seconds":[0,1],"audio_event_source_seconds":.75},"audio event is outside"),
            ("visual_event_at_end",{"visual_event_source_frame_index":18},"visual event is outside"),
        )
        for label,changes,reason in cases:
            with self.subTest(case=label):
                changed=copy.deepcopy(declaration);changed["audio_review"].update(changes)
                assessment=validate_temporal_review(output,changed)
                self.assertEqual(assessment["status"],"OPEN")
                self.assertIsNone(assessment["adequate_interval_seconds"])
                self.assertTrue(any(reason in item for item in assessment["reasons"]))
        # Critical intervals include their start and exclude their end.
        start_pair=copy.deepcopy(declaration)
        start_pair["audio_review"].update(audio_event_source_seconds=.375,visual_event_source_frame_index=9)
        self.assertEqual(validate_temporal_review(output,start_pair)["status"],"ADEQUATE_DECLARED")

    def test_schema_loads_and_all_receipt_required_fields_exist(self):
        _,report=self.extract()
        schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/temporal-inspection.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"],report["schema"])
        self.assertTrue(set(schema["required"])<=set(report))

    def test_bad_profiles_critical_bounds_and_unbounded_windows_are_refused(self):
        for params in ({"profile":"all"},{"critical_interval":[0,4]},{"start":None},
                       {"question_type":"NATIVE_VIDEO_MODEL"},{"limits":{"unknown":2}}):
            with self.assertRaises(AVError):
                self.extract(**params)


if __name__=="__main__":
    unittest.main()
