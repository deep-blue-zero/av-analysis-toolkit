import json
from pathlib import Path
import tempfile
import unittest

from avevidence.common import AVError, probe_source, run
from avevidence.visual import contact_sheets, extract_frames


def make_video(root, name="source", *, offset=0, vfr=False, levels=None):
    root = Path(root)
    raw = root / f"{name}.gray"
    levels = levels or [10 + i * 7 for i in range(30)]
    raw.write_bytes(b"".join(bytes([value]) * 32 * 32 for value in levels))
    output = root / f"{name}.mkv"
    command = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "rawvideo", "-pixel_format", "gray",
               "-video_size", "32x32", "-framerate", "10", "-i", raw]
    if vfr:
        command += ["-vf", "select='not(eq(n,1)+eq(n,3))'", "-fps_mode", "vfr"]
    command += ["-c:v", "ffv1"]
    if offset:
        command += ["-output_ts_offset", str(offset)]
    run(command + [output])
    return output


def result(directory):
    return json.loads((Path(directory) / "frames.json").read_text(encoding="utf8"))


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.source = make_video(self.root)

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()

    def test_interval_uses_actual_source_pts_and_source_start_is_not_retimed(self):
        output = self.root / "interval"
        extract_frames(self.source, output, interval=1, width=0)
        self.assertEqual([r["source_seconds"] for r in result(output)["rows"]], [0, 1, 2])
        output = self.root / "source_frames"
        extract_frames(self.source, output, mode="source", start=.25, end=.8, width=0)
        self.assertEqual([r["source_seconds"] for r in result(output)["rows"]], [.3, .4, .5, .6, .7])
        self.assertTrue(all(r["requested_seconds"] is None for r in result(output)["rows"]))

    def test_exact_first_after_and_repeated_requests_share_tracked_artifact(self):
        output = self.root / "exact"
        manifest = extract_frames(self.source, output, mode="exact", timestamps=[.25, .25, .2], width=0)
        data = result(output)
        self.assertEqual([r["source_seconds"] for r in data["rows"]], [.3, .3, .2])
        self.assertEqual(data["rows"][1]["duplicate_of"], "F000001")
        self.assertEqual(data["rows"][0]["path"], data["rows"][1]["path"])
        self.assertAlmostEqual(data["rows"][0]["timing_delta_seconds"], .05)
        self.assertEqual(manifest["status"], "GENERATED_NOT_REVIEWED")

    def test_vfr_with_nonzero_container_origin_preserves_original_pts(self):
        source = make_video(self.root, "vfr", offset=2.5, vfr=True)
        output = self.root / "vfr_frames"
        extract_frames(source, output, mode="exact", timestamps=[.15, .25], width=0)
        data = result(output)
        self.assertAlmostEqual(data["origin_seconds"], 2.5)
        self.assertAlmostEqual(data["rows"][0]["source_seconds"], .2)
        self.assertAlmostEqual(data["rows"][0]["original_pts_seconds"], 2.7)
        self.assertAlmostEqual(data["rows"][1]["source_seconds"], .4)

    def test_video_delay_relative_to_audio_origin_is_retained_and_hdr_refuses(self):
        delayed = self.root / "delayed.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-itsoffset", "0.5", "-i", self.source,
             "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "3.5", "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "pcm_s16le", delayed])
        output = self.root / "delay_frames"
        extract_frames(delayed, output, mode="exact", timestamps=[0, .25, .55], width=0)
        data = result(output)
        self.assertEqual(data["origin_seconds"], 0)
        self.assertEqual([r["source_seconds"] for r in data["rows"]], [.5, .5, .6])
        hdr = self.root / "hdr.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", self.source, "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", hdr])
        self.assertEqual(probe_source(hdr)["streams"][0]["color_space"], "bt2020nc")
        with self.assertRaises(AVError):
            extract_frames(hdr, self.root / "hdr_frames", mode="exact", timestamps=[0], width=0)

    def test_missing_and_beyond_eof_never_publish_success(self):
        for source, mode, params, name in [(self.root / "absent.mkv", "shots", {}, "missing"),
                                           (self.source, "exact", {"timestamps": [9]}, "beyond")]:
            output = self.root / name
            with self.assertRaises(AVError):
                extract_frames(source, output, mode=mode, width=0, **params)
            self.assertFalse(output.exists())

    def test_shots_distinguish_no_changes_from_real_cut(self):
        flat = make_video(self.root, "flat", levels=[20] * 30)
        output = self.root / "flat_shots"
        extract_frames(flat, output, mode="shots", width=0)
        self.assertEqual(result(output)["rows"], [])
        cut = make_video(self.root, "cut", levels=[20]*15 + [220]*15)
        output = self.root / "cut_shots"
        extract_frames(cut, output, mode="shots", width=0)
        self.assertEqual([r["source_seconds"] for r in result(output)["rows"]], [1.5])

    def test_output_refusal_and_manifest_driven_verified_contacts(self):
        output = self.root / "frames"
        extract_frames(self.source, output, mode="exact", timestamps=[0, 1], width=0)
        with self.assertRaises(AVError):
            extract_frames(self.source, output, mode="exact", timestamps=[2], width=0)
        (output / "unmanaged.png").write_bytes(b"not a managed image")
        contact_output = self.root / "contacts"
        contact_sheets(output, contact_output, columns=2, rows=1, thumb_width=128)
        contacts = json.loads((contact_output / "contacts.json").read_text())
        self.assertEqual(contacts["sheets"][0]["frame_ids"], ["F000001", "F000002"])
        point = result(output)["rows"][0]
        (output / point["path"]).write_bytes(b"changed")
        with self.assertRaises(AVError):
            contact_sheets(output, self.root / "tampered")

    def test_source_requires_bounded_interval_and_ambiguous_streams_refuse(self):
        with self.assertRaises(AVError):
            extract_frames(self.source, self.root / "unbounded", mode="source", width=0)
        multi = self.root / "multi.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", self.source, "-map", "0:v", "-map", "0:v", "-c", "copy", multi])
        with self.assertRaises(AVError):
            extract_frames(multi, self.root / "ambiguous", mode="exact", timestamps=[0], width=0)
        extract_frames(multi, self.root / "chosen", mode="exact", timestamps=[0], width=0, stream_index=1)


if __name__ == "__main__":
    unittest.main()
