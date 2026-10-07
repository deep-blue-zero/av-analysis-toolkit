"""Synthetic signal/clock regressions, not perceptual or GBC validation."""
import contextlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

try:
    import numpy as np
except ImportError:
    np = None

from avevidence.audio import clip_audio, extract_audio
from avevidence.audio_tracks import (audio_track, _spectral_rows, _stereo_rows,
    _onsets_and_tempo, _validate_segments, _waveform_and_summary)
from avevidence.audio_render import render_audio
from avevidence.cli import main, parser
from avevidence.common import AVError, read_json, read_csv, run, sha256
from avevidence.inventory import verify_run
from avevidence.packaging import pack_run, verify_archive


def part(n, start=0., rate=16000):
    return dict(segment_id="s000",input_sample_start=0,input_sample_end=n,
                source_start_seconds=start,source_end_seconds=start+n/rate)


def tracks(x, rate=16000, n=640, hop=160, start=0., music=False):
    return list(_spectral_rows(x,rate,n,hop,part(len(x),start,rate),0,-45,music=music))


@unittest.skipUnless(np is not None, "NumPy optional extra required")
class DSPTests(unittest.TestCase):
    def test_sine_rms_peak_centroid_bandwidth(self):
        sr=16000;t=np.arange(sr)/sr;x=.2*np.sin(2*np.pi*1000*t)
        rows=[r for k,r,_ in tracks(x) if k=="track"]
        self.assertAlmostEqual(rows[0]["rms_dbfs"],20*math.log10(.2/math.sqrt(2)),places=8)
        self.assertAlmostEqual(rows[0]["crest_factor_db"],20*math.log10(math.sqrt(2)),places=8)
        self.assertAlmostEqual(rows[0]["spectral_centroid_hz"],1000,delta=1)
        self.assertLess(rows[0]["spectral_bandwidth_hz"],50)
        self.assertIsNone(rows[0]["spectral_flux_l1"])
        self.assertEqual(rows[1]["input_sample_start"],160)
        self.assertAlmostEqual(rows[1]["difference_support_start_seconds"],0)

    def test_silence_is_null_not_fake_measurement(self):
        result=tracks(np.zeros(16000));rows=[r for k,r,_ in result if k=="track"]
        for r in rows:
            for k in ("rms_dbfs","sample_peak_dbfs","crest_factor_db","spectral_centroid_hz","spectral_flatness_power"):
                self.assertIsNone(r[k])
            self.assertTrue(r["low_energy"])
        events,tempo=_onsets_and_tempo(rows,16000,160)
        self.assertEqual(events,[]);self.assertEqual(tempo["candidates"],[])
        json.dumps(rows,allow_nan=False)

    def test_tail_is_counted_and_waveform_keeps_last_sample(self):
        x=np.zeros(651);x[-1]=.8
        final=tracks(x)[-1][1]
        self.assertEqual(final["unmeasured_tail_samples"],11)
        wave_rows,summary=_waveform_and_summary(x,part(len(x)),16000,0)
        self.assertEqual(wave_rows[-1]["input_sample_end"],651)
        self.assertEqual(summary["sample_max"],.8)
        self.assertAlmostEqual(summary["sample_peak_dbfs"],20*math.log10(.8))

    def test_nonfinite_refused(self):
        for v in (np.nan,np.inf):
            x=np.zeros(16000);x[10]=v
            with self.assertRaises(AVError):tracks(x)

    def test_absolute_time_not_rebased(self):
        rows=[r for k,r,_ in tracks(np.ones(16000),start=72.125) if k=="track"]
        self.assertAlmostEqual(rows[0]["source_start_seconds"],72.125)
        self.assertAlmostEqual(rows[0]["source_center_seconds"],72.145)
        self.assertEqual(rows[0]["input_sample_end"],640)

    def test_stereo_in_phase_and_antiphase(self):
        x=np.sin(np.arange(1600)*.1)
        for polarity,fraction in ((1,0),(-1,1)):
            stereo=np.column_stack((x,polarity*x))
            rows=list(_stereo_rows(stereo,part(len(x)),16000,640,160))
            self.assertAlmostEqual(rows[0]["correlation"],polarity)
            self.assertAlmostEqual(rows[0]["side_energy_fraction"],fraction)
            self.assertLess(rows[0]["mid_side_energy_identity_error"],1e-14)
            self.assertIsNone(rows[0]["side_rms_dbfs" if polarity==1 else "mid_rms_dbfs"])

    def test_stereo_silent_or_constant_correlation_is_undefined(self):
        for x in (np.zeros((1600,2)),np.ones((1600,2))):
            rows=list(_stereo_rows(x,part(len(x)),16000,640,160))
            self.assertIsNone(rows[0]["correlation"])

    def test_spectral_change_detects_frequency_switch(self):
        sr=16000;t=np.arange(sr*2)/sr
        x=.2*np.sin(2*np.pi*np.where(t<1,440,2000)*t)
        rows=[r for k,r,_ in tracks(x) if k=="track"]
        events,_=_onsets_and_tempo(rows,sr,160)
        self.assertTrue(any(.95<e["source_center_seconds"]<1.05 for e in events))
        self.assertLess(rows[10]["spectral_centroid_hz"],500)
        self.assertGreater(rows[-10]["spectral_centroid_hz"],1900)

    def test_pitch_class_fold_and_silence(self):
        sr=16000;t=np.arange(sr)/sr
        results=tracks(.3*np.sin(2*np.pi*440*t),n=2048,music=True)
        row=next(r for k,r,_ in results if k=="chroma")
        keys=[k for k in row if k.startswith('chroma_') and k!='chroma_change_l1']
        self.assertEqual(max(keys,key=lambda k:row[k]),"chroma_A")
        self.assertAlmostEqual(sum(row[k] for k in keys),1)
        silent=next(r for k,r,_ in tracks(np.zeros(sr),music=True) if k=="chroma")
        self.assertIsNone(silent["chroma_A"])

    def test_tempo_hypotheses_on_click_train(self):
        rows=[]
        for i in range(1000):
            rows.append(dict(segment_id='s000',channel_index=0,source_center_seconds=i*.01,
                difference_support_start_seconds=max(0,i*.01-.04),source_end_seconds=i*.01+.02,
                onset_strength_amplitude=1. if i%50==25 else 0.))
        events,result=_onsets_and_tempo(rows,16000,160)
        self.assertEqual(len(events),20)
        self.assertTrue(any(abs(c['bpm']-120)<1 for c in result['candidates']))
        self.assertIn('not confidence',result['scope'])

    def test_bad_map_refuses_retiming_and_missing_samples(self):
        good=dict(sample_rate_hz=16000,sample_frames=16000,
            segments=[dict(sample_start=0,sample_end=16000,source_start_seconds=3.,source_end_seconds=4.)])
        _validate_segments(good)
        bad=json.loads(json.dumps(good));bad['segments'][0]['source_end_seconds']=5
        with self.assertRaises(AVError):_validate_segments(bad)
        bad=json.loads(json.dumps(good));bad['segments'][0]['sample_start']=1
        with self.assertRaises(AVError):_validate_segments(bad)

    def test_mel_payload_is_finite_and_frequency_localized(self):
        sr=16000;x=.3*np.sin(2*np.pi*440*np.arange(sr)/sr)
        payload=list(_spectral_rows(x,sr,1024,160,part(len(x)),0,-45,mel=True))[-1][2]
        self.assertEqual(payload['mel_power'].shape[0],64)
        self.assertTrue(np.isfinite(payload['mel_power']).all())
        peak=int(np.argmax(payload['mel_power'].mean(axis=1)))
        self.assertLess(abs(float(payload['mel_center_hz'][peak])-440),60)
        self.assertTrue(np.all(np.diff(payload['mel_center_hz'])>0))

    def test_overview_pooling_limits_shape_and_retains_support(self):
        x=np.sin(np.arange(16000)*.1)
        results=list(_spectral_rows(x,16000,640,160,part(len(x),17),0,-45,overview_cols=20,overview_bins=50))
        payload=results[-1][2]
        self.assertEqual(payload['amplitude'].shape,(50,20))
        self.assertAlmostEqual(payload['source_support_start_seconds'][0],17)
        self.assertAlmostEqual(payload['source_support_end_seconds'][-1],18)


@unittest.skipUnless(np is not None and shutil.which('ffmpeg') and shutil.which('ffprobe'),'NumPy and FFmpeg required')
class TrackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ave-audio-tracks-');self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def tone(self,name='tone.wav',seconds=1,stereo=False):
        p=self.root/name;rate=16000
        x=.2*np.sin(2*np.pi*1000*np.arange(round(rate*seconds))/rate)
        samples=np.column_stack((x,-x)) if stereo else x
        with wave.open(str(p),'wb') as f:
            f.setnchannels(2 if stereo else 1);f.setsampwidth(2);f.setframerate(rate)
            f.writeframes((samples*32767).astype('<i2').tobytes())
        return p
    def test_full_run_manifests_images_and_archive(self):
        p=self.tone(seconds=4);before=sha256(p);out=self.root/'result'
        audio_track(p,out,profile='music',loudness='require')
        data=read_json(out/'audio_tracks.json')
        self.assertEqual(data['schema'],'ave.audio_tracks.v1')
        self.assertEqual(data['perceptual_review'],'NOT_PERFORMED')
        self.assertEqual(len(data['tracks']),3)
        self.assertGreater(len(data['renders']),5)
        self.assertEqual(before,sha256(p));verify_run(out,verify_sources=True)
        archive=self.root/'evidence.zip';pack_run(out,archive)
        self.assertTrue(verify_archive(archive)['integrity_valid'])
    def test_ebu_warmup_and_block_end_timing(self):
        p=self.tone(seconds=3.5);out=self.root/'ebu'
        audio_track(p,out,loudness='require',render=False,spectral_scales_ms=())
        rows=read_csv(out/'loudness.csv')
        self.assertAlmostEqual(float(rows[0]['source_measurement_end_seconds']),.1)
        self.assertEqual(rows[0]['momentary_lufs'],'')
        self.assertNotEqual(rows[3]['momentary_lufs'],'')
        self.assertEqual(rows[28]['short_term_lufs'],'')
        self.assertNotEqual(rows[29]['short_term_lufs'],'')
        self.assertLess(abs(float(rows[-1]['short_term_lufs'])-float(rows[-1]['momentary_lufs'])),.1)
    def test_unknown_pair_requires_opt_in(self):
        p=self.tone(stereo=True);out=self.root/'unknown'
        audio_track(p,out,render=False,spectral_scales_ms=())
        data=read_json(out/'audio_tracks.json');self.assertEqual(data['stereo']['status'],'NOT_APPLIED')
        self.assertIn('LOUDNESS_SKIPPED_UNKNOWN_LAYOUT',data['warnings'])
        declared=self.root/'declared';audio_track(p,declared,stereo='channels-0-1',render=False,spectral_scales_ms=())
        d=read_json(declared/'audio_tracks.json')
        self.assertEqual(d['stereo']['basis'],'OPERATOR_DECLARED_CHANNEL_PAIR_NOT_VERIFIED')
        self.assertAlmostEqual(float(read_csv(declared/'stereo.csv')[0]['correlation']),-1)
        with self.assertRaises(AVError):audio_track(p,self.root/'required',loudness='require')
    def test_known_stereo_detected(self):
        p=self.tone(stereo=True);known=self.root/'known.wav'
        run(['ffmpeg','-v','error','-nostdin','-n','-i',p,'-channel_layout','stereo','-c:a','pcm_s32le',known])
        out=self.root/'known-run';audio_track(known,out,loudness='require',render=False,spectral_scales_ms=())
        self.assertEqual(read_json(out/'audio_tracks.json')['stereo']['basis'],'SOURCE_DECLARED_STEREO')
    def test_extract_clip_parent_clock_is_preserved(self):
        p=self.tone(seconds=3);clip=self.root/'clip';clip_audio(p,clip,start=1.,end=2.)
        out=self.root/'tracks';audio_track(clip,out,render=False,spectral_scales_ms=())
        data=read_json(out/'audio_tracks.json')
        self.assertEqual(data['parent_source_sha256'],sha256(p))
        self.assertAlmostEqual(data['segments'][0]['source_start_seconds'],1.)
        self.assertAlmostEqual(float(read_csv(out/data['tracks'][0]['path'])[0]['source_start_seconds']),1.)
    def test_gap_no_features_or_flux_bridge(self):
        p=self.tone(seconds=3);gap=self.root/'gap.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-i',p,'-af','aselect=not(between(t\\,0.8\\,1.2))','-c:a','pcm_s16le',gap])
        out=self.root/'gapped';audio_track(gap,out,render=False,spectral_scales_ms=(),loudness='require')
        data=read_json(out/'audio_tracks.json');self.assertEqual(data['selection']['status'],'PARTIAL')
        self.assertGreaterEqual(len(data['segments']),2)
        for tr in data['tracks']:
            rows=read_csv(out/tr['path'])
            self.assertEqual(rows[0]['spectral_flux_l1'],'')
            seg=next(s for s in data['segments'] if s['segment_id']==tr['segment_id'])
            self.assertLessEqual(float(rows[-1]['source_end_seconds']),seg['source_end_seconds']+1e-8)
        loud=read_csv(out/'loudness.csv');second=next(r for r in loud if r['segment_id']=='s001')
        self.assertEqual(second['momentary_lufs'],'')
    def test_refuse_collision_duration_and_frame_budget(self):
        p=self.tone(seconds=1);original=sha256(p)
        for kwargs in ({'max_duration':.5},{'max_frames':1},{'window_ms':10,'hop_ms':20}):
            with self.assertRaises(AVError):audio_track(p,self.root/'bad',**kwargs)
            self.assertFalse((self.root/'bad').exists())
        with self.assertRaises(AVError):audio_track(p,p)
        self.assertEqual(original,sha256(p))
    def test_rerender_tamper_detection_and_no_decode(self):
        p=self.tone();out=self.root/'tracks';audio_track(p,out,render=False,spectral_scales_ms=())
        with patch('avevidence.audio._decode',side_effect=AssertionError('must not decode')):
            render_audio(out,self.root/'images')
        verify_run(self.root/'images')
        data=read_json(out/'audio_tracks.json');(out/data['tracks'][0]['path']).write_text('changed')
        with self.assertRaises(AVError):render_audio(out,self.root/'bad-render')

    def test_rerender_rejects_measurement_changed_during_render(self):
        import avevidence.audio_render as renderer
        p=self.tone();out=self.root/'tracks'
        audio_track(p,out,render=False,spectral_scales_ms=(),loudness='off')
        original_render=renderer.render_into
        def changed(root,output,report):
            refs=original_render(root,output,report)
            with (out/'waveform.csv').open('a',encoding='utf-8') as f:f.write('\n')
            return refs
        target=self.root/'bad-render'
        with patch.object(renderer,'render_into',side_effect=changed):
            with self.assertRaises(AVError):render_audio(out,target)
        self.assertFalse(target.exists())

    def test_rerender_rejects_rebound_manifest_during_render(self):
        import avevidence.audio_render as renderer
        p=self.tone();out=self.root/'tracks'
        audio_track(p,out,render=False,spectral_scales_ms=(),loudness='off')
        original_render=renderer.render_into
        def changed(root,output,report):
            refs=original_render(root,output,report)
            artifact=out/'waveform.csv'
            with artifact.open('a',encoding='utf-8') as f:f.write('\n')
            manifest=read_json(out/'run.json')
            item=next(a for a in manifest['artifacts'] if a['path']=='waveform.csv')
            item.update(sha256=sha256(artifact),size_bytes=artifact.stat().st_size)
            (out/'run.json').write_text(json.dumps(manifest),encoding='utf-8')
            verify_run(out)  # Internally valid, but not the input originally admitted.
            return refs
        target=self.root/'bad-render'
        with patch.object(renderer,'render_into',side_effect=changed):
            with self.assertRaises(AVError):render_audio(out,target)
        self.assertFalse(target.exists())

    def test_rerender_snapshot_isolates_transient_original_changes(self):
        import avevidence.audio_render as renderer
        p=self.tone();out=self.root/'tracks'
        audio_track(p,out,render=False,spectral_scales_ms=(),loudness='off')
        control=self.root/'control';render_audio(out,control)
        original_render=renderer.render_into
        def transient(root,output,report):
            self.assertNotEqual(Path(root).resolve(),out.resolve())
            artifact=out/'waveform.csv';original=artifact.read_bytes()
            try:
                artifact.write_text('temporary invalid data',encoding='utf-8')
                return original_render(root,output,report)
            finally:artifact.write_bytes(original)
        target=self.root/'snapshot-render'
        with patch.object(renderer,'render_into',side_effect=transient):render_audio(out,target)
        verify_run(target,verify_sources=True)
        expected={p.name:sha256(p) for p in (control/'renders').glob('*.png')}
        self.assertEqual(expected,{p.name:sha256(p) for p in (target/'renders').glob('*.png')})
        self.assertFalse(any(p.name.startswith('.verified-input-') for p in target.iterdir()))
    def test_missing_loudness_is_explicit_and_require_fails(self):
        p=self.tone();out=self.root/'soft'
        with patch('avevidence.audio_tracks._ebu',side_effect=AVError('filter unavailable')):
            audio_track(p,out,render=False,spectral_scales_ms=())
            data=read_json(out/'audio_tracks.json')
            self.assertIn('LOUDNESS_UNAVAILABLE_s000',data['warnings'])
            with self.assertRaises(AVError):audio_track(p,self.root/'hard',loudness='require',render=False,spectral_scales_ms=())
    def test_cli_music_defaults_and_empty_extra_scales(self):
        p=self.tone();out=self.root/'cli'
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            result=main(['audio-track',str(p),str(out),'--profile','music','--spectral-scales-ms','--no-render'])
        self.assertEqual(result,0)
        self.assertEqual(len(read_json(out/'audio_tracks.json')['tracks']),1)
    def test_ebu_cumulative_peak_does_not_drop_with_signal(self):
        src=self.root/'peak.wav'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             'aevalsrc=if(lt(t\\,1)\\,0.4\\,0.04)*sin(2*PI*1000*t):s=48000:d=2','-c:a','pcm_s32le',src])
        out=self.root/'peak-run';audio_track(src,out,render=False,spectral_scales_ms=(),loudness='require')
        rows=read_csv(out/'loudness.csv')
        self.assertAlmostEqual(float(rows[-1]['cumulative_true_peak_dbtp']),float(rows[8]['cumulative_true_peak_dbtp']),places=2)
        self.assertGreater(float(rows[8]['momentary_lufs'])-float(rows[-1]['momentary_lufs']),18)

    def test_no_complete_spectrum_still_keeps_waveform(self):
        p=self.tone(seconds=.005);out=self.root/'tiny'
        audio_track(p,out,render=False,spectral_scales_ms=(),loudness='off')
        d=read_json(out/'audio_tracks.json')
        self.assertEqual(d['tracks'][0]['status'],'INSUFFICIENT_SAMPLES')
        self.assertEqual(d['spectra'],[])
        self.assertEqual(d['segments'][0]['channels'][0]['sample_frames'],80)

    def test_multichannel_is_not_implicitly_stereo(self):
        src=self.root/'six.wav'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','anullsrc=r=16000:cl=5.1','-t','0.2','-c:a','pcm_s32le',src])
        out=self.root/'six';audio_track(src,out,render=False,spectral_scales_ms=())
        data=read_json(out/'audio_tracks.json')
        self.assertEqual(len(data['tracks']),6);self.assertEqual(data['stereo']['status'],'NOT_APPLIED')
        with self.assertRaises(AVError):audio_track(src,self.root/'badpair',stereo='channels-0-1')


if __name__=='__main__':unittest.main()
