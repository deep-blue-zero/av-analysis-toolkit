import json
from pathlib import Path
import tempfile
import unittest

from avevidence.common import AVError, run
from avevidence.subtitles import parse_subtitles
from avevidence.timeline import align_timeline
from avevidence.visual import extract_frames
from test_visual import make_video

ASS = """[Script Info]
ScriptType: v4.00+
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.25,0:00:00.75,Default,篠澤広,0,0,0,,こんにちは。{\\i1}世界{\\i0}\\N次の行
"""


class SubtitleTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.video = make_video(self.root)

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()

    def test_utf8_and_utf16_preserve_japanese_names_raw_and_plain(self):
        for encoding in ("utf8", "utf16"):
            path = self.root / f"{encoding}.ass"
            path.write_text(ASS, encoding=encoding)
            output = self.root / encoding
            parse_subtitles(path, output)
            data = json.loads((output / "subtitles.json").read_text(encoding="utf8"))
            self.assertEqual(data["rows"][0]["name"], "篠澤広")
            self.assertEqual(data["rows"][0]["text_plain"], "こんにちは。世界\n次の行")
            self.assertIn(r"{\i1}", data["rows"][0]["text_raw"])
            self.assertEqual(data["clock"], "unbound_subtitle_clock")

    def test_invalid_encoding_empty_and_reversed_cues_refuse(self):
        cases = [("bad.srt", b"\xff\xffbroken"), ("empty.ass", b"[Events]\n"),
                 ("reversed.srt", b"1\n00:00:02,000 --> 00:00:01,000\nBad\n")]
        for name, content in cases:
            path = self.root / name
            path.write_bytes(content)
            output = self.root / (name + "-out")
            with self.assertRaises(AVError):
                parse_subtitles(path, output)
            self.assertFalse(output.exists())

    def test_embedded_ass_uses_original_packet_timing(self):
        text = self.root / "captions.ass"
        text.write_text(ASS, encoding="utf8")
        media = self.root / "subbed.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", self.video, "-i", text,
             "-map", "0:v", "-map", "1:0", "-c:v", "copy", "-c:s", "ass", "-output_ts_offset", "2.5", media])
        output = self.root / "embedded"
        parse_subtitles(media, output, media=True)
        data = json.loads((output / "subtitles.json").read_text(encoding="utf8"))
        self.assertEqual(data["binding_method"], "embedded_original_packet_pts")
        self.assertEqual(data["rows"][0]["name"], "篠澤広")
        self.assertAlmostEqual(data["rows"][0]["start_seconds"], .25)
        self.assertAlmostEqual(data["rows"][0]["end_seconds"], .75)
        self.assertEqual(data["parent_stream_index"], 1)

    def test_timeline_requires_source_binding_and_reports_distance(self):
        text = self.root / "captions.srt"
        text.write_text("1\n00:00:00,200 --> 00:00:00,600\n近い\n\n2\n00:00:02,800 --> 00:00:02,900\n遠い\n", encoding="utf16")
        frames = self.root / "frames"
        extract_frames(self.video, frames, mode="exact", timestamps=[0, 2], width=0)
        unbound = self.root / "unbound"
        parse_subtitles(text, unbound)
        with self.assertRaises(AVError):
            align_timeline(unbound, frames, self.root / "bad_timeline")
        bound = self.root / "bound"
        parse_subtitles(text, bound, source_media=self.video)
        output = self.root / "timeline"
        align_timeline(bound, frames, output, max_distance=.1)
        data = json.loads((output / "timeline.json").read_text(encoding="utf8"))
        self.assertEqual(data["rows"][0]["proximity_status"], "DISTANT")
        self.assertEqual(data["rows"][1]["proximity_status"], "OUTSIDE_FRAME_POINT_RANGE")
        self.assertTrue(all(row["status"] == "GENERATED_NOT_REVIEWED" for row in data["rows"]))
        other = make_video(self.root, "other", levels=[100] * 30)
        wrong = self.root / "wrong"
        parse_subtitles(text, wrong, source_media=other)
        with self.assertRaises(AVError):
            align_timeline(wrong, frames, self.root / "wrong_timeline")


if __name__ == "__main__":
    unittest.main()
