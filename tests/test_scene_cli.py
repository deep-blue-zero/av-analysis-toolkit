"""Public CLI workflows over generated contracts/media; zero hosted calls."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from avevidence.cli import main
from avevidence.common import read_json, run, sha256, write_json
from avevidence.inventory import verify_run
from avevidence.visual import build_frame_index

EXAMPLE_PATH=Path(__file__).resolve().parent.parent/"examples"/"build_synthetic_scene.py"
EXAMPLE_SPEC=importlib.util.spec_from_file_location("synthetic_scene_example",EXAMPLE_PATH)
EXAMPLE=importlib.util.module_from_spec(EXAMPLE_SPEC)
EXAMPLE_SPEC.loader.exec_module(EXAMPLE)
HAS_MEDIA_TOOLS=bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class SceneCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_temp=tempfile.TemporaryDirectory(prefix="ave-scene-cli-")
        cls.shared=Path(cls.shared_temp.name)
        cls.source=cls.shared/"generated.mkv"
        cls.index=cls.shared/"frame-index"
        if HAS_MEDIA_TOOLS:
            run(["ffmpeg","-v","error","-nostdin","-n","-f","lavfi","-i","color=c=gray:size=32x24:rate=24",
                 "-f","lavfi","-i","anullsrc=r=8000:cl=mono","-t","3","-map","0:v","-map","1:a",
                 "-c:v","ffv1","-pix_fmt","yuv420p","-c:a","pcm_s16le",cls.source])
            build_frame_index(cls.source,cls.index)

    @classmethod
    def tearDownClass(cls):
        cls.shared_temp.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="ave-scene-command-")
        self.root=Path(self.temp.name)
        self.example=EXAMPLE.build_synthetic_scene(self.root/"example")
        self.before=Path(self.example["before"]); self.after=Path(self.example["after"])
        self.no_http=patch("avevidence.providers.openai_audio.http_transport",side_effect=AssertionError("Unexpected provider call"))
        self.no_http.start()

    def tearDown(self):
        self.no_http.stop(); self.temp.cleanup()

    def command(self,*arguments,expected=0):
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch("sys.stdout",stdout),patch("sys.stderr",stderr):
            status=main([str(x) for x in arguments])
        self.assertEqual(status,expected,stderr.getvalue()+stdout.getvalue())
        return json.loads(stdout.getvalue()) if status==0 else stderr.getvalue()

    def test_scene_public_validate_prepare_reconcile_plan_delta_localize_lexical_change(self):
        original=self.before.read_bytes()
        validated=self.command("scene","validate",self.before)
        self.assertEqual(validated["status"],"VALID")
        self.command("scene","prepare",self.before,self.root/"prepared")
        self.assertEqual((self.root/"prepared"/"input-packet.json").read_bytes(),original)
        self.assertEqual(self.command("scene","validate",self.root/"prepared"/"scene-packet.json")["packet_id"],validated["packet_id"])
        self.command("scene","reconcile",self.after,self.root/"reconciled")
        result=read_json(self.root/"reconciled"/"reconciliation.json")
        states=result["dependency_assessment"]["state_overlay"]
        self.assertEqual(states["oGuess"]["after"],"CONTRADICTED")
        self.assertEqual(states["cReferent"]["after"],"RECHECK")
        self.assertEqual(states["cUrgency"]["after"],"RECHECK")
        self.assertEqual(states["oDelivery"]["after"],"REPORTED")
        self.assertEqual(states["cDelivery"]["after"],"PROVISIONAL")
        self.assertEqual(states["cName"]["after"],"SUPPORTED")
        self.assertEqual(states["oContext"]["after"],"REPORTED")
        self.assertFalse(result["observations"]["oContext"]["adequate_for_support"])
        self.assertEqual(set(result["conflicts"][0]["observation_ids"]), {"oText", "oGuess"})
        self.assertIn("oContext", {r["id"] for r in read_json(self.before)["observations"]})
        self.assertIn("oContext", {r["id"] for r in read_json(self.after)["observations"]})
        self.assertTrue(result["raw_records_preserved"])
        self.assertTrue(result["no_majority_vote"])
        self.assertIn("stronger textual witness",result["conflicts"][0]["recommendation"])
        self.command("scene","plan",self.after,self.root/"planned")
        plan=read_json(self.root/"planned"/"query-plan.json")
        self.assertEqual(plan["api_spend_usd"],0)
        self.assertEqual(plan["execution"],"NONE")
        self.assertTrue(any(r["route"]=="AUDITORY" and r["task_profile"]=="SPEECH_PERFORMANCE" for r in plan["requests"]))
        self.assertTrue(any(r["route"]=="TEMPORAL" and r["question_type"]=="EXACT_CONTACT" for r in plan["requests"]))
        self.command("scene","delta",self.before,self.after,self.root/"delta")
        delta=read_json(self.root/"delta"/"claim-delta.json")
        rows={r["claim_id"]:r for r in delta["claims"]}
        self.assertEqual(rows["cDelivery"]["disposition"],"PRESERVE")
        self.assertEqual(rows["cReferent"]["after_state"],"RECHECK")
        self.assertEqual(rows["cReferent"]["disposition"],"OPEN")
        self.assertEqual(rows["cUrgency"]["disposition"],"OPEN")
        self.assertEqual(self.before.read_bytes(),original)
        for folder in ("prepared","reconciled","planned","delta"):verify_run(self.root/folder)

    def test_public_scene_refuses_missing_hash_changed_and_circular_refs(self):
        value=read_json(self.before)
        value["evidence"]["TXT"][0]["artifact"]["sha256"]="a1"*32
        wrong=self.before.parent/"wrong-hash.json";write_json(wrong,value)
        self.assertIn("changed",self.command("scene","validate",wrong,expected=2))
        value=read_json(self.before)
        value["dependencies"].append({"from":"cUrgency","to":"cReferent","relation":"DEPENDS_ON","reason":"Artificial cycle"})
        cyclic=self.before.parent/"cyclic.json";write_json(cyclic,value)
        self.assertIn("Circular",self.command("scene","validate",cyclic,expected=2))
        value=read_json(self.before);value["observations"][0]["evidence_ids"]=["absent-evidence"]
        absent=self.before.parent/"absent.json";write_json(absent,value)
        self.assertIn("missing evidence",self.command("scene","validate",absent,expected=2))

    def test_public_profiles_and_capability_export_preserve_failure(self):
        profiles=self.command("observer","profiles")
        self.assertEqual(len(profiles["profiles"]),6)
        self.assertEqual(set(profiles["competency_profiles"]),
                         {"SPEECH_DELIVERY","AUDITORY_EVENT_TIMING","LEXICAL_TRANSCRIPTION","EXACT_WORD_TIMING"})
        self.assertEqual(profiles["competency_profiles"]["SPEECH_DELIVERY"],
                         profiles["profiles"]["SPEECH_PERFORMANCE"])
        self.assertEqual(profiles["qualification"],"NONE_IMPLIED")
        self.command("observer","capability-profile",self.example["capability_config"],self.root/"capabilities")
        record=read_json(self.root/"capabilities"/"task-capabilities.json")
        self.assertEqual(record["historical_probes"][0]["status"],"PROBE_FAILED")
        self.assertTrue(all(r["status"]=="UNQUALIFIED" and r["test_status"]=="NOT_TESTED" for r in record["tasks"].values()))
        self.assertFalse(record["authorizes_hosted_submission"])
        verify_run(self.root/"capabilities")

    def test_public_planner_resource_exhaustion_retains_open(self):
        self.command("scene","plan",self.after,self.root/"limited","--max-requests",1,"--max-frames",0,"--max-audio-seconds",0)
        plan=read_json(self.root/"limited"/"query-plan.json")
        self.assertLessEqual(len(plan["requests"]),1)
        self.assertTrue(any(r["status"]=="OPEN_RESOURCE_CEILING" for r in plan["open_requests"]))
        self.assertEqual(plan["planned_resources"]["frames"],0)
        self.assertEqual(plan["planned_resources"]["audio_seconds"],0)

    def test_public_delta_cannot_preserve_a_changed_unreviewed_premise(self):
        decisions=self.root/"decisions.json"
        write_json(decisions,{"reviewer":"Synthetic contract tester","decisions":{"cReferent":{
            "disposition":"PRESERVE","reason":"A deliberately invalid attempt to conceal a rechecked premise.",
            "what_survived":"The raw statement remains in the input.","what_changed":"Its lexical premise was contradicted.",
            "remaining_open_questions":["The corrected referent needs a new formulation."]}}})
        output=self.root/"bad-delta"
        error=self.command("scene","delta",self.before,self.after,output,"--decisions",decisions,expected=2)
        self.assertIn("cannot preserve or strengthen",error)
        self.assertFalse(output.exists())

    @unittest.skipUnless(HAS_MEDIA_TOOLS,"FFmpeg and ffprobe are required")
    def test_public_performance_sections_preserve_full_source_without_submission(self):
        config=self.root/"sections.json"
        lyric=self.root/"fictional-lyric.txt";lyric.write_text("Invented lyric, not performed",encoding="utf-8")
        write_json(config,{"schema":"ave.performance-sections.input.v1","source_path":str(self.source),"source_sha256":sha256(self.source),"audio_stream":1,"video_stream":0,
            "performance_interval_seconds":[0,3],"sections":[
                {"id":"opening","label":"Declared formal opening","interval_seconds":[0,1.5],"boundary_origin":"musical_review_declared","question":"Describe any section change","lyric_ids":["lyric-1"]},
                {"id":"close","label":"Declared formal close","interval_seconds":[1.5,3],"boundary_origin":"estimated_boundary","question":"Describe any closing change"}],
            "lyrics":[{"id":"lyric-1","artifact":{"path":lyric.name,"sha256":sha256(lyric)},"interval_seconds":[.2,.8],"boundary_origin":"subtitle_boundary"}]})
        self.command("performance-sections",config,self.root/"sections")
        report=read_json(self.root/"sections"/"performance-sections.json")
        self.assertEqual(report["source"]["sha256"],sha256(self.source))
        self.assertEqual(report["performance_interval_seconds"],[0,3])
        self.assertEqual(len(report["sections"]),2)
        self.assertEqual(report["paid_calls"],0)
        self.assertFalse(report["source_media_copied"])
        self.assertEqual(report["evidence_references"][0]["boundary_origin"],"subtitle_boundary")
        verify_run(self.root/"sections")

    @unittest.skipUnless(HAS_MEDIA_TOOLS,"FFmpeg and ffprobe are required")
    def test_public_scoped_human_review_is_separate_from_mock_response(self):
        self.command("audio-witness",self.source,self.root/"witness","--start",1,"--end",2,"--audio-stream",1)
        request=self.root/"request.json"
        write_json(request,{"request_id":"synthetic-review","question":"Describe audible delivery.","source_sha256":sha256(self.source),"task_profile":"SPEECH_PERFORMANCE"})
        self.command("observer","observe","--witness-run",self.root/"witness","--request",request,"--output",self.root/"mock")
        path=self.root/"mock"/"observation.json";before=path.read_bytes()
        config=self.root/"review-input.json"
        write_json(config,{"reviewer_type":"owner","reviewer":"Synthetic contract tester","scope":"A fixture declaration, not actual human listening.",
            "judgment":"abstain","exceptions":["The source is generated silence and this is a contract test."],"date":"2026-10-06",
            "inspected_intervals":[{"source_sha256":sha256(self.source),"clock":"original_pts_minus_source_origin","stream_index":1,"intervals_seconds":[[1,2]]}]})
        self.command("observer","scoped-human-review",self.root/"mock",config,self.root/"human-review")
        record=read_json(self.root/"human-review"/"human-review.json")
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(record["global_backend_qualification"])
        self.assertEqual(record["observation_sha256"],sha256(path))

    @unittest.skipUnless(HAS_MEDIA_TOOLS,"FFmpeg and ffprobe are required")
    def test_public_temporal_inspect_review_escalate_preserve_generation_vs_review(self):
        low=self.root/"temporal-low"
        self.command("temporal-inspect",self.source,low,"--question","Does the generated image sequence change?","--start",0,"--end",1,
            "--profile","TEMPORAL_LOW","--question-type","CONTACT_ORDER","--video-stream",0,"--width",0,"--frame-index-run",self.index)
        report=read_json(low/"temporal_report.json")
        self.assertEqual(report["status"],"GENERATED_NOT_REVIEWED")
        self.assertEqual(report["assessment"]["status"],"OPEN")
        declaration=self.root/"review.json"
        write_json(declaration,{"schema":"ave.temporal-review.v1","temporal_run_manifest_sha256":sha256(low/"run.json"),"temporal_report_sha256":sha256(low/"temporal_report.json"),
            "source_sha256":report["source_sha256"],"stream_index":0,"reviewer":"Synthetic CLI contract tester","actual_review_declared":False,
            "inspection_mode":"source_frame_sequence","question_answered":False,"observation":"No actual review was performed by this test.",
            "inspected_interval_seconds":[0,1],"inspected_source_frame_indices":sorted({r["source_frame_index"] for r in report["frame_rows"]})})
        self.command("temporal-review",low,declaration,self.root/"temporal-review")
        review=read_json(self.root/"temporal-review"/"review-assessment.json")
        self.assertEqual(review["status"],"OPEN")
        self.command("temporal-escalate",low,self.root/"escalation","--review-assessment",self.root/"temporal-review"/"review-assessment.json",
            "--critical-start",.25,"--critical-end",.5,"--remaining-frame-budget",30,"--remaining-seconds-budget",1)
        step=read_json(self.root/"escalation"/"escalation.json")
        self.assertEqual(step["status"],"EXTRACT_PLANNED")
        self.assertEqual(step["next_profile"],"TEMPORAL_MEDIUM")
        self.assertEqual(step["next_interval_seconds"],[.25,.5])
        for folder in (low,self.root/"temporal-review",self.root/"escalation"):verify_run(folder)

    @unittest.skipUnless(HAS_MEDIA_TOOLS,"FFmpeg and ffprobe are required")
    def test_public_deep_read_scene_packet_uses_supplied_method_without_model_calls(self):
        example=EXAMPLE.build_synthetic_scene(self.root/"media-example",source=self.source,audio_stream=1,video_stream=0)
        method=self.root/"method.txt";method.write_bytes(b"Supplied synthetic analytical method.\r\nReason jointly; retain uncertainty.\r\n")
        output=self.root/"deep-read"
        self.command("deep-read",self.source,output,"--scene-packet",example["after"],"--method",method,"--episode-isolated",
            "--auditory-observer","none","--audio-stream",1,"--video-stream",0)
        self.assertEqual((output/"method.txt").read_bytes(),method.read_bytes())
        plan=read_json(output/"analysis-plan.json")
        self.assertEqual(plan["stages"]["dramatic_event"]["status"],"PREPARED_NOT_INTERPRETED")
        self.assertEqual(read_json(output/"dramatic-event-queries"/"query-plan.json")["api_spend_usd"],0)
        self.assertFalse((output/"hosted-observations").exists())
        verify_run(output)
        bad=self.root/"wrong-source"
        self.assertIn("identities differ",self.command("deep-read",self.source,bad,"--scene-packet",self.after,expected=2))
        self.assertFalse(bad.exists())

    def test_example_generation_refuses_repo_and_existing_outputs(self):
        from avevidence.common import AVError
        with self.assertRaises(AVError):EXAMPLE.build_synthetic_scene(EXAMPLE_PATH.parent/"generated-forbidden")
        with self.assertRaises(AVError):EXAMPLE.build_synthetic_scene(self.before.parent)

    def test_generator_entrypoint_runs_from_outside_checkout(self):
        target=self.root/"entrypoint-example"
        result=subprocess.run([sys.executable,"-B",str(EXAMPLE_PATH),str(target)],cwd=self.root,
                              check=False,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=json.loads(result.stdout)
        self.assertEqual(Path(metadata["output"]),target.resolve())
        self.assertEqual(self.command("scene","validate",metadata["before"])["status"],"VALID")
