"""Canonical source duration is derived from presentation bounds, not container-name arithmetic."""
from pathlib import Path
import tempfile
import unittest

from avevidence.common import probe_source, run


class SourceClockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()

    def video(self, suffix, offset=0):
        path = self.root / ("video_" + str(offset) + suffix)
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "color=red:size=32x32:rate=10:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
             "-bf", "0", "-g", "10", "-output_ts_offset", str(offset), path])
        return path

    def test_nonzero_matroska_uses_packet_endpoint_minus_origin(self):
        source = probe_source(self.video(".mkv", 2.5))
        self.assertAlmostEqual(source["origin_seconds"], 2.5)
        self.assertAlmostEqual(source["duration_seconds"], 3.0)
        self.assertEqual(source["format_duration_seconds"], float(source["probe"]["format"]["duration"]))
        self.assertEqual(source["stream_timeline_bounds"][0]["basis"], "explicit_packet_pts_plus_duration")
        self.assertEqual(source["stream_timeline_bounds"][0]["packet_count"], 30)
        self.assertIsNotNone(source["packet_bounds_scan"]["table_sha256"])
        self.assertAlmostEqual(source["stream_timeline_bounds"][0]["end_seconds"], 5.5)

    def test_nonzero_mp4_uses_stream_presentation_interval(self):
        for offset in (0, 2.5):
            source = probe_source(self.video(".mp4", offset))
            self.assertAlmostEqual(source["origin_seconds"], offset)
            self.assertAlmostEqual(source["duration_seconds"], 3.0)
            self.assertEqual(source["format_duration_seconds"], float(source["probe"]["format"]["duration"]))
            self.assertEqual(source["stream_timeline_bounds"][0]["basis"], "container_stream_presentation_interval")
            self.assertIsNone(source["packet_bounds_scan"])

    def test_delayed_audio_extends_the_canonical_timeline_in_both_containers(self):
        for suffix, audio_codec in ((".mkv", "pcm_s16le"), (".mp4", "alac")):
            path = self.root / ("delayed_audio" + suffix)
            run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                 "color=blue:size=32x32:rate=10:duration=3", "-itsoffset", "4", "-f", "lavfi", "-i",
                 "anullsrc=r=8000:cl=mono:d=1", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                 "-preset", "ultrafast", "-bf", "0", "-c:a", audio_codec, path])
            source = probe_source(path)
            self.assertAlmostEqual(source["origin_seconds"], 0)
            self.assertAlmostEqual(source["duration_seconds"], 5.0)
            by_kind = {b["codec_type"]: b for b in source["stream_timeline_bounds"]}
            self.assertAlmostEqual(by_kind["video"]["canonical_end_seconds"], 3.0)
            self.assertAlmostEqual(by_kind["audio"]["canonical_start_seconds"], 4.0)
            self.assertAlmostEqual(by_kind["audio"]["canonical_end_seconds"], 5.0)

    def test_delayed_video_keeps_audio_zero_origin_and_video_tail(self):
        path = self.root / "delayed_video.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-itsoffset", "0.5", "-f", "lavfi", "-i",
             "color=blue:size=32x32:rate=10:duration=3", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-bf", "0",
             "-c:a", "pcm_s16le", path])
        source = probe_source(path)
        self.assertAlmostEqual(source["origin_seconds"], 0)
        self.assertAlmostEqual(source["duration_seconds"], 3.5)

    def test_unknown_packet_pts_stays_unavailable_and_wav_sample_clock_is_known(self):
        source_video = self.video(".mp4")
        raw = self.root / "without_timestamps.h264"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", source_video, "-c:v", "copy", "-f", "h264", raw])
        source = probe_source(raw)
        self.assertIsNone(source["duration_seconds"])
        self.assertEqual(source["duration_basis"], "UNAVAILABLE_INCOMPLETE_STREAM_BOUNDS")
        self.assertIn("packet lacks PTS or duration", source["stream_timeline_bounds"][0]["issues"])
        wav = self.root / "audio.wav"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1",
             "-c:a", "pcm_s16le", wav])
        audio = probe_source(wav)
        self.assertAlmostEqual(audio["duration_seconds"], 1.0)
        self.assertEqual(audio["origin_seconds"], 0)
        self.assertEqual(audio["stream_timeline_bounds"][0]["basis"], "intrinsic_audio_sample_count")


if __name__ == "__main__":
    unittest.main()
