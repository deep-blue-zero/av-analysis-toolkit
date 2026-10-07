"""Regression coverage for the 1.1.0-rc1 hardening specification."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from avevidence.audio import extract_audio
from avevidence.bundle import build_bundle
from avevidence.clips import _windows
from avevidence.common import AVError, read_json, run, sha256
from avevidence.external import admit_external_clip
from avevidence.inventory import inventory
from avevidence.reviews import validate_reviews
from avevidence.visual import build_frame_index, extract_frames


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe required")
class HardeningIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ave-hardening-tests-")
        cls.root = Path(cls.temp.name).resolve()
        cls.source = cls.root / "source.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=128x96:rate=10:duration=6", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=48000:duration=6", "-c:v", "ffv1",
             "-g", "10", "-c:a", "flac", cls.source])
        cls.clip = cls.root / "losslesscut-like.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-ss", "1.2", "-to", "3.0",
             "-i", cls.source, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", cls.clip])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def path(self, name):
        return self.root / name

    def test_ffmpeg_71_missing_frame_duration_uses_pts_and_endpoint(self):
        source = {"path": str(self.source), "origin_seconds": 0.0,
                  "stream_timeline_bounds": [{"stream_index": 0, "end_seconds": 0.3}]}
        stream = {"index": 0, "codec_type": "video", "time_base": "1/10"}
        payload = {"frames": [{"best_effort_timestamp_time": "0.0"},
                               {"best_effort_timestamp_time": "0.1"},
                               {"best_effort_timestamp_time": "0.2"}]}
        with patch("avevidence.clips.run", return_value=SimpleNamespace(stdout=json.dumps(payload))):
            windows, _, _, extent = _windows(source, stream)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0][0], 0.0)
        self.assertAlmostEqual(windows[0][1], 0.3)
        self.assertEqual(extent["method_counts"]["next_decoded_pts_delta"], 2)
        self.assertEqual(extent["method_counts"]["verified_stream_endpoint"], 1)
        self.assertFalse(extent["final_frame_extent_estimated"])

    def test_frame_index_cache_seek_matches_uncached_output_and_refuses_mismatch(self):
        index = self.path("frame-index")
        build_frame_index(self.source, index)
        cached = self.path("cached-frames")
        direct = self.path("direct-frames")
        extract_frames(self.source, cached, mode="dense", start=5.0, end=5.5, fps=5,
                       frame_index_run=index, seek_preroll=1.0)
        extract_frames(self.source, direct, mode="dense", start=5.0, end=5.5, fps=5,
                       input_seek=False)
        a, b = read_json(cached / "frames.json"), read_json(direct / "frames.json")
        self.assertEqual([x["source_pts"] for x in a["rows"]], [x["source_pts"] for x in b["rows"]])
        self.assertEqual([x["sha256"] for x in a["rows"]], [x["sha256"] for x in b["rows"]])
        self.assertEqual(a["decode_strategy"]["used"], "bounded_input_seek")
        self.assertIsNotNone(a["frame_index_cache"])
        other = self.path("other.mkv")
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=128x96:rate=10:duration=2", "-c:v", "ffv1", other])
        with self.assertRaisesRegex(AVError, "source hash"):
            extract_frames(other, self.path("bad-cache"), mode="exact", timestamps=[1], frame_index_run=index)

    def test_practical_storage_is_compact_and_forensic_remains_explicit(self):
        bundle = self.path("practical-bundle")
        build_bundle(self.source, bundle, interval=2, audio_storage="practical")
        audio = read_json(bundle / "audio" / "audio.json")
        self.assertEqual(audio["storage_profile"], "practical")
        self.assertEqual(audio["artifact_path"], "audio.flac")
        self.assertFalse(any(p.suffix.lower() == ".wav" for p in (bundle / "audio").iterdir()))
        forensic = self.path("forensic-audio")
        extract_audio(self.source, forensic, storage_profile="forensic")
        receipt = read_json(forensic / "audio.json")
        self.assertEqual(receipt["storage_profile"], "forensic")
        self.assertTrue((forensic / "audio.wav").is_file())

    def test_external_stream_copy_is_mapped_and_review_validator_accepts_it(self):
        admitted = self.path("admitted-clip")
        admit_external_clip(self.source, self.clip, admitted, parent_start=1.2, parent_end=3.0)
        receipt = read_json(admitted / "external_clip.json")
        self.assertEqual(receipt["overall_status"], "VERIFIED_ALL_SELECTED_MODALITIES")
        motion = next(x for x in receipt["review_mappings"] if x["modality"] == "motion")
        self.assertTrue(motion["claimed_interval_covered"])

        config = self.path("review-inventory-config.json")
        config.write_text(json.dumps({"schema": "ave.inventory.config.v1",
            "required_logical_source_ids": ["parent"],
            "sources": [{"logical_source_id": "parent", "materialization_id": "parent-file",
                         "path": str(self.source), "selected_streams": {"video": 0},
                         "required_modalities": ["motion"],
                         "required_intervals_seconds": {"motion": [[1.2, 2.0]]}, "preferred": True}]}),
                         encoding="utf-8")
        inventory_run = self.path("review-inventory")
        inv = inventory(config, inventory_run)
        source = inv["sources"][0]
        segment = motion["segments"][0]
        offset = segment["parent_start_seconds"] - segment["derivative_start_seconds"]
        derivative_interval = [[1.2 - offset, 2.0 - offset]]
        evidence = {"kind": "derivative", "run_manifest_path": str(admitted / "run.json"),
                    "run_manifest_sha256": sha256(admitted / "run.json"),
                    "artifact_path": motion["artifact_path"], "artifact_sha256": motion["artifact_sha256"],
                    "derivative_stream_index": motion["derivative_stream_index"],
                    "derivative_intervals_seconds": derivative_interval}
        attestation = self.path("attestation.txt")
        attestation.write_text("Synthetic regression declaration; no actual media review occurred.", encoding="utf-8")
        reviewer = {"id": "synthetic-human", "type": "human", "name": "Synthetic Test Human"}
        receipt_ref = {"kind": "human_attestation", "path": str(attestation), "sha256": sha256(attestation)}
        capability = {"capability_id": "motion-cap", "reviewer": copy.deepcopy(reviewer),
                      "modality": "motion", "mechanism": "video_playback",
                      "verified_at": "2026-01-01T00:00:00Z", "receipt": copy.deepcopy(receipt_ref)}
        presentation = {"presentation_id": "motion-presentation", "capability_id": "motion-cap",
                        "reviewer_id": reviewer["id"], "reviewer_type": reviewer["type"],
                        "modality": "motion", "mechanism": "video_playback",
                        "presented_at": "2026-01-01T00:01:00Z", "logical_source_id": "parent",
                        "materialization_id": "parent-file", "source_sha256": source["sha256"],
                        "stream_index": 0, "intervals_seconds": [[1.2, 2.0]],
                        "evidence": copy.deepcopy(evidence), "receipt": copy.deepcopy(receipt_ref)}
        review = {"schema": "ave.review.v1", "review_id": "motion-review",
                  "reviewer": copy.deepcopy(reviewer), "reviewed_at": "2026-01-01T00:02:00Z",
                  "logical_source_id": "parent", "materialization_id": "parent-file",
                  "source_sha256": source["sha256"], "stream_index": 0, "modality": "motion",
                  "capability_id": "motion-cap", "presentation_id": "motion-presentation",
                  "intervals_seconds": [[1.2, 2.0]], "evidence": copy.deepcopy(evidence),
                  "observation": "Synthetic structural validation only; no perceptual claim is made."}
        caps = self.path("external-capabilities.json")
        records = self.path("external-reviews.json")
        caps.write_text(json.dumps({"schema": "ave.capabilities.v1", "capabilities": [capability],
                                    "presentations": [presentation]}), encoding="utf-8")
        records.write_text(json.dumps([review]), encoding="utf-8")
        result = validate_reviews(records, inventory_run, caps, self.path("external-review-check"))
        self.assertTrue(result["metadata"]["full_required_coverage"])

    def test_reencoded_external_clip_is_not_packet_verified(self):
        changed = self.path("reencoded.mkv")
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", self.clip,
             "-c:v", "ffv1", "-c:a", "pcm_s16le", changed])
        out = self.path("reencoded-admission")
        admit_external_clip(self.source, changed, out, parent_start=1.2, parent_end=3.0)
        result = read_json(out / "external_clip.json")
        self.assertNotEqual(result["overall_status"], "VERIFIED_ALL_SELECTED_MODALITIES")
        self.assertFalse(any(x["status"] == "VERIFIED_PACKET_SUBSEQUENCE" for x in result["modalities"]))

    def test_external_packet_identity_does_not_validate_a_wrong_claimed_interval(self):
        out = self.path("wrong-claim-admission")
        admit_external_clip(self.source, self.clip, out, parent_start=3.5, parent_end=4.0)
        result = read_json(out / "external_clip.json")
        self.assertEqual(result["overall_status"], "PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED")
        self.assertTrue(all(x["status"] == "VERIFIED_PACKET_SUBSEQUENCE" for x in result["modalities"]))
        self.assertTrue(all(not x["claimed_interval_covered"] for x in result["modalities"]))


if __name__ == "__main__":
    unittest.main()
