import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from avevidence.common import (AVError, file_record, finish_run, interval, output_transaction,
                              parse_time, read_json, run, select_stream, sha256)
from avevidence.clips import clip_av
from avevidence.packaging import deterministic_zip, verify_archive


class ConfinedTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-core-test-")
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("ave-core-test-"))

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()


class SharedTests(ConfinedTestCase):
    def test_time_rejects_nonfinite_and_bad_boundaries(self):
        for value in ("nan", "inf", -1, "00:00:60", "01:99:00"):
            with self.subTest(value=value), self.assertRaises(AVError):
                parse_time(value)
        self.assertEqual(parse_time("01:02:03.25"), 3723.25)
        with self.assertRaises(AVError): interval(1, 3, 2)

    def test_output_collision_and_existing_run_preserve_bytes(self):
        source = self.root / "source.wav"
        source.write_bytes(b"SOURCE")
        with self.assertRaises(AVError):
            with output_transaction(source, [source]): pass
        output = self.root / "old-run"
        output.mkdir()
        (output / "keep.txt").write_text("KEEP")
        with self.assertRaises(AVError):
            with output_transaction(output, [source]): pass
        self.assertEqual(source.read_bytes(), b"SOURCE")
        self.assertEqual((output / "keep.txt").read_text(), "KEEP")
        with self.assertRaises(AVError):
            with output_transaction(output / "child", [output]): pass
        self.assertFalse((output / "child").exists())

    def test_explicit_missing_optional_stream_is_not_silently_omitted(self):
        source = {"streams": [{"index": 0, "codec_type": "video"}]}
        self.assertIsNone(select_stream(source, "audio", required=False))
        for index in (1, 0, -1, True, 0.5):
            with self.subTest(index=index), self.assertRaises(AVError):
                select_stream(source, "audio", index, required=False)

    def test_source_mutation_prevents_publication(self):
        source = self.root / "input.txt"
        source.write_text("BEFORE")
        record = file_record(source)
        output = self.root / "run"
        with self.assertRaises(AVError):
            with output_transaction(output, [source]) as stage:
                (stage / "artifact.txt").write_text("data")
                source.write_text("AFTER")
                finish_run(stage, "synthetic", [record])
        self.assertFalse(output.exists())
        self.assertEqual(len(list(self.root.glob(".run.staging-*/FAILED.json"))), 1)

    def test_archives_deterministic_complete_and_no_overwrite(self):
        files = [("b/data.bin", b"\x00\x80\xff"), ("a.txt", "日本語".encode())]
        a = deterministic_zip(files, self.root / "a.zip")
        b = deterministic_zip(reversed(files), self.root / "b.zip")
        self.assertEqual(a["sha256"], b["sha256"])
        self.assertTrue(verify_archive(self.root / "a.zip")["integrity_valid"])
        with self.assertRaises(AVError): deterministic_zip(files, self.root / "a.zip")
        with self.assertRaises(AVError): deterministic_zip([("../escape", b"x")], self.root / "bad.zip")
        with self.assertRaises(AVError): deterministic_zip([("A", b"a"), ("a", b"b")], self.root / "case.zip")

    def test_streamed_archive_matches_bytes_and_detects_changed_artifacts(self):
        payload = self.root / "payload.bin"
        payload.write_bytes(b"block" * 500000)
        a = deterministic_zip([("payload.bin", payload)], self.root / "path.zip")
        b = deterministic_zip([("payload.bin", payload.read_bytes())], self.root / "bytes.zip")
        self.assertEqual(a["sha256"], b["sha256"])
        old_hash = sha256(payload)
        payload.write_bytes(b"changed")
        with self.assertRaises(AVError):
            deterministic_zip([("payload.bin", payload)], self.root / "changed.zip",
                              expected_hashes={"payload.bin": old_hash})
        self.assertFalse((self.root / "changed.zip").exists())

    def test_malformed_archive_and_names_are_refused(self):
        bad = self.root / "bad.zip"
        bad.write_bytes(b"This is not a ZIP")
        with self.assertRaises(AVError): verify_archive(bad)
        for name in ("../escape", "a\nb", "a//b", "/root", "a/./b"):
            with self.subTest(name=name), self.assertRaises(AVError):
                deterministic_zip([(name, b"x")], self.root / "unsafe.zip")
        with self.assertRaises(AVError):
            deterministic_zip([("ok", b"x")], self.root / "prefix.zip", prefix="../")
        malicious = self.root / "malicious.zip"
        with zipfile.ZipFile(malicious, "x") as z:
            z.writestr("a", b"x")
            z.writestr("SHA256SUMS.txt", "malformed\n")
        with self.assertRaises(AVError): verify_archive(malicious)

    def test_corrupt_deflate_is_a_controlled_error(self):
        archive = self.root / "broken-deflate.zip"
        payload = b"synthetic payload" * 40
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("a", payload)
            z.writestr("SHA256SUMS.txt", hashlib.sha256(payload).hexdigest() + "  a\n")
        data = bytearray(archive.read_bytes())
        name_length = int.from_bytes(data[26:28], "little")
        extra_length = int.from_bytes(data[28:30], "little")
        offset = 30 + name_length + extra_length
        data[offset] = (data[offset] & 0xF8) | 0x07  # Reserved DEFLATE block type.
        archive.write_bytes(data)
        with self.assertRaises(AVError): verify_archive(archive)


class ClipTests(ConfinedTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.root / "source.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=96x64:rate=10:duration=3", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=48000:duration=3", "-c:v", "ffv1", "-c:a", "pcm_s16le", self.source])

    def test_av_clip_maps_actual_frame_start_and_audio(self):
        digest = sha256(self.source)
        out = self.root / "clip"
        manifest = clip_av(self.source, out, start=.25, end=1.1)
        data = read_json(out / "clip.json")
        maps = {x["modality"]: x for x in data["review_mappings"]}
        self.assertAlmostEqual(maps["motion"]["segments"][0]["parent_start_seconds"], .3, places=5)
        self.assertAlmostEqual(maps["audio"]["segments"][0]["parent_start_seconds"], .25, places=4)
        self.assertEqual(sha256(self.source), digest)
        self.assertEqual(manifest["status"], "GENERATED_NOT_REVIEWED")
        self.assertTrue((out / "clip.mkv").is_file())

    def test_nonfinite_padding_is_refused(self):
        with self.assertRaises(AVError):
            clip_av(self.source, self.root / "nan", start=1, end=2, pad=float("nan"))
        self.assertFalse((self.root / "nan").exists())

    def test_unknown_source_duration_with_explicit_bounds(self):
        from avevidence.common import probe_source
        known = probe_source(self.source)
        known["duration_seconds"] = None
        def probe(path):
            return known if Path(path).resolve() == self.source.resolve() else probe_source(path)
        with patch("avevidence.clips.probe_source", side_effect=probe):
            clip_av(self.source, self.root / "unknown-duration", start=.5, end=1, pad=.1)
        data = read_json(self.root / "unknown-duration" / "clip.json")
        self.assertEqual(data["padded_interval_seconds"], [.4, 1.1])

    def test_coarse_original_timebase_preserves_offgrid_clip_offset(self):
        source = self.root / "coarse-clock.avi"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=96x64:rate=10:duration=2", "-c:v", "ffv1", source])
        out = self.root / "coarse-clip"
        clip_av(source, out, start=.25, end=.7)
        mapping = read_json(out / "clip.json")["review_mappings"][0]
        self.assertAlmostEqual(mapping["segments"][0]["parent_start_seconds"], .3, places=5)
        self.assertAlmostEqual(mapping["segments"][0]["parent_end_seconds"], .7, places=5)

    def test_delayed_audio_mapping_counts_only_available_samples(self):
        source = self.root / "delayed.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=96x64:rate=10:duration=3", "-itsoffset", "2", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=16000:duration=1", "-c:v", "ffv1", "-c:a", "pcm_s16le", source])
        out = self.root / "delayed-clip"
        clip_av(source, out, start=1, end=2.5)
        maps = read_json(out / "clip.json")["review_mappings"]
        audio = next(m for m in maps if m["modality"] == "audio")
        self.assertAlmostEqual(audio["segments"][0]["parent_start_seconds"], 2, places=4)
        self.assertAlmostEqual(audio["segments"][0]["parent_end_seconds"], 2.5, places=3)


if __name__ == "__main__":
    unittest.main()
