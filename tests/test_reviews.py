import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from avevidence.common import AVError, file_record, finish_run, output_transaction, sha256, write_json
from avevidence.inventory import inventory, verify_run
from avevidence.reviews import validate_reviews, write_review_templates


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.scratch_parent = Path(tempfile.gettempdir()).resolve()
        self.root = Path(tempfile.mkdtemp(prefix="ave-reviews-test-", dir=self.scratch_parent)).resolve()
        self.assertTrue(self.root.is_relative_to(self.scratch_parent))
        self.assertTrue(self.root.name.startswith("ave-reviews-test-"))
        self.env = patch("avevidence.common.environment", return_value={"testing": True}); self.env.start()
        self.media = self.root / "source.bin"; self.media.write_bytes(b"synthetic media fixture only")
        self.receipt = self.root / "receipt.txt"
        self.receipt.write_text("SYNTHETIC UNIT TEST RECEIPT. No actual media review or playback occurred.")
        self.config = self.root / "config.json"
        self.record_path = self.root / "reviews.json"; self.cap_path = self.root / "capabilities.json"
        self.counter = 0
        self.build_inventory()

    def tearDown(self):
        self.env.stop()
        target = self.root.resolve()
        self.assertEqual(target, self.root)
        self.assertNotEqual(target, self.scratch_parent)
        self.assertTrue(target.is_relative_to(self.scratch_parent))
        self.assertTrue(target.name.startswith("ave-reviews-test-"))
        shutil.rmtree(target)

    def probe(self, path):
        p = Path(path)
        return {"schema": "ave.source.v1", "path": str(p.resolve()), "sha256": sha256(p), "size_bytes": p.stat().st_size,
                "duration_seconds": 10.0, "origin_seconds": 0.0,
                "streams": [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}], "probe": {}}

    def build_inventory(self, modalities=None, points=None, alternate=False):
        modalities = modalities or ["audio", "motion"]
        row = {"logical_source_id": "source-one", "materialization_id": "capture-one", "path": str(self.media),
               "selected_streams": {"audio": 1, "video": 0}, "required_modalities": modalities, "preferred": True}
        if points is not None:
            row["required_points_seconds"] = points
        rows = [row]
        if alternate:
            rows.append(dict(row, materialization_id="capture-two", preferred=False))
        self.config.write_text(json.dumps({"sources": rows}))
        self.inventory_path = self.root / f"inventory-{len(list(self.root.glob('inventory-*')))}"
        with patch("avevidence.inventory.probe_source", side_effect=self.probe):
            run = inventory(self.config, self.inventory_path)
        self.source = run["sources"][0]

    def records(self, modalities=None):
        modalities = modalities or ["audio", "motion"]
        reviewer = {"id": "synthetic-reviewer", "type": "human", "name": "Synthetic test actor"}
        receipt = {"kind": "human_attestation", "path": str(self.receipt), "sha256": sha256(self.receipt)}
        caps, presentations, records = [], [], []
        for m in modalities:
            mechanism = {"audio": "audio_playback", "motion": "video_playback", "visual_stills": "image_view"}[m]
            cap = {"capability_id": "cap-"+m, "reviewer": copy.deepcopy(reviewer), "modality": m, "mechanism": mechanism,
                   "verified_at": "2025-01-01T00:00:00Z", "receipt": copy.deepcopy(receipt)}
            base = {"logical_source_id": "source-one", "materialization_id": "capture-one", "source_sha256": self.source["sha256"],
                    "stream_index": 1 if m == "audio" else 0, "modality": m, "capability_id": cap["capability_id"], "evidence": {"kind": "source"}}
            base["points_seconds" if m == "visual_stills" else "intervals_seconds"] = [2, 7] if m == "visual_stills" else [[0, 10]]
            presentation = dict(copy.deepcopy(base), presentation_id="presentation-"+m, reviewer_id=reviewer["id"], reviewer_type=reviewer["type"],
                                mechanism=mechanism, presented_at="2025-01-01T00:01:00Z", receipt=copy.deepcopy(receipt))
            record = dict(copy.deepcopy(base), schema="ave.review.v1", review_id="review-"+m, reviewer=copy.deepcopy(reviewer),
                          reviewed_at="2025-01-01T00:02:00Z", presentation_id=presentation["presentation_id"],
                          observation="Synthetic declaration for regression testing; no actual character media was reviewed.")
            caps.append(cap); presentations.append(presentation); records.append(record)
        return records, {"schema": "ave.capabilities.v1", "capabilities": caps, "presentations": presentations}

    def validate(self, records, caps):
        self.record_path.write_text(json.dumps(records), encoding="utf-8")
        self.cap_path.write_text(json.dumps(caps), encoding="utf-8")
        self.counter += 1
        path = self.root / f"review-run-{self.counter}"
        run = validate_reviews(self.record_path, self.inventory_path, self.cap_path, path)
        return run, json.loads((path / "review_validation.json").read_text())

    def derivative(self, segments=None, *, still=False):
        self.counter += 1
        path = self.root / f"derivative-{self.counter}"
        with output_transaction(path, [self.media]) as stage:
            if still:
                artifact = "frame.png"; (stage/artifact).write_bytes(b"synthetic image bytes")
                h = sha256(stage/artifact)
                rows = [{"frame_id": "F000001", "path": artifact, "sha256": h, "source_sha256": self.source["sha256"], "stream_index": 0, "source_seconds": 2.0}]
                write_json(stage/"frames.json", {"rows": rows})
                mapping = {"kind": "still_points", "parent_source_sha256": self.source["sha256"], "parent_stream_index": 0,
                           "frames": [{"frame_id": "F000001", "artifact_path": artifact, "artifact_sha256": h, "source_seconds": 2.0}]}
                result = "frames.json"; operation = "extract_frames"
            else:
                artifact = "audio.wav"; (stage/artifact).write_bytes(b"synthetic audio bytes")
                h = sha256(stage/artifact)
                mapping = {"parent_source_sha256": self.source["sha256"], "parent_stream_index": 1, "modality": "audio", "derivative_stream_index": 0,
                           "artifact_kind": "audio_pcm", "artifact_path": artifact, "artifact_sha256": h,
                           "segments": segments or [{"parent_start_seconds": 0, "parent_end_seconds": 10, "derivative_start_seconds": 0, "derivative_end_seconds": 10}]}
                write_json(stage/"audio.json", {"synthetic": True}); result = "audio.json"; operation = "extract_audio"
            finish_run(stage, operation, [self.source], metadata={"result_file": result, "review_mapping": mapping})
        ev = {"kind": "derivative", "run_manifest_path": str(path/"run.json"), "run_manifest_sha256": sha256(path/"run.json"), "artifact_path": artifact, "artifact_sha256": h}
        if still:
            ev["frame_ids"] = ["F000001"]
        else:
            ev.update(derivative_stream_index=0, derivative_intervals_seconds=[[0, 10]])
        return ev, path

    def test_full_means_declared_required_modalities_not_perception_truth(self):
        run, report = self.validate(*self.records())
        self.assertTrue(run["metadata"]["full_required_coverage"])
        self.assertEqual(report["status"], "FULL_DECLARED_COVERAGE")
        self.assertEqual(report["perception_truth"], "NOT_ESTABLISHED_BY_VALIDATION")
        self.assertIn("external", report["trust_boundary"])

    def test_audio_only_cannot_fill_motion_requirement(self):
        run, report = self.validate(*self.records(["audio"]))
        self.assertFalse(run["metadata"]["full_required_coverage"])
        missing = report["coverage"][0]["modalities"][1]["missing_intervals_seconds"]
        self.assertEqual(missing, [[0.0, 10.0]])

    def test_union_does_not_double_count_overlap_or_hide_gaps(self):
        records, caps = self.records(["audio"])
        records[0]["intervals_seconds"] = [[0, 4], [2, 6], [8, 10]]
        _, report = self.validate(records, caps)
        audio = report["coverage"][0]["modalities"][0]
        self.assertEqual(audio["declared_union_intervals_seconds"], [[0.0, 6.0], [8.0, 10.0]])
        self.assertEqual(audio["missing_intervals_seconds"], [[6.0, 8.0]])
        self.assertEqual(audio["declared_seconds_within_requirements"], 8)

    def test_missing_presentation_is_rejected(self):
        records, caps = self.records(); caps["presentations"] = []
        with self.assertRaisesRegex(AVError, "capability and presentation"):
            self.validate(records, caps)

    def test_capability_receipt_missing_file_is_rejected(self):
        records, caps = self.records(); caps["capabilities"][0]["receipt"]["path"] = str(self.root/"absent.txt")
        with self.assertRaisesRegex(AVError, "Missing receipt"):
            self.validate(records, caps)

    def test_receipt_hash_not_just_nonempty_field(self):
        records, caps = self.records(); caps["presentations"][0]["receipt"]["sha256"] = "0123456789abcdef"*4
        with self.assertRaisesRegex(AVError, "Receipt hash"):
            self.validate(records, caps)

    def test_capability_mechanism_cannot_be_asr(self):
        records, caps = self.records(); caps["capabilities"][0]["mechanism"] = "speech_to_text"
        with self.assertRaisesRegex(AVError, "mechanism"):
            self.validate(records, caps)

    def test_model_requires_tool_receipt(self):
        records, caps = self.records(["audio"])
        records[0]["reviewer"]["type"] = "model"; caps["capabilities"][0]["reviewer"]["type"] = "model"; caps["presentations"][0]["reviewer_type"] = "model"
        with self.assertRaisesRegex(AVError, "Model capabilities"):
            self.validate(records, caps)

    def test_cross_reviewer_presentation_is_rejected(self):
        records, caps = self.records(); caps["presentations"][0]["reviewer_id"] = "another-person"
        with self.assertRaisesRegex(AVError, "not bound"):
            self.validate(records, caps)

    def test_wrong_source_hash_or_materialization_or_stream(self):
        for key, value in [("source_sha256", "0123456789abcdef"*4), ("materialization_id", "unknown"), ("stream_index", 0)]:
            records, caps = self.records(); records[0][key] = value
            with self.subTest(key=key), self.assertRaises(AVError):
                self.validate(records, caps)

    def test_duplicate_review_ids_rejected(self):
        records, caps = self.records(); records[1]["review_id"] = records[0]["review_id"]
        with self.assertRaisesRegex(AVError, "Duplicate review"):
            self.validate(records, caps)

    def test_duplicate_capability_and_presentation_ids_rejected(self):
        for collection in ("capabilities", "presentations"):
            records, caps = self.records(); caps[collection].append(copy.deepcopy(caps[collection][0]))
            with self.subTest(collection=collection), self.assertRaisesRegex(AVError, "Duplicate"):
                self.validate(records, caps)

    def test_timezone_date_and_json_number_types_are_strict(self):
        for value in ("2025-01-01", "2025-01-01T00:02:00", "2025-02-31T00:02:00Z", 123):
            records, caps = self.records(); records[0]["reviewed_at"] = value
            with self.subTest(value=value), self.assertRaises(AVError):
                self.validate(records, caps)
        for value in (True, "0", float("nan")):
            records, caps = self.records(); records[0]["intervals_seconds"][0][0] = value
            with self.subTest(value=value), self.assertRaises(AVError):
                self.validate(records, caps)

    def test_review_cannot_predate_presentation(self):
        records, caps = self.records(); records[0]["reviewed_at"] = "2024-12-31T00:00:00Z"
        with self.assertRaisesRegex(AVError, "predates presentation"):
            self.validate(records, caps)

    def test_placeholder_observation_or_hash_rejected(self):
        for key, value in [("observation", "TODO"), ("source_sha256", "0"*64), ("review_id", "REPLACE_REVIEW_ID")]:
            records, caps = self.records(); records[0][key] = value
            with self.subTest(key=key), self.assertRaises(AVError):
                self.validate(records, caps)

    def test_claimed_interval_must_have_been_presented(self):
        records, caps = self.records(); caps["presentations"][0]["intervals_seconds"] = [[0, 5]]
        with self.assertRaisesRegex(AVError, "exceed"):
            self.validate(records, caps)

    def test_metrics_and_asr_evidence_are_not_listening(self):
        for kind in ("metrics", "asr", "visual_stills"):
            records, caps = self.records(); caps["presentations"][0]["evidence"] = {"kind": kind}
            with self.subTest(kind=kind), self.assertRaisesRegex(AVError, "cannot stand in"):
                self.validate(records, caps)

    def test_derivative_hash_and_mapping_are_verified(self):
        evidence, path = self.derivative()
        records, caps = self.records(["audio"])
        records[0]["evidence"] = copy.deepcopy(evidence); caps["presentations"][0]["evidence"] = copy.deepcopy(evidence)
        _, report = self.validate(records, caps)
        self.assertTrue(report["coverage"][0]["modalities"][0]["full_declared_coverage"])
        (path/"audio.wav").write_bytes(b"altered derivative")
        with self.assertRaisesRegex(AVError, "Artifact verification"):
            self.validate(records, caps)

    def test_derivative_gaps_stay_gaps_in_parent_coverage(self):
        segments = [{"parent_start_seconds": 0, "parent_end_seconds": 3, "derivative_start_seconds": 0, "derivative_end_seconds": 3},
                    {"parent_start_seconds": 6, "parent_end_seconds": 10, "derivative_start_seconds": 3, "derivative_end_seconds": 7}]
        evidence, path = self.derivative(segments); evidence["derivative_intervals_seconds"] = [[0, 7]]
        records, caps = self.records(["audio"])
        for item in (records[0], caps["presentations"][0]):
            item["evidence"] = copy.deepcopy(evidence); item["intervals_seconds"] = [[0, 3], [6, 10]]
        _, report = self.validate(records, caps)
        self.assertEqual(report["coverage"][0]["modalities"][0]["missing_intervals_seconds"], [[3.0, 6.0]])
        records[0]["intervals_seconds"] = [[0, 10]]
        with self.assertRaisesRegex(AVError, "mapping"):
            self.validate(records, caps)

    def test_unmapped_derivative_padding_is_not_coverage(self):
        segments = [{"parent_start_seconds": 0, "parent_end_seconds": 3, "derivative_start_seconds": 0, "derivative_end_seconds": 3},
                    {"parent_start_seconds": 6, "parent_end_seconds": 10, "derivative_start_seconds": 5, "derivative_end_seconds": 9}]
        evidence, _ = self.derivative(segments); evidence["derivative_intervals_seconds"] = [[0, 9]]
        records, caps = self.records(["audio"])
        for item in (records[0], caps["presentations"][0]):
            item["evidence"] = copy.deepcopy(evidence); item["intervals_seconds"] = [[0, 3], [6, 10]]
        with self.assertRaisesRegex(AVError, "unmapped"):
            self.validate(records, caps)

    def test_wrong_derivative_stream_rejected(self):
        evidence, _ = self.derivative(); evidence["derivative_stream_index"] = 1
        records, caps = self.records(["audio"])
        for item in (records[0], caps["presentations"][0]): item["evidence"] = copy.deepcopy(evidence)
        with self.assertRaisesRegex(AVError, "No unique derivative"):
            self.validate(records, caps)

    def test_metrics_run_cannot_gain_listening_credit_by_adding_mapping(self):
        evidence, path = self.derivative()
        manifest = json.loads((path/"run.json").read_text()); manifest["operation"] = "measure_audio"
        (path/"run.json").write_text(json.dumps(manifest)); evidence["run_manifest_sha256"] = sha256(path/"run.json")
        records, caps = self.records(["audio"])
        for item in (records[0], caps["presentations"][0]): item["evidence"] = copy.deepcopy(evidence)
        with self.assertRaisesRegex(AVError, "Only media-producing"):
            self.validate(records, caps)

    def test_still_points_are_partial_and_never_motion_intervals(self):
        self.build_inventory(["visual_stills"], [2, 7])
        evidence, _ = self.derivative(still=True)
        records, caps = self.records(["visual_stills"])
        for item in (records[0], caps["presentations"][0]):
            item["evidence"] = copy.deepcopy(evidence); item["points_seconds"] = [2]
        _, report = self.validate(records, caps)
        self.assertFalse(report["full_required_coverage"])
        self.assertEqual(report["coverage"][0]["modalities"][0]["missing_points_seconds"], [7.0])
        records[0]["intervals_seconds"] = [[0, 10]]
        with self.assertRaisesRegex(AVError, "cannot declare temporal"):
            self.validate(records, caps)

    def test_unknown_still_frame_id_rejected(self):
        self.build_inventory(["visual_stills"], [2, 7]); evidence, _ = self.derivative(still=True)
        evidence["frame_ids"] = ["F999999"]
        records, caps = self.records(["visual_stills"])
        for item in (records[0], caps["presentations"][0]): item["evidence"] = copy.deepcopy(evidence)
        with self.assertRaisesRegex(AVError, "not represented"):
            self.validate(records, caps)

    def test_contact_sheet_parent_mapping_survives_portable_bundle_relocation(self):
        self.build_inventory(["visual_stills"], [2])
        frame_evidence, frames = self.derivative(still=True)
        contacts = self.root/"contacts"
        parent_link = {"run_manifest_path": str(frames/"run.json"), "run_manifest_sha256": sha256(frames/"run.json")}
        with output_transaction(contacts, [frames]) as stage:
            (stage/"sheet.png").write_bytes(b"synthetic contact sheet bytes")
            digest = sha256(stage/"sheet.png")
            sheets = [{"path": "sheet.png", "sha256": digest, "frame_ids": ["F000001"]}]
            write_json(stage/"contacts.json", {"sheets": sheets})
            mapping = {"kind": "contact_sheet_points", "parent_source_sha256": self.source["sha256"], "parent_stream_index": 0,
                       "parent_frame_run": parent_link, "sheets": [{"artifact_path": "sheet.png", "artifact_sha256": digest, "frame_ids": ["F000001"]}]}
            finish_run(stage, "contact_sheets", [file_record(frames/"run.json")], metadata={"result_file": "contacts.json", "parent_frame_run": parent_link, "review_mapping": mapping})
        evidence = {"kind": "derivative", "run_manifest_path": str(contacts/"run.json"), "run_manifest_sha256": sha256(contacts/"run.json"),
                    "artifact_path": "sheet.png", "artifact_sha256": digest, "frame_ids": ["F000001"]}
        bundle = self.root/"portable"; bundle.mkdir()
        for source, name in [(self.inventory_path, "inventory"), (frames, "frames"), (contacts, "contacts")]:
            shutil.copytree(source, bundle/name)
        artifacts = [{"path": p.relative_to(bundle).as_posix(), "sha256": sha256(p), "size_bytes": p.stat().st_size} for p in bundle.rglob("*") if p.is_file()]
        (bundle/"run.json").write_text(json.dumps({"schema": "ave.run.v1", "operation": "bundle", "status": "GENERATED_NOT_REVIEWED",
            "created_at": "2025-01-01T00:00:00Z", "sources": [], "metadata": {}, "artifacts": artifacts}))
        for source in (frames, contacts):
            target = source.with_name(source.name+"-retired").resolve()
            self.assertTrue(source.resolve().is_relative_to(self.root)); self.assertTrue(target.is_relative_to(self.root))
            source.rename(target)
        self.assertEqual(verify_run(bundle)["structural_validity"], "VALID")
        self.assertTrue(verify_run(bundle, verify_sources=True)["original_sources_verified"])
        moved = self.root/"portable-moved"
        self.assertTrue(bundle.resolve().is_relative_to(self.root)); self.assertTrue(moved.resolve().is_relative_to(self.root))
        bundle.rename(moved); bundle = moved
        self.assertTrue(verify_run(bundle, verify_sources=True)["original_sources_verified"])
        self.inventory_path = bundle/"inventory"
        self.media.unlink()
        with self.assertRaisesRegex(AVError, "source verification"):
            verify_run(bundle, verify_sources=True)
        records, caps = self.records(["visual_stills"])
        for item in (records[0], caps["presentations"][0]):
            item["evidence"] = copy.deepcopy(evidence); item["points_seconds"] = [2]
        _, report = self.validate(records, caps)
        self.assertTrue(report["full_required_coverage"])

    def test_receipts_are_archived_and_portable_declarations_revalidate(self):
        _, report = self.validate(*self.records())
        original_output = self.root/f"review-run-{self.counter}"
        self.receipt.unlink()
        self.assertTrue(report["receipt_artifacts"])
        run = validate_reviews(original_output/"reviews.json", self.inventory_path,
                               original_output/"capabilities.json", self.root/"archived-revalidation")
        self.assertTrue(run["metadata"]["full_required_coverage"])

    def test_alternate_materialization_does_not_fill_preferred_clock(self):
        self.build_inventory(alternate=True)
        records, caps = self.records()
        for item in records + caps["presentations"]: item["materialization_id"] = "capture-two"
        _, report = self.validate(records, caps)
        self.assertFalse(report["full_required_coverage"])

    def test_portable_review_validation_keeps_original_media_optional(self):
        self.media.unlink()
        _, report = self.validate(*self.records())
        self.assertTrue(report["structural_valid"])
        self.assertTrue(verify_run(self.root/f"review-run-{self.counter}")["artifact_count"])

    def test_templates_remain_invalid_until_human_fills_them(self):
        path = self.root/"templates"; write_review_templates(path)
        with self.assertRaises(AVError):
            validate_reviews(path/"reviews.json", self.inventory_path, path/"capabilities.json", self.root/"invalid-review")

    def test_jsonl_records_supported(self):
        records, caps = self.records(); self.record_path = self.root/"reviews.jsonl"
        self.record_path.write_text("\n".join(json.dumps(x) for x in records))
        self.cap_path.write_text(json.dumps(caps))
        run = validate_reviews(self.record_path, self.inventory_path/"inventory.json", self.cap_path, self.root/"jsonl-run")
        self.assertTrue(run["metadata"]["full_required_coverage"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg integration dependencies unavailable")
    def test_actual_synthetic_audio_clip_frame_contact_producer_integration(self):
        try:
            import numpy  # noqa: F401
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("NumPy/Pillow integration dependencies unavailable")
        from avevidence.common import run as command
        from avevidence.audio import extract_audio
        from avevidence.clips import clip_av
        from avevidence.visual import extract_frames, contact_sheets
        actual = self.root/"synthetic.mkv"
        command(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=10:d=1",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=1", "-c:v", "ffv1", "-c:a", "pcm_s16le", actual])
        self.media = actual
        config = {"sources": [{"logical_source_id": "source-one", "materialization_id": "capture-one", "path": str(actual),
                 "selected_streams": {"audio": 1, "video": 0}, "required_modalities": ["audio", "motion", "visual_stills"],
                 "required_intervals_seconds": {"audio": [[0, 1]], "motion": [[0.2, 0.8]]}, "required_points_seconds": [0.2, 0.7]}]}
        self.config.write_text(json.dumps(config)); self.inventory_path = self.root/"real-producer-inventory"
        admitted = inventory(self.config, self.inventory_path); self.source = admitted["sources"][0]
        audio_dir = self.root/"real-audio"; audio = extract_audio(actual, audio_dir, stream_index=1)
        clip_dir = self.root/"real-av"; clip = clip_av(actual, clip_dir, start=0.2, end=0.8, video_stream=0, audio_stream=1)
        frames_dir = self.root/"real-frames"
        extract_frames(actual, frames_dir, mode="exact", timestamps=[0.2, 0.7], stream_index=0, width=32)
        contact_dir = self.root/"real-contacts"; contact = contact_sheets(frames_dir, contact_dir, columns=2, rows=1, thumb_width=32)
        for path in (audio_dir, clip_dir, frames_dir, contact_dir): verify_run(path)
        records, caps = self.records(["audio", "motion", "visual_stills"])
        for record, presentation in zip(records, caps["presentations"]):
            m = record["modality"]
            if m == "visual_stills":
                mapping = contact["metadata"]["review_mapping"]; member = mapping["sheets"][0]; parent = contact_dir
                ev = {"kind": "derivative", "run_manifest_path": str(parent/"run.json"), "run_manifest_sha256": sha256(parent/"run.json"),
                      "artifact_path": member["artifact_path"], "artifact_sha256": member["artifact_sha256"], "frame_ids": member["frame_ids"]}
                for item in (record, presentation): item["points_seconds"] = [0.2, 0.7]
            else:
                mapping = audio["metadata"]["review_mapping"] if m == "audio" else next(x for x in clip["metadata"]["review_mappings"] if x["modality"] == "motion")
                parent = audio_dir if m == "audio" else clip_dir
                ev = {"kind": "derivative", "run_manifest_path": str(parent/"run.json"), "run_manifest_sha256": sha256(parent/"run.json"),
                      "artifact_path": mapping["artifact_path"], "artifact_sha256": mapping["artifact_sha256"], "derivative_stream_index": mapping["derivative_stream_index"],
                      "derivative_intervals_seconds": [[x["derivative_start_seconds"], x["derivative_end_seconds"]] for x in mapping["segments"]]}
                for item in (record, presentation): item["intervals_seconds"] = [[x["parent_start_seconds"], x["parent_end_seconds"]] for x in mapping["segments"]]
            for item in (record, presentation): item["evidence"] = copy.deepcopy(ev)
        _, report = self.validate(records, caps)
        self.assertTrue(report["full_required_coverage"])
        self.assertEqual(report["perception_truth"], "NOT_ESTABLISHED_BY_VALIDATION")


if __name__ == "__main__":
    unittest.main()
