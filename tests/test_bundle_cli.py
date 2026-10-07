import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.bundle import build_bundle
from avevidence.cli import main
from avevidence.common import AVError, read_json, run, sha256
from avevidence.inventory import verify_run
from avevidence.packaging import pack_run, verify_archive


class BundleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-integration-")
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.source = self.root / "source テスト.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=128x96:rate=10:duration=3", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=16000:duration=3", "-c:v", "ffv1", "-c:a", "flac", self.source])
        self.captions = self.root / "captions 日本語.srt"
        self.captions.write_text("1\n00:00:00,100 --> 00:00:00,900\n日本語のテスト。\n\n"
                                 "2\n00:00:01,200 --> 00:00:02,400\n二つ目。\n", encoding="utf-8")

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()

    def test_bundle_portable_verified_and_packable(self):
        digest = sha256(self.source)
        out = self.root / "bundle"
        manifest = build_bundle(self.source, out, subtitles=self.captions, interval=1, width=128, shots=True, features=True)
        self.assertEqual(manifest["status"], "GENERATED_NOT_REVIEWED")
        verify_run(out)
        verify_run(out, verify_sources=True)
        self.assertEqual(sha256(self.source), digest)
        self.assertTrue((out / "features" / "features.csv").is_file())
        self.assertTrue((out / "timeline" / "timeline.json").is_file())
        moved = self.root / "moved bundle"
        out.rename(moved)
        verify_run(moved)
        verify_run(moved, verify_sources=True)
        with self.assertRaises(AVError):
            pack_run(moved, moved / "inside.zip")
        self.assertFalse((moved / "inside.zip").exists())
        pack_run(moved, self.root / "evidence.zip")
        self.assertTrue(verify_archive(self.root / "evidence.zip")["integrity_valid"])
        # New operations must resolve hash-bound internal references after the parent bundle moves.
        from avevidence.visual import contact_sheets
        contact_sheets(moved / "frames", self.root / "new contacts")
        # Artifact tampering must stop packaging.
        (moved / "INDEX.md").write_text("tampered", encoding="utf-8")
        with self.assertRaises(AVError): verify_run(moved)
        with self.assertRaises(AVError): pack_run(moved, self.root / "bad.zip")

    def test_cli_comparison_exit_status_and_output_reuse(self):
        out = self.root / "compare"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["compare-audio", str(self.source), str(self.source), str(out)])
        self.assertEqual(code, 0)
        before = sha256(out / "run.json")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(["compare-audio", str(self.source), str(self.source), str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(sha256(out / "run.json"), before)


if __name__ == "__main__":
    unittest.main()
