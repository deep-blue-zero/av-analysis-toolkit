"""Source-bound stream selection in zero-call scene acquisition plans."""
import copy
from pathlib import Path
import tempfile
import unittest

import jsonschema

from avevidence.common import read_json, sha256, write_json
from avevidence.event_contracts import CLOCK
from avevidence.inventory import verify_run
from avevidence.scene_planning import plan_scene_queries


class ScenePlannerStreams(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-plan-streams-")
        self.root = Path(self.temp.name)
        # The planner checks identity rather than decoding. This fictional
        # source contract represents two video angles and two audio tracks;
        # no fixture asserts that any actual performance was heard or viewed.
        self.source = self.root / "fictional-multistream-source.txt"
        self.source.write_text("Generated multistream planning contract.\n", encoding="utf-8")
        self.text = self.root / "fictional-text.json"
        write_json(self.text, {"line": "A generated speaker calls a name."})
        self.schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "scene-query-plan.schema.json")
        jsonschema.Draft202012Validator.check_schema(self.schema)

    def tearDown(self):
        self.temp.cleanup()

    def packet(self, propositions, selections):
        source_hash = sha256(self.source)
        observations, claims, dependencies = [], [], []
        for index, proposition in enumerate(propositions):
            oid, cid = "o-%d" % index, "c-%d" % index
            observations.append({"id": oid, "proposition": proposition,
                "statement": "Unreviewed fictional premise.", "status": "OPEN",
                "evidence_ids": ["text"], "interval_seconds": [1, 2]})
            claims.append({"id": cid, "proposition": proposition,
                "statement": "A fictional %s question remains open." % proposition,
                "status": "OPEN", "observation_ids": [oid], "interval_seconds": [1, 2]})
            dependencies.append({"from": oid, "to": cid, "relation": "DEPENDS_ON",
                "reason": "This fictional claim requires review of its declared premise."})
        return {"schema": "ave.scene-packet.v1", "scene_id": "fictional-stream-selection",
            "source": {"sha256": source_hash, "clock": CLOCK, "path": self.source.name,
                "duration_seconds": 3, **selections},
            "interval_seconds": [0, 3],
            "narrative_boundary": {"basis": "generated_contract", "description": "One fictional bounded event."},
            "question": "Which selected source streams must each acquisition use?",
            "evidence": {"TXT": [{"id": "text", "authority": "generated", "record_pointer": "/line",
                "artifact": {"path": self.text.name, "sha256": sha256(self.text)},
                "locator": {"source_sha256": source_hash, "clock": CLOCK, "intervals_seconds": [[1, 2]]}}]},
            "observations": observations, "claims": claims, "dependencies": dependencies,
            "conflicts": [], "adjudication": {"decisions": {}}, "open_questions": ["No perceptual review has occurred."]}

    def plan(self, propositions, selections):
        packet = self.packet(propositions, selections)
        path = self.root / "packet.json"
        write_json(path, packet)
        original = path.read_bytes()
        output = self.root / "plan"
        plan_scene_queries(path, output)
        result = read_json(output / "query-plan.json")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result["api_spend_usd"], 0)
        self.assertEqual(result["execution"], "NONE")
        self.assertTrue(all(not r["api_submission_authorized"] for r in result["requests"]))
        jsonschema.validate(result, self.schema)
        verify_run(output)
        return result

    def test_nondefault_tracks_survive_all_applicable_routes(self):
        result = self.plan(["delivery", "motion", "visual_fact", "sound_level", "speech_timing", "speaker_identity"],
                           {"audio_stream": 3, "video_stream": 1})
        expected = {
            "AUDITORY": {"audio_stream": 3},
            "TEMPORAL": {"video_stream": 1},
            "STATIC": {"video_stream": 1},
            "ACOUSTIC_MEASUREMENT": {"audio_stream": 3},
            "ALIGNMENT_REVIEW": {"audio_stream": 3},
            "SOURCE_ATTRIBUTION_REVIEW": {"audio_stream": 3, "video_stream": 1},
        }
        self.assertEqual({r["route"] for r in result["requests"]}, set(expected))
        for request in result["requests"]:
            with self.subTest(route=request["route"]):
                self.assertEqual({k: request[k] for k in ("audio_stream", "video_stream") if k in request},
                                 expected[request["route"]])
                self.assertEqual(request["stream_selection_status"], "SELECTED")

    def test_av_sync_carries_both_tracks_for_paired_temporal_review(self):
        requests = self.plan(["av_sync"], {"audio_stream": 2, "video_stream": 0})["requests"]
        by_route = {r["route"]: r for r in requests}
        self.assertEqual(by_route["AUDITORY"]["audio_stream"], 2)
        self.assertNotIn("video_stream", by_route["AUDITORY"])
        self.assertEqual(by_route["TEMPORAL"]["audio_stream"], 2)
        self.assertEqual(by_route["TEMPORAL"]["video_stream"], 0)
        self.assertTrue(all(r["stream_selection_status"] == "SELECTED" for r in requests))

    def test_absent_selection_is_explicitly_open_and_zero_is_selected(self):
        result = self.plan(["delivery", "motion", "speaker_identity", "av_sync"], {"audio_stream": 0})
        for request in result["requests"]:
            with self.subTest(route=request["route"], profile=request["profile"]):
                if request["route"] == "AUDITORY":
                    self.assertEqual(request["audio_stream"], 0)
                    self.assertEqual(request["stream_selection_status"], "SELECTED")
                else:
                    self.assertIsNone(request["video_stream"])
                    self.assertEqual(request["stream_selection_status"], "OPEN_SELECTION_REQUIRED")
                self.assertIn("must never authorize use of a default track", request["execution_requirement"])

    def test_text_and_premise_routes_do_not_invent_media_selections(self):
        result = self.plan(["wording", "interpretation"], {"audio_stream": 3, "video_stream": 1})
        self.assertEqual({r["route"] for r in result["requests"]}, {"TEXT_WITNESS", "PREMISE_REVIEW"})
        for request in result["requests"]:
            self.assertFalse({"audio_stream", "video_stream", "stream_selection_status"} & request.keys())

    def test_no_selected_streams_does_not_infer_first_audio_or_video(self):
        result = self.plan(["delivery", "visual_fact"], {})
        by_route = {r["route"]: r for r in result["requests"]}
        self.assertIsNone(by_route["AUDITORY"]["audio_stream"])
        self.assertIsNone(by_route["STATIC"]["video_stream"])
        self.assertTrue(all(r["stream_selection_status"] == "OPEN_SELECTION_REQUIRED" for r in result["requests"]))

    def test_schema_keeps_legacy_plans_readable_and_rejects_false_selection(self):
        result = self.plan(["delivery", "av_sync"], {"audio_stream": 3, "video_stream": 1})
        legacy = copy.deepcopy(result)
        for request in legacy["requests"]:
            for key in ("audio_stream", "video_stream", "stream_selection_status"):
                request.pop(key, None)
            if request["route"] != "AUDITORY":
                request.pop("execution_requirement", None)
        jsonschema.validate(legacy, self.schema)
        mutations = [
            {"audio_stream": -1},
            {"audio_stream": None},
            {"stream_selection_status": "OPEN_SELECTION_REQUIRED"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                invalid = copy.deepcopy(result)
                invalid["requests"][0].update(mutation)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(invalid, self.schema)
        invalid = copy.deepcopy(result)
        temporal = next(r for r in invalid["requests"] if r["route"] == "TEMPORAL")
        temporal.pop("audio_stream")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, self.schema)


if __name__ == "__main__":
    unittest.main()
