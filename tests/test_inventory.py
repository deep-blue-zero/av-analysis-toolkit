import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from avevidence.common import AVError, sha256
from avevidence.inventory import inventory, verify_run


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.scratch_parent = Path(tempfile.gettempdir()).resolve()
        self.root = Path(tempfile.mkdtemp(prefix="ave-inventory-test-", dir=self.scratch_parent)).resolve()
        self.assertTrue(self.root.is_relative_to(self.scratch_parent))
        self.assertTrue(self.root.name.startswith("ave-inventory-test-"))
        self.media = self.root / "fixture.bin"
        self.media.write_bytes(b"synthetic fixture; never a performed character review")
        self.config = self.root / "config.json"
        self.env = patch("avevidence.common.environment", return_value={"testing": True})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        target = self.root.resolve()
        self.assertEqual(target, self.root)
        self.assertNotEqual(target, self.scratch_parent)
        self.assertTrue(target.is_relative_to(self.scratch_parent))
        self.assertTrue(target.name.startswith("ave-inventory-test-"))
        shutil.rmtree(target)

    def source(self, path):
        path = Path(path)
        return {"schema": "ave.source.v1", "path": str(path.resolve()), "sha256": sha256(path),
                "size_bytes": path.stat().st_size, "duration_seconds": 10.0, "origin_seconds": 2.0,
                "streams": [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}, {"index": 2, "codec_type": "audio"}], "probe": {}}

    def row(self, **changes):
        row = {"logical_source_id": "source-A", "materialization_id": "capture-A",
               "path": "fixture.bin", "selected_streams": {"audio": 1, "video": 0},
               "required_modalities": ["audio", "motion"]}
        row.update(changes)
        return row

    def admit(self, rows=None, **top):
        data = {"sources": rows or [self.row()]}
        data.update(top)
        self.config.write_text(json.dumps(data), encoding="utf-8")
        with patch("avevidence.inventory.probe_source", side_effect=self.source):
            result = inventory(self.config, self.root / "run")
        return result

    def rewrite(self, manifest):
        (self.root / "run" / "run.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_admits_explicit_absolute_stream_and_config_completeness(self):
        run = self.admit([self.row(selected_streams={"audio": 2}) | {"required_modalities": ["audio"]}])
        self.assertEqual(run["sources"][0]["selected_streams"], {"audio": 2})
        self.assertEqual(run["sources"][0]["required_intervals_seconds"], {"audio": [[0.0, 10.0]]})
        report = verify_run(self.root / "run", verify_sources=True)
        self.assertEqual(report["structural_validity"], "VALID")
        self.assertEqual(report["perception_truth"], "NOT_ESTABLISHED_BY_VALIDATION")

    def test_identical_copies_and_logical_materializations_get_one_weight(self):
        rows = [self.row(preferred=True), self.row(materialization_id="capture-B", preferred=False),
                self.row(logical_source_id="alias-of-A", materialization_id="capture-C")]
        self.admit(rows)
        inv = json.loads((self.root / "run" / "inventory.json").read_text())
        self.assertEqual(len(inv["sources"]), 3)
        self.assertEqual(len(inv["evidence_weight_groups"]), 1)
        self.assertEqual(inv["evidence_weight_groups"][0]["maximum_independent_evidence_units"], 1)
        self.assertEqual(len(inv["byte_duplicate_groups"][0]["materialization_ids"]), 3)

    def test_missing_file_not_silently_admitted(self):
        with self.assertRaisesRegex(AVError, "missing"):
            self.admit([self.row(path="absent.bin")])
        self.assertFalse((self.root / "run").exists())

    def test_required_logical_membership_is_exact(self):
        with self.assertRaisesRegex(AVError, "does not match"):
            self.admit(required_logical_source_ids=["source-A", "missing-source"])

    def test_missing_stream_selection_is_rejected_even_with_one_stream(self):
        with self.assertRaisesRegex(AVError, "explicitly selected"):
            self.admit([self.row(selected_streams={"video": 0})])

    def test_wrong_kind_stream_rejected(self):
        with self.assertRaisesRegex(AVError, "No matching audio"):
            self.admit([self.row(selected_streams={"audio": 0, "video": 0})])

    def test_boolean_stream_index_is_not_integer(self):
        with self.assertRaisesRegex(AVError, "integer"):
            self.admit([self.row(selected_streams={"audio": True, "video": 0})])

    def test_multiple_materializations_need_one_preferred(self):
        with self.assertRaisesRegex(AVError, "Exactly one preferred"):
            self.admit([self.row(materialization_id="one"), self.row(materialization_id="two")])

    def test_duplicate_materialization_id(self):
        with self.assertRaisesRegex(AVError, "Duplicate materialization"):
            self.admit([self.row(), self.row()])

    def test_unknown_config_field_rejected(self):
        with self.assertRaisesRegex(AVError, "unknown"):
            self.admit([self.row(selected_steam=1)])

    def test_still_requirements_cannot_be_inferred(self):
        with self.assertRaisesRegex(AVError, "required_points"):
            self.admit([self.row(required_modalities=["visual_stills"])])

    def test_still_points_are_explicit_and_bounded(self):
        run = self.admit([self.row(required_modalities=["visual_stills"], required_points_seconds=[1, 3])])
        self.assertEqual(run["sources"][0]["required_points_seconds"], [1.0, 3.0])

    def test_expected_hash_mismatch(self):
        with self.assertRaisesRegex(AVError, "hash mismatch"):
            self.admit([self.row(expected_sha256="0123456789abcdef" * 4)])

    def test_placeholder_id_is_rejected(self):
        with self.assertRaisesRegex(AVError, "placeholder"):
            self.admit([self.row(logical_source_id="REPLACE_LOGICAL_SOURCE_ID")])

    def test_portable_validation_does_not_require_old_source_paths(self):
        self.admit()
        self.media.unlink()
        self.assertFalse(verify_run(self.root / "run")["original_sources_verified"])
        with self.assertRaisesRegex(AVError, "source verification"):
            verify_run(self.root / "run", verify_sources=True)

    def test_artifact_tampering_rejected(self):
        self.admit()
        (self.root / "run" / "inventory.json").write_bytes(b"changed")
        with self.assertRaisesRegex(AVError, "Artifact verification"):
            verify_run(self.root / "run")

    def test_unlisted_artifact_rejected(self):
        self.admit()
        (self.root / "run" / "unlisted.txt").write_text("extra")
        with self.assertRaisesRegex(AVError, "membership"):
            verify_run(self.root / "run")

    def test_duplicate_manifest_member_rejected(self):
        run = self.admit()
        run["artifacts"].append(copy.deepcopy(run["artifacts"][0]))
        self.rewrite(run)
        with self.assertRaisesRegex(AVError, "Duplicate artifact"):
            verify_run(self.root / "run")

    def test_path_traversal_is_rejected(self):
        run = self.admit()
        for path in ("../config.json", "C:/config.json", "folder\\file.txt", "./inventory.json", "run.json"):
            bad = copy.deepcopy(run); bad["artifacts"][0]["path"] = path
            self.rewrite(bad)
            with self.subTest(path=path), self.assertRaises(AVError):
                verify_run(self.root / "run")

    def test_missing_result_artifact_reference_is_rejected(self):
        run = self.admit(); run["metadata"]["result_file"] = "absent.json"; self.rewrite(run)
        with self.assertRaisesRegex(AVError, "result_file"):
            verify_run(self.root / "run")

    def test_missing_source_reference_in_derivative_mapping_rejected(self):
        run = self.admit()
        artifact = run["artifacts"][0]
        run["metadata"]["review_mapping"] = {"parent_source_sha256": "0123456789abcdef" * 4,
            "parent_stream_index": 1, "artifact_path": artifact["path"], "artifact_sha256": artifact["sha256"]}
        self.rewrite(run)
        with self.assertRaisesRegex(AVError, "absent parent"):
            verify_run(self.root / "run")

    def test_nested_bundle_checks_child_manifest_not_just_parent_hashes(self):
        self.admit()
        child = self.root / "run"
        bundle = self.root / "bundle"; bundle.mkdir()
        shutil.copytree(child, bundle / "child")
        child_data = json.loads((bundle / "child" / "run.json").read_text())
        child_data["artifacts"].append(copy.deepcopy(child_data["artifacts"][0]))
        (bundle / "child" / "run.json").write_text(json.dumps(child_data))
        members = [{"path": p.relative_to(bundle).as_posix(), "sha256": sha256(p), "size_bytes": p.stat().st_size} for p in bundle.rglob("*") if p.is_file()]
        parent = {"schema": "ave.run.v1", "operation": "bundle", "created_at": "2025-01-02T03:04:05Z",
                  "status": "GENERATED_NOT_REVIEWED", "sources": [], "artifacts": members, "metadata": {}}
        (bundle / "run.json").write_text(json.dumps(parent))
        with self.assertRaisesRegex(AVError, "Duplicate artifact"):
            verify_run(bundle)


if __name__ == "__main__":
    unittest.main()
