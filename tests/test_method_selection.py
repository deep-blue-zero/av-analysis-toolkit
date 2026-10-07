"""Explicit method authority is independent of semantic evidence isolation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from avevidence.analysis_benchmark import FALLBACK_GENERIC_METHOD, blind_export
from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.deep_read import deep_read
from avevidence.inventory import verify_run


class MethodSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/"source-fixture.bin"
        self.source.write_bytes(b"Fixture identity only; preflight mocked, not an episode.")
        self.method = self.root/"custom-method.md"
        self.original = b"\xef\xbb\xbf# Explicit method\r\n" + "独自の方法。\r\n".encode("utf-8") + b"Compare local counterevidence.\r\n"
        self.method.write_bytes(self.original)
        self.config = self.root/"blind-input.json"
        write_json(self.config, {"title":"Excluded title", "prior_analysis":"Excluded thesis", "utterances":[
            {"cue_id":1, "speaker":"Named character", "text":"Named character speaks", "start_s":0, "end_s":1}]})

    def tearDown(self):
        self.temp.cleanup()

    def fake_preflight(self, source, output, **kwargs):
        write_json(Path(output)/"capabilities.json", {"modalities":{"audio":False,"video":False,"text":False}})

    def prepare(self, *, explicit, isolated, name="reading"):
        output = self.root/name
        with patch("avevidence.analysis_preflight.preflight", side_effect=self.fake_preflight):
            result = deep_read(self.source, output, method=self.method if explicit else None, episode_isolated=isolated)
        self.check_receipt(output, "method.txt", explicit=explicit, isolated=isolated)
        plan = read_json(output/"analysis-plan.json")
        self.assertEqual(plan["schema"], "ave.deep-read.v1")
        self.assertEqual(plan["episode_isolated"], isolated)
        self.assertEqual(plan["method_selection"], "explicit" if explicit else "fallback")
        self.assertEqual(result["metadata"]["method_sha256"], plan["method_sha256"])
        instructions = (output/"START_HERE.txt").read_text(encoding="utf-8")
        self.assertIn("governing analytical method", instructions)
        self.assertEqual("Do not use later episodes" in instructions, isolated)
        return output

    def check_receipt(self, output, copy_path, *, explicit, isolated):
        expected = self.original if explicit else FALLBACK_GENERIC_METHOD.encode("utf-8")
        self.assertEqual((output/copy_path).read_bytes(), expected)
        receipt = read_json(output/"method-receipt.json")
        self.assertEqual(receipt["schema"], "ave.method-selection.v1")
        self.assertEqual(receipt["method_selection"], "explicit" if explicit else "fallback")
        self.assertEqual(receipt["method_source"], str(self.method.resolve()) if explicit else "builtin:fallback_generic_method")
        self.assertEqual(receipt["method_filename"], self.method.name if explicit else None)
        self.assertEqual(receipt["method_sha256"], sha256(self.method) if explicit else sha256(output/copy_path))
        self.assertEqual(receipt["method_source_sha256"], receipt["method_copied_sha256"])
        self.assertTrue(receipt["method_copy_matches_source"])
        self.assertEqual(receipt["method_copy_path"], copy_path)
        self.assertEqual(receipt["episode_isolated"], isolated)
        self.assertEqual(receipt["semantic_evidence_boundary"], "supplied_source_only" if isolated else "owner_defined")
        manifest = read_json(output/"run.json")
        self.assertEqual(manifest["metadata"]["method_sha256"], receipt["method_sha256"])
        methods = [s for s in manifest["sources"] if s["kind"] == "analytical-method"]
        self.assertEqual(len(methods), 1 if explicit else 0)
        if explicit:
            self.assertEqual(methods[0]["sha256"], receipt["method_sha256"])
        verify_run(output)

    def test_explicit_method_with_episode_isolated(self):
        self.prepare(explicit=True, isolated=True)

    def test_explicit_method_without_episode_isolated(self):
        self.prepare(explicit=True, isolated=False)

    def test_fallback_with_episode_isolated(self):
        self.prepare(explicit=False, isolated=True)

    def test_fallback_ordinary_run(self):
        self.prepare(explicit=False, isolated=False)

    def test_blind_export_explicit_method_preserves_bytes(self):
        output = self.root/"blind"
        blind_export(self.config, output, method=self.method)
        self.check_receipt(output, "analyst-context/method.txt", explicit=True, isolated=True)
        # Coordinator provenance does not inject private paths into the blind context.
        text = (output/"analyst-context/source-001.json").read_text(encoding="utf-8")
        self.assertNotIn("Named character", text)
        self.assertNotIn("Excluded title", text)
        self.assertNotIn("Excluded thesis", text)
        self.assertFalse((output/"analyst-context/method-receipt.json").exists())

    def test_blind_export_fallback(self):
        output = self.root/"blind"
        blind_export(self.config, output)
        self.check_receipt(output, "analyst-context/method.txt", explicit=False, isolated=True)

    def test_missing_explicit_method_fails_before_media_preflight(self):
        missing = self.root/"missing.md"
        with patch("avevidence.analysis_preflight.preflight") as preflight:
            with self.assertRaisesRegex(AVError, "Explicit analytical method"):
                deep_read(self.source, self.root/"refused", method=missing, episode_isolated=True)
            preflight.assert_not_called()
        self.assertFalse((self.root/"refused").exists())
        self.assertEqual(list(self.root.glob(".refused.staging-*")), [])

    def test_blind_export_missing_method_fails_early(self):
        with self.assertRaisesRegex(AVError, "Explicit analytical method"):
            blind_export(self.config, self.root/"refused", method=self.root/"missing.md")
        self.assertFalse((self.root/"refused").exists())
        self.assertEqual(list(self.root.glob(".refused.staging-*")), [])

    def test_explicit_directory_is_not_a_fallback_request(self):
        with self.assertRaisesRegex(AVError, "Explicit analytical method"):
            deep_read(self.source, self.root/"refused", method=self.root, episode_isolated=True)

    def test_unreadable_explicit_method_is_not_a_fallback_request(self):
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("fixture denial")):
            with self.assertRaisesRegex(AVError, "Cannot read explicit analytical method"):
                blind_export(self.config, self.root/"refused", method=self.method)

    def test_invalid_utf8_is_refused(self):
        self.method.write_bytes(b"\xff\xfeinvalid")
        with self.assertRaisesRegex(AVError, "Cannot read explicit analytical method as UTF-8"):
            deep_read(self.source, self.root/"refused", method=self.method)

    def test_empty_explicit_path_does_not_silently_fall_back(self):
        with self.assertRaisesRegex(AVError, "Explicit analytical method"):
            deep_read(self.source, self.root/"refused", method="")

    def test_zero_byte_explicit_file_remains_explicit(self):
        self.original = b""
        self.method.write_bytes(self.original)
        self.prepare(explicit=True, isolated=True)

    def test_method_changed_during_preparation_cannot_publish(self):
        def mutate(source, output, **kwargs):
            self.fake_preflight(source, output, **kwargs)
            self.method.write_bytes(b"Changed during preparation.")
        with patch("avevidence.analysis_preflight.preflight", side_effect=mutate):
            with self.assertRaises(AVError):
                deep_read(self.source, self.root/"refused", method=self.method, episode_isolated=True)
        self.assertFalse((self.root/"refused").exists())

    def test_deep_read_cli_passes_both_orthogonal_options(self):
        from avevidence.cli import dispatch, parser
        output = self.root/"cli-reading"
        args = parser().parse_args(["deep-read", str(self.source), str(output), "--method", str(self.method), "--episode-isolated"])
        with patch("avevidence.analysis_preflight.preflight", side_effect=self.fake_preflight):
            dispatch(args)
        self.check_receipt(output, "method.txt", explicit=True, isolated=True)

    def test_blind_export_cli_accepts_and_passes_explicit_method(self):
        from avevidence.cli import dispatch, parser
        output = self.root/"cli-blind"
        args = parser().parse_args(["blind-export", str(self.config), str(output), "--method", str(self.method)])
        dispatch(args)
        self.check_receipt(output, "analyst-context/method.txt", explicit=True, isolated=True)


if __name__ == "__main__":
    unittest.main()
