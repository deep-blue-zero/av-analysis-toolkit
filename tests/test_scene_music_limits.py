"""Executable music query intervals; generated contracts and no external calls."""
import unittest

import jsonschema
import test_scene_planner_streams as fixtures

from avevidence.common import read_json, write_json
from avevidence.inventory import verify_run
from avevidence.scene_planning import plan_scene_queries


class SceneMusicLimits(unittest.TestCase):
    setUp = fixtures.ScenePlannerStreams.setUp
    tearDown = fixtures.ScenePlannerStreams.tearDown
    packet = fixtures.ScenePlannerStreams.packet

    PROPOSITIONS = ("recording_identity", "composition_identity", "arrangement_identity",
                    "performance_identity", "musical_reference")

    def music_plan(self, proposition, seconds):
        packet = self.packet([proposition], {"audio_stream": 3})
        packet["source"]["duration_seconds"] = 600
        packet["interval_seconds"] = [0, 600]
        bounds = [10, 10 + seconds]
        packet["evidence"]["TXT"][0]["locator"]["intervals_seconds"] = [bounds]
        packet["observations"][0]["interval_seconds"] = bounds
        packet["claims"][0]["interval_seconds"] = bounds
        stem = proposition + "-" + str(seconds)
        source = self.root / (stem + ".json")
        write_json(source, packet)
        original = source.read_bytes()
        output = self.root / (stem + "-plan")
        plan_scene_queries(source, output)
        report = read_json(output / "query-plan.json")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(report["execution"], "NONE")
        self.assertEqual(report["api_spend_usd"], 0)
        jsonschema.validate(report, self.schema)
        verify_run(output)
        return report, bounds

    def test_all_identity_routes_plan_short_executable_intervals(self):
        for proposition in self.PROPOSITIONS:
            with self.subTest(proposition=proposition):
                report, bounds = self.music_plan(proposition, 30)
                self.assertEqual(report["open_requests"], [])
                self.assertEqual(len(report["requests"]), 1)
                request = report["requests"][0]
                self.assertEqual(request["route"], "MUSIC_IDENTITY")
                self.assertEqual(request["interval_seconds"], bounds)
                self.assertEqual(request["audio_stream"], 3)
                self.assertFalse(request["api_submission_authorized"])

    def test_all_identity_routes_admit_exact_120_second_boundary(self):
        for proposition in self.PROPOSITIONS:
            with self.subTest(proposition=proposition):
                report, bounds = self.music_plan(proposition, 120)
                self.assertEqual(report["open_requests"], [])
                self.assertEqual(len(report["requests"]), 1)
                self.assertEqual(report["requests"][0]["interval_seconds"], bounds)

    def test_music_stream_contract_rejects_invalid_selection_declarations(self):
        report, _ = self.music_plan("recording_identity", 30)
        for mutation in ({"audio_stream": None}, {"video_stream": 0},
                         {"stream_selection_status": "OPEN_SELECTION_REQUIRED"}):
            with self.subTest(mutation=mutation):
                import copy
                invalid = copy.deepcopy(report)
                invalid["requests"][0].update(mutation)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(invalid, self.schema)

    def test_oversized_identity_routes_remain_open_without_arbitrary_splitting(self):
        for proposition in self.PROPOSITIONS:
            with self.subTest(proposition=proposition):
                report, bounds = self.music_plan(proposition, 120.001)
                self.assertEqual(report["requests"], [])
                self.assertEqual(len(report["open_requests"]), 1)
                unresolved = report["open_requests"][0]
                self.assertEqual(unresolved["claim_id"], "c-0")
                self.assertEqual(unresolved["route"], "MUSIC_IDENTITY")
                self.assertEqual(unresolved["status"], "OPEN_REFINE_INTERVAL")
                self.assertEqual(unresolved["source_object_interval_seconds"], bounds)
                self.assertEqual(report["planned_resources"], {"frames": 0, "audio_seconds": 0.0})


if __name__ == "__main__":
    unittest.main()
