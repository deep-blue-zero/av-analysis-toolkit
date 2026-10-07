"""Synthetic regressions for sample fidelity, source clocks and unavailable data."""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from avevidence.audio import (CLOCK, clip_audio, compare_audio, cue_features,
                              extract_audio, measure_audio, _preserve_wav_channel_mask)
from avevidence.common import AVError, probe_source, sha256


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe required")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-audio-tests-")
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.temp.cleanup()

    def command(self, *args):
        result = subprocess.run([str(x) for x in args], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf8", "replace"))
        return result

    def tone(self, name="tone.wav", seconds=2, rate=16000, stereo=False):
        values=[]
        for i in range(round(seconds*rate)):
            sample=round(32767*.2*math.sin(2*math.pi*440*i/rate))
            values.extend((sample, -sample) if stereo else (sample,))
        path=self.root/name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(2 if stereo else 1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(struct.pack("<"+"h"*len(values),*values))
        return path

    def delayed(self):
        tone=self.tone()
        path=self.root/"delayed.mkv"
        self.command("ffmpeg","-nostdin","-v","error","-n","-f","lavfi","-i","color=c=black:s=160x90:r=10:d=6",
                     "-itsoffset","2","-i",tone,"-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","ultrafast","-c:a","flac",path)
        return path

    def cues(self, source, intervals, name="cues.csv", *, bound=True):
        rows=[]
        fields=["cue_index","start_seconds","end_seconds","name","text_plain","parent_source_sha256","clock","origin_seconds"]
        for i,(start,end) in enumerate(intervals,1):
            rows.append(dict(cue_index=i,start_seconds=start,end_seconds=end,name="SYNTHETIC",text_plain="test fixture",
                             parent_source_sha256=source["sha256"] if bound else "",clock=CLOCK if bound else "",origin_seconds=source["origin_seconds"] if bound else ""))
        path=self.root/name
        with path.open("w",encoding="utf8",newline="") as out:
            writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        return path

    def report(self, directory, filename):
        return json.loads((directory/filename).read_text(encoding="utf8"))

    def test_float_preservation_and_flac_refusal(self):
        raw=self.root/"float.raw"
        values=[.123456789,1.25,-1.25,1e-8]*4000
        raw.write_bytes(struct.pack("<"+"d"*len(values),*values))
        source=self.root/"float.wav"
        self.command("ffmpeg","-nostdin","-v","error","-n","-f","f64le","-ar","16000","-ac","1","-i",raw,"-c:a","pcm_f32le",source)
        output=self.root/"wav-run"
        extract_audio(source,output)
        result=self.report(output,"audio.json")
        self.assertTrue(result["sample_preservation_verified"])
        comparison=self.root/"cmp"
        compare_audio(source,output/"audio.wav",comparison)
        self.assertTrue(self.report(comparison,"comparison.json")["native_decoded_samples_equal"])
        with self.assertRaisesRegex(AVError,"changed decoded f64"):
            extract_audio(source,self.root/"bad-flac",format="flac")
        self.assertFalse((self.root/"bad-flac").exists())

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required")
    def test_delayed_mapping_missing_and_partial_cues(self):
        source_path=self.delayed();source=probe_source(source_path)
        output=self.root/"extract"
        extract_audio(source_path,output)
        receipt=self.report(output,"audio.json")
        self.assertAlmostEqual(receipt["segments"][0]["source_start_seconds"],2,places=6)
        mapping=receipt["review_mapping"]["segments"][0]
        self.assertAlmostEqual(mapping["parent_start_seconds"],2,places=6)
        self.assertAlmostEqual(mapping["derivative_start_seconds"],0,places=6)
        csv_path=self.cues(source,[(2.1,3.1),(0,1),(1.5,2.5),(7,8)])
        features=self.root/"features"
        cue_features(output,csv_path,features)
        result=self.report(features,"features.json")
        self.assertEqual(result["dialogue_clock_status"],"VERIFIED_SOURCE_BINDING")
        rows=result["rows"]
        self.assertEqual([r["status"] for r in rows],["COMPLETE","MISSING","PARTIAL","MISSING"])
        self.assertAlmostEqual(rows[0]["per_channel"][0]["rms_dbfs"],20*math.log10(.2/math.sqrt(2)),delta=.02)
        for row in rows[1:]:self.assertIsNone(row["per_channel"])

    def test_comparison_separates_timing_and_rejects_empty(self):
        source=self.delayed()
        comparison=self.root/"compare"
        compare_audio(source,self.root/"tone.wav",comparison)
        result=self.report(comparison,"comparison.json")
        self.assertTrue(result["native_decoded_samples_equal"])
        self.assertFalse(result["canonical_timing_equal_within_source_granularity"])
        self.assertEqual(result["comparison_status"],"DIFFERENT")
        empty=self.root/"empty.wav"
        with wave.open(str(empty),"wb") as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b"")
        with self.assertRaisesRegex(AVError,"positive-length"):
            compare_audio(empty,empty,self.root/"empty-result")
        self.assertFalse((self.root/"empty-result").exists())

    def test_output_collision_and_existing_output_refuse(self):
        source=self.tone();before=sha256(source)
        with self.assertRaises(AVError):measure_audio(source,source)
        with self.assertRaises(AVError):compare_audio(source,source,source)
        self.assertEqual(sha256(source),before)
        existing=self.root/"existing";existing.mkdir()
        with self.assertRaises(AVError):extract_audio(source,existing)

    def test_pcm32_one_bit_difference_is_not_lost(self):
        paths=[]
        for index,value in enumerate((2147483000,2147483001)):
            path=self.root/f"pcm32-{index}.wav"
            with wave.open(str(path),"wb") as out:
                out.setnchannels(1);out.setsampwidth(4);out.setframerate(16000)
                out.writeframes(struct.pack("<i",value)*1600)
            paths.append(path)
        output=self.root/"pcm32-compare"
        compare_audio(paths[0],paths[1],output)
        result=self.report(output,"comparison.json")
        self.assertFalse(result["native_decoded_samples_equal"])
        self.assertEqual(result["comparison_status"],"DIFFERENT")

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required")
    def test_internal_timestamp_gap_remains_missing(self):
        tone=self.tone(seconds=3)
        gap=self.root/"gap.mka"
        self.command("ffmpeg","-nostdin","-v","error","-n","-i",tone,
                     "-af","aselect=not(between(t\\,0.8\\,1.2))","-c:a","pcm_s16le",gap)
        source=probe_source(gap);extracted=self.root/"extracted-gap"
        extract_audio(gap,extracted)
        receipt=self.report(extracted,"audio.json")
        self.assertGreater(len(receipt["segments"]),1)
        output=self.root/"gap-features"
        cue_features(extracted,self.cues(source,[(0,3)]),output)
        row=self.report(output,"features.json")["rows"][0]
        self.assertEqual(row["status"],"PARTIAL")
        self.assertGreater(row["coverage"]["missing_seconds"],.1)
        self.assertIsNone(row["per_channel"])

    def test_loudness_uses_input_and_reports_audio_coverage(self):
        source=self.delayed();output=self.root/"metrics"
        measure_audio(source,output)
        result=self.report(output,"metrics.json")
        self.assertAlmostEqual(result["container_duration_seconds"],6,places=3)
        self.assertAlmostEqual(result["decoded_audio_seconds"],2,places=6)
        self.assertEqual(result["selection"]["status"],"COMPLETE")
        data=result["measurements"]
        self.assertEqual(data["loudnorm_input"]["integrated_gating_threshold_lufs"],float(data["raw_loudnorm_report"]["input_thresh"]))
        self.assertAlmostEqual(data["volumedetect"]["mean_volume_dbfs"],-17,delta=.1)
        self.assertFalse(data["normalization_written_to_source"])

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy required")
    def test_stereo_channels_do_not_cancel(self):
        path=self.tone(stereo=True);source=probe_source(path)
        output=self.root/"features"
        cue_features(path,self.cues(source,[(0,1)]),output)
        channels=self.report(output,"features.json")["rows"][0]["per_channel"]
        self.assertEqual(len(channels),2)
        for channel in channels:self.assertAlmostEqual(channel["rms_dbfs"],-16.9897,delta=.02)

    def test_known_stereo_aac_and_pcm_exports_preserve_layout_and_ordered_samples(self):
        tone=self.tone(stereo=True)
        for name,codec in (("stereo.m4a","aac"),("stereo-pcm.wav","pcm_s32le")):
            with self.subTest(codec=codec):
                source=self.root/name
                self.command("ffmpeg","-nostdin","-v","error","-n","-i",tone,
                             "-channel_layout","stereo","-c:a",codec,source)
                self.assertEqual(probe_source(source)["streams"][0].get("channel_layout"),"stereo")
                original=self.command("ffmpeg","-nostdin","-v","error","-i",source,
                                      "-map","0:a:0","-c:a","pcm_f64le","-f","f64le","-").stdout
                extract=self.root/(codec+"-extract")
                extract_audio(source,extract)
                result=self.report(extract,"audio.json")
                self.assertEqual(result["identity"]["pcm_sha256"],hashlib.sha256(original).hexdigest())
                self.assertEqual(result["artifact_channel_layout"],"stereo")
                self.assertEqual(result["channel_layout_preservation"],"MATCH")
                clip=self.root/(codec+"-clip")
                clip_audio(source,clip,start=.25,end=1.25)
                result=self.report(clip,"audio.json")
                rate=result["identity"]["sample_rate_hz"]
                expected=original[int(.25*rate)*16:int(1.25*rate)*16]
                self.assertEqual(result["identity"]["pcm_sha256"],hashlib.sha256(expected).hexdigest())
                self.assertTrue(result["sample_preservation_verified"])
                self.assertEqual(result["artifact_channel_layout"],"stereo")
                self.assertEqual(result["channel_layout_preservation"],"MATCH")

    def test_unknown_two_channel_layout_is_not_promoted_to_stereo(self):
        source=self.tone(stereo=True)
        self.assertIsNone(probe_source(source)["streams"][0].get("channel_layout"))
        output=self.root/"unknown-stereo"
        extract_audio(source,output)
        result=self.report(output,"audio.json")
        self.assertIsNone(result["identity"]["channel_layout"])
        self.assertIsNone(result["artifact_channel_layout"])
        self.assertEqual(result["channel_layout_preservation"],"SOURCE_LAYOUT_UNKNOWN")
        self.assertTrue(result["sample_preservation_verified"])

    def test_riff_and_rf64_mask_upgrade_preserves_payload_and_size_fields(self):
        tone=self.tone(stereo=True)

        def chunks(path):
            data=path.read_bytes();rows={};offset=12;large_data_length=None
            while offset+8<=len(data):
                tag=data[offset:offset+4];length=struct.unpack_from("<I",data,offset+4)[0]
                if tag==b"data" and length==0xFFFFFFFF:
                    length=large_data_length
                self.assertIsNotNone(length)
                value=data[offset+8:offset+8+length]
                rows[tag]=value
                if tag==b"ds64":large_data_length=struct.unpack_from("<Q",value,8)[0]
                offset+=8+length+(length&1)
            self.assertEqual(offset,len(data))
            return data,rows

        for container in ("never","always"):
            with self.subTest(rf64=container):
                path=self.root/(container+".wav")
                self.command("ffmpeg","-nostdin","-v","error","-n","-i",tone,
                             "-c:a","pcm_f64le","-channel_layout","stereo","-rf64",container,path)
                before,old=chunks(path)
                _preserve_wav_channel_mask({"channel_layout":"stereo","channels":2,"sample_rate_hz":16000},path)
                after,new=chunks(path)
                self.assertEqual(new[b"data"],old[b"data"])
                self.assertEqual(struct.unpack_from("<H",new[b"fmt "])[0],0xFFFE)
                self.assertEqual(struct.unpack_from("<I",new[b"fmt "],20)[0],3)
                self.assertEqual(probe_source(path)["streams"][0].get("channel_layout"),"stereo")
                if container=="always":
                    self.assertEqual(struct.unpack_from("<Q",new[b"ds64"])[0],len(after)-8)
                    self.assertEqual(new[b"ds64"][8:],old[b"ds64"][8:])
                    self.assertEqual(struct.unpack_from("<I",after,4)[0],0xFFFFFFFF)
                else:
                    self.assertEqual(struct.unpack_from("<I",after,4)[0],len(after)-8)

    def test_clip_empty_and_nonfinite_padding_refuse(self):
        path=self.delayed()
        with self.assertRaisesRegex(AVError,"no decoded audio"):
            clip_audio(path,self.root/"empty-clip",start=0,end=1)
        with self.assertRaises(AVError):clip_audio(path,self.root/"nan-clip",start=2,end=3,pad=float("nan"))
        output=self.root/"partial-clip"
        clip_audio(path,output,start=1.5,end=2.5)
        result=self.report(output,"audio.json")
        self.assertEqual(result["selection"]["status"],"PARTIAL")
        self.assertGreater(result["identity"]["sample_frames"],0)
        self.assertAlmostEqual(result["review_mapping"]["parent_start_seconds"],2,places=6)

    @unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("librosa"), "Optional pitch dependencies required")
    def test_pitch_requires_confirmation_and_records_qualification(self):
        path=self.tone(seconds=1);source=probe_source(path);csv_path=self.cues(source,[(0,1)])
        with self.assertRaisesRegex(AVError,"isolated-speech"):
            cue_features(path,csv_path,self.root/"unconfirmed",pitch=True)
        output=self.root/"pitch"
        cue_features(path,csv_path,output,pitch=True,confirm_isolated_speech=True)
        pitch=self.report(output,"features.json")["rows"][0]["per_channel"][0]["pitch"]
        self.assertEqual(pitch["status"],"QUALIFIED_ESTIMATES")
        self.assertGreater(pitch["qualified_frames"],0)
        self.assertAlmostEqual(pitch["qualified_f0_median_hz"],440,delta=3)
        self.assertEqual(pitch["isolation"],"OPERATOR_DECLARED_NOT_VERIFIED")
        self.assertIn("source_center_seconds",pitch["frame_track"][0])


if __name__ == "__main__":
    unittest.main()
