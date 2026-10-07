"""Local performance regressions: signal truth, timing gaps, cache integrity and search."""
from pathlib import Path
import importlib.util
import json
import math
import shutil
import tempfile
import unittest
import wave
from unittest.mock import patch
from types import SimpleNamespace

from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.performance_alignment import align_cue, speech_text
from avevidence.performance_cache import cache_get


AVAILABLE = all(importlib.util.find_spec(name) for name in ("numpy", "parselmouth"))


@unittest.skipUnless(AVAILABLE, "performance extra required")
class ContourTests(unittest.TestCase):
    def test_known_tones_levels_silence_and_gap(self):
        import numpy as np
        from avevidence.performance_contours import compute_contours
        rate = 16000
        t = np.arange(rate * 2) / rate
        signal = np.r_[.2 * np.sin(2*np.pi*220*t), .1 * np.sin(2*np.pi*440*t), np.zeros(rate*2)]
        meta = {"identity": {"sample_rate_hz": rate}, "segments": [
            {"sample_start": 0, "sample_end": rate*2, "source_start_seconds": 3., "source_end_seconds": 5.},
            {"sample_start": rate*2, "sample_end": rate*6, "source_start_seconds": 7., "source_end_seconds": 11.}]}
        track = compute_contours(signal, meta, block_s=1.)
        for start, end, frequency, amplitude in ((3.2, 4.8, 220, .2), (7.2, 8.8, 440, .1)):
            pick = (track["time_s"] > start) & (track["time_s"] < end)
            self.assertAlmostEqual(float(np.nanmedian(track["pitch_hz"][pick])), frequency, delta=1)
            self.assertAlmostEqual(float(np.median(track["rms_dbfs"][pick])), 20*math.log10(amplitude/math.sqrt(2)), delta=.15)
        self.assertFalse(np.any((track["time_s"] >= 5) & (track["time_s"] < 7)))
        silent = track["time_s"] > 9.3
        self.assertTrue(np.isnan(track["pitch_hz"][silent]).all())
        self.assertTrue((track["rms_dbfs"][silent] == -240).all())
        self.assertEqual(len(np.unique(track["sample_center"])), len(track["sample_center"]))
        self.assertTrue((np.diff(track["time_s"]) > 0).all())

    def test_invalid_configuration_is_refused(self):
        import numpy as np
        from avevidence.performance_contours import compute_contours
        with self.assertRaises(AVError):
            compute_contours(np.zeros(16000), {}, floor=600, ceiling=75)

    def test_level_summary_averages_energy_before_logarithm(self):
        import numpy as np
        from avevidence.performance import cue_summary
        tracks = {"time_s": np.array([.2, .8]), "pitch_hz": np.array([np.nan, np.nan]),
                  "rms_dbfs": np.array([-20., -240.])}
        result = cue_summary(tracks, {"start_s": 0., "end_s": 1., "alignment": {"units": []}})
        self.assertAlmostEqual(result["rms_dbfs_energy_mean"], -23.0102999566, places=7)
        self.assertEqual(result["rms_dbfs_mean"], -130.)


class CacheTests(unittest.TestCase):
    def test_timestamp_tolerance_is_explicit_bounded_and_recorded(self):
        from avevidence.audio import _decode
        with tempfile.TemporaryDirectory() as directory:
            def decoder(command, **kwargs):
                Path(kwargs["stdout_file"]).write_bytes(bytes(8*1024*8))
                pts = [0, 1008, 2016, 3024, 4080, 5088, 6096, 7104]
                return SimpleNamespace(stderr="\n".join(
                    f"n:{i} pts:{p} pts_time:0 rate:48000 nb_samples:1024" for i,p in enumerate(pts)))
            source = {"path": "synthetic", "origin_seconds": 0.}
            stream = {"sample_rate": "48000", "channels": 1, "index": 0, "time_base": "1/1000"}
            with patch("avevidence.audio.run", decoder):
                with self.assertRaises(AVError):
                    _decode(source, stream, directory, "strict")
                value = _decode(source, stream, directory, "explicit", timestamp_tolerance_seconds=.002)
                self.assertEqual(len(value["segments"]), 1)
                self.assertAlmostEqual(value["max_timestamp_adjustment_seconds"], .001333333333333, places=8)
                self.assertIn("Explicit analysis", value["timestamp_policy"])
                with self.assertRaises(AVError):
                    _decode(source, stream, directory, "too_large", timestamp_tolerance_seconds=.011)

    def test_reuse_invalidation_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def build(stage):
                calls.append(1)
                write_json(stage / "value.json", {"value": 3})
            first, hit, key = cache_get(directory, "test", {"config": 1}, build)
            self.assertFalse(hit)
            self.assertTrue(cache_get(directory, "test", {"config": 1}, build)[1])
            self.assertFalse(cache_get(directory, "test", {"config": 2}, build)[1])
            self.assertEqual(len(calls), 2)
            (first / "value.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(AVError):
                cache_get(directory, "test", {"config": 1}, build)

    def test_japanese_sdh_normalization_retains_dialogue(self):
        text, speaker, kind, flags = speech_text("（ヤニ）店員(てんいん)さん！")
        self.assertEqual((text, speaker, kind), ("店員さん！", "ヤニ", "speech_candidate"))
        self.assertEqual(speech_text("（静江(しずえ)）ンフフ")[:2], ("ンフフ", "静江"))
        self.assertEqual(speech_text("（２人の悲鳴）")[2], "non_speech_caption")
        self.assertEqual(speech_text("♪～")[2], "non_speech_caption")
        self.assertIn("multiple_caption_speakers", speech_text("（甲）ねえ\n（乙）はい")[3])


@unittest.skipUnless(AVAILABLE, "performance extra required")
class AlignmentTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.samples = np.zeros(64000, dtype=np.float32)
        self.meta = {"identity": {"sample_rate_hz": 16000}, "segments": [
            {"sample_start": 0, "sample_end": 64000, "source_start_seconds": 3., "source_end_seconds": 7.}]}
        self.cue = {"alignment_text": "こんにちは", "kind": "speech_candidate", "start_s": 4., "end_s": 5.}
        class Fake:
            identity = {"model": "test_only"}
            device = "cpu"
            calls = 0
            def __call__(self, audio, rate, text):
                self.calls += 1
                return [{"text": text, "start_s": .2, "end_s": .9}]
        self.model = Fake()

    def tearDown(self):
        self.temp.cleanup()

    def align(self, **kwargs):
        return align_cue(self.cue, self.samples, self.meta, aligner=self.model,
                         cache_root=self.temp.name, audio_key="synthetic", **kwargs)

    def test_native_offset_and_warm_cache_no_inference(self):
        result = self.align()
        self.assertAlmostEqual(result["units"][0]["start_s"], 4.)
        self.assertAlmostEqual(result["units"][0]["end_s"], 4.7)
        self.assertIsNone(result["confidence"])
        self.assertTrue(self.align()["cache_hit"])
        self.assertEqual(self.model.calls, 1)
        self.cue["alignment_text"] = "さようなら"
        self.assertFalse(self.align()["cache_hit"])
        self.assertEqual(self.model.calls, 2)

    def test_uncovered_cue_and_nonspeech_do_not_infer(self):
        self.cue["end_s"] = 8.
        self.assertEqual(self.align()["status"], "SKIPPED_AUDIO_GAP_OR_PARTIAL_COVERAGE")
        self.cue["kind"] = "non_speech_caption"
        self.assertEqual(self.align()["status"], "SKIPPED_NON_SPEECH")
        self.assertEqual(self.model.calls, 0)

    def test_invalid_model_timing_cached_as_rejected_without_usable_units(self):
        class Broken:
            identity = {"model": "bad_test"}
            device = "cpu"
            def __call__(self, audio, rate, text):
                return [{"text": text, "start_s": float("nan"), "end_s": 1.}]
        self.model = Broken()
        result = self.align()
        self.assertEqual(result["status"], "REJECTED_TIMING")
        self.assertEqual(result["units"], [])
        self.assertIsNone(result["raw_rejected_units"][0]["crop_start_s"])
        self.assertTrue(self.align()["cache_hit"])


@unittest.skipUnless(AVAILABLE and shutil.which("ffmpeg") and shutil.which("ffprobe"), "performance extra and FFmpeg required")
class PerformanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / "source.wav"
        rate = 16000
        signal = (.2*np.sin(2*np.pi*220*np.arange(rate*2)/rate)*32767).astype("<i2")
        with wave.open(str(self.media), "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(signal.tobytes())
        self.transcript = self.root / "transcript.json"
        write_json(self.transcript, {"source_sha256": sha256(self.media), "text_authority": "synthetic_test",
            "utterances": [{"start_s": .1, "end_s": 1.5, "text": "猫と話す ' OR 1=1 --", "speaker": "甲"}]})

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, name):
        from avevidence.performance import performance
        return performance(self.media, self.root/name, cache_dir=self.root/"cache", transcript=self.transcript, progress=False)

    def test_cold_warm_verified_runs_search_and_deduplication(self):
        from avevidence.inventory import verify_run
        from avevidence.performance_index import index_run, search
        self.generate("first")
        self.generate("second")
        report = read_json(self.root/"second"/"performance.json")
        self.assertTrue(report["cache"]["audio_hit"])
        self.assertTrue(report["cache"]["contour_hit"])
        self.assertEqual(report["perceptual_review"], "NOT_PERFORMED")
        verify_run(self.root/"second", verify_sources=True)
        database = self.root/"search.sqlite"
        self.assertFalse(index_run(self.root/"first", database)["replaced_identical_evidence"])
        self.assertTrue(index_run(self.root/"second", database)["replaced_identical_evidence"])
        found = search(database, "猫と", speaker="甲", min_pitch=200)
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["matches"][0]["source_sha256"], sha256(self.media))
        self.assertEqual(search(database, "not found ' OR 1=1 --")["count"], 0)
        self.assertEqual(search(database, "' OR 1=1 --")["count"], 1)
        with (self.root/"second"/"run.json").open("a", encoding="utf-8") as out:
            out.write(" ")
        self.assertEqual(search(database, "猫と")["matches"][0]["run_manifest_status"], "MISSING_OR_CHANGED")

    def test_wrong_source_text_binding_is_refused(self):
        data = read_json(self.transcript)
        data["source_sha256"] = "0"*64
        self.transcript.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(AVError):
            self.generate("invalid")
        self.assertFalse((self.root/"invalid").exists())

    def test_index_refuses_tampered_artifact(self):
        from avevidence.performance_index import index_run
        self.generate("first")
        (self.root/"first"/"performance.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(AVError):
            index_run(self.root/"first", self.root/"search.sqlite")


if __name__ == "__main__":
    unittest.main()
