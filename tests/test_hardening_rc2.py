"""Codex review defect analogues. All receipts are synthetic, never perception."""
import copy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from avevidence import __version__, __release_label__
from avevidence.common import AVError, read_json, run, sha256, write_json
from avevidence.audio import extract_audio
from avevidence.bundle import build_bundle
from avevidence.clips import _windows
from avevidence.external import admit_external_clip
from avevidence.inventory import inventory, verify_run
from avevidence.mapping import (timing_basis, validated_segments, map_review_intervals,
                                missing_intervals, union_intervals)
from avevidence.reviews import validate_reviews
import test_reviews as review_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_builder_test_module', ROOT/'scripts/build_release.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


@contextmanager
def fixture():
    f = review_fixture.ReviewTests('test_full_means_declared_required_modalities_not_perception_truth')
    f.setUp()
    try:
        yield f
    finally:
        f.tearDown()


class MappingContractTests(unittest.TestCase):
    def test_union_is_not_an_enclosing_span(self):
        self.assertEqual(missing_intervals([[.5,5.5]], [[0,2],[4,6]]), [[2,4]])
        self.assertEqual(missing_intervals([[.5,5.5]], [[0,2],[2,6]]), [])

    def test_small_real_gap_is_not_hidden_by_clock_tolerance(self):
        self.assertEqual(len(union_intervals([[0,1],[1.0005,2]])), 2)
        self.assertTrue(missing_intervals([[0,2]], [[0,1],[1.0005,2]]))

    def test_packet_group_does_not_merge_a_positive_parent_gap(self):
        from avevidence.external import _segments
        rows=[{"canonical_start":0.,"canonical_end":1.,"duration":1.,"extent_method":"explicit_packet_duration"},
              {"canonical_start":1.001,"canonical_end":2.001,"duration":1.,"extent_method":"explicit_packet_duration"}]
        result,_=_segments(rows,rows,0,.003)
        self.assertEqual(len(result),2)
        self.assertTrue(missing_intervals([[0,2]],[[s['parent_start_seconds'],s['parent_end_seconds']] for s in result]))

    def mapping(self):
        return {'segments':[{'parent_start_seconds':.490667, 'parent_end_seconds':1.514666,
            'derivative_start_seconds':0.,'derivative_end_seconds':1.023}],
            'timing_basis':timing_basis({'time_base':'1/48000'},{'time_base':'1/1000'})}

    def test_quantized_aac_endpoint_contract_accepts_bounded_disagreement(self):
        m=self.mapping()
        self.assertEqual(map_review_intervals(m,[[0,1.023]],[[.490667,1.514666]],3),[[.490667,1.514666]])

    def test_arbitrary_tolerance_inflation_is_rejected(self):
        m=self.mapping(); m['timing_basis']['endpoint_tolerance_seconds']=.1
        with self.assertRaisesRegex(AVError,'clock budget'):
            validated_segments(m)

    def test_time_base_spoofing_is_rejected_against_selected_stream(self):
        m=self.mapping()
        with self.assertRaisesRegex(AVError,'selected stream'):
            validated_segments(m,parent_stream={'time_base':'1/1000000'})

    def test_retimming_is_not_accepted_as_quantization(self):
        m=self.mapping();m['segments'][0]['derivative_end_seconds']=.5
        with self.assertRaisesRegex(AVError,'Retimed'):
            validated_segments(m)

    def test_estimated_tail_is_recorded_by_window_producer(self):
        s={'path':'synthetic','origin_seconds':0,'stream_timeline_bounds':[]}
        stream={'index':0,'codec_type':'video','time_base':'1/10'}
        payload={'frames':[{'pts_time':str(t)} for t in [0,.1,.2]]}
        with patch('avevidence.clips.run',return_value=SimpleNamespace(stdout=json.dumps(payload))):
            _,_,_,extent=_windows(s,stream)
        self.assertTrue(extent['final_frame_extent_estimated'])
        self.assertEqual(extent['estimated_intervals_absolute_seconds'], [[.2,.2+.1]])

    def test_estimated_tail_qualifies_review_but_earlier_interval_does_not(self):
        with fixture() as f:
            f.build_inventory(modalities=['audio'])
            ev,path=f.derivative()
            manifest=read_json(path/'run.json')
            manifest['metadata']['review_mapping']['estimated_parent_intervals_seconds']=[[9,10]]
            (path/'run.json').write_text(json.dumps(manifest),encoding='utf-8')
            ev['run_manifest_sha256']=sha256(path/'run.json')
            rec,caps=f.records(['audio'])
            for x in [rec[0],caps['presentations'][0]]: x['evidence']=copy.deepcopy(ev)
            _,report=f.validate(rec,caps)
            self.assertEqual(report['status'],'FULL_DECLARED_COVERAGE_WITH_ESTIMATED_EXTENT')
            self.assertFalse(report['full_required_exact_extent_coverage'])
            self.assertEqual(report['extent_qualifications'][0]['estimated_intervals_seconds'],[[9.,10.]])
            for x in [rec[0],caps['presentations'][0]]:
                x['intervals_seconds']=[[0,8]];x['evidence']['derivative_intervals_seconds']=[[0,8]]
            _,before=f.validate(rec,caps)
            self.assertEqual(before['extent_qualifications'],[])


class PortableReviewTests(unittest.TestCase):
    def test_relative_derivative_references_survive_removal_and_relocation(self):
        with fixture() as f:
            f.build_inventory(modalities=['audio'])
            ev,derivative=f.derivative();ev['run_manifest_path']=derivative.name+'/run.json'
            rec,caps=f.records(['audio'])
            for x in [rec[0],caps['presentations'][0]]:x['evidence']=copy.deepcopy(ev)
            _,report=f.validate(rec,caps)
            original=f.root/f'review-run-{f.counter}'
            moved=f.root/'relocated';original.rename(moved)
            shutil.rmtree(derivative);shutil.rmtree(f.inventory_path);f.receipt.unlink();f.media.unlink()
            verify_run(moved)
            inv=moved/report['portable_declarations']['inventory']
            result=validate_reviews(moved/'reviews.json',inv,moved/'capabilities.json',f.root/'again')
            self.assertTrue(result['metadata']['full_required_coverage'])
            self.assertFalse(Path(read_json(moved/'reviews.json')[0]['evidence']['run_manifest_path']).is_absolute())

    def test_contact_sheet_transitive_frame_run_closure_is_portable(self):
        with fixture() as f:
            # Existing fixture builds/reviews actual synthetic audio/AV/frames/sheet.
            f.test_actual_synthetic_audio_clip_frame_contact_producer_integration()
            output=f.root/f'review-run-{f.counter}'
            report=read_json(output/'review_validation.json')
            moved=f.root/'moved-with-closure';output.rename(moved)
            for name in ['real-audio','real-av','real-frames','real-contacts','real-producer-inventory']:
                shutil.rmtree(f.root/name)
            f.receipt.unlink();f.media.unlink()
            verify_run(moved)
            result=validate_reviews(moved/'reviews.json',moved/report['portable_declarations']['inventory'],
                                    moved/'capabilities.json',f.root/'revalidate-closure')
            self.assertTrue(result['metadata']['full_required_coverage'])


class ReleaseBindingTests(unittest.TestCase):
    def test_uppercase_current_report_is_not_retired_lowercase(self):
        names=builder.stored_files(ROOT)
        self.assertNotIn('reports/wheel_smoke.json', names)
        files=builder.select_members(ROOT)
        self.assertIn('scripts/build_release.py',dict(files))

    def test_binding_matches_exact_selected_paths(self):
        files=builder.select_members(ROOT);binding=builder.source_binding(files)
        expected=sorted(n for n,_ in files if not n.startswith('reports/'))
        self.assertEqual([x['path'] for x in binding['members']],expected)
        self.assertEqual(binding['member_count'],len(expected))
        self.assertNotIn('SHA256SUMS.txt',expected)

    def test_stale_exact_case_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'reports').mkdir();(r/'reports/wheel_smoke.json').write_text('{}')
            with self.assertRaisesRegex(AVError,'Stale'):
                builder.select_members(r)

    def test_tampered_aggregate_and_runtime_bindings_are_rejected(self):
        files=builder.select_members(ROOT)
        runtime={n.removeprefix('avevidence/'):sha256(p) for n,p in files if n.startswith('avevidence/') and n.endswith('.py')}
        self.assertIn('__init__.py',runtime)
        self.assertIn('reuse/__init__.py',runtime)
        tests={Path(n).name:sha256(p) for n,p in files if n.startswith('tests/test_') and n.endswith('.py')}
        reports={}
        for name in ('REGRESSION_LINUX','CLEAN_EXTRACTION_REGRESSION'):
            reports[f'reports/{name}.json']={'passed':True,'environment':{'tool_version':__version__,
                'implementation_sha256':runtime},'test_source_sha256':tests}
        for name in ('WHEEL_SMOKE','REAL_MEDIA_SMOKE'):
            reports[f'reports/{name}.json']={'passed':True,'release':{'version':__version__},'implementation_sha256':runtime}
        reports['reports/BUILD_REPRODUCIBILITY.json']={'passed':True,'release_label':__release_label__,
                                                     'source_binding':builder.source_binding(files)}
        selected=[x for x in files if not x[0].startswith('reports/')]+[(n,ROOT/n) for n in builder.REQUIRED_REPORTS]
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'reports').mkdir()
            for name in ('IMPLEMENTATION_CONFORMANCE','RELEASE_VALIDATION'):
                (r/f'reports/{name}.md').write_text('SYNTHETIC TEST REPORT '+__release_label__)
            with patch.object(builder,'_json_report',side_effect=lambda root,name:reports[name]):
                builder._validate_current_reports(selected,r)
                reports['reports/BUILD_REPRODUCIBILITY.json']['source_binding']['member_count']+=1
                with self.assertRaisesRegex(AVError,'source binding'):
                    builder._validate_current_reports(selected,r)
                reports['reports/BUILD_REPRODUCIBILITY.json']['source_binding']=builder.source_binding(files)
                reports['reports/REGRESSION_LINUX.json']['environment']['implementation_sha256']={}
                with self.assertRaisesRegex(AVError,'runtime/tests'):
                    builder._validate_current_reports(selected,r)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class CodecInteropTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ave-rc2-');self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def audio(self, name='p.m4a', codec='aac', length=3):
        p=self.root/name
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             f'sine=frequency=457:sample_rate=48000:duration={length}','-ac','2','-c:a',codec,p])
        return p

    def review(self,parent,admitted,wanted,modality="audio"):
        result=read_json(admitted/('external_clip.json' if (admitted/'external_clip.json').exists() else 'clip.json'))
        m=next(x for x in result['review_mappings'] if x['modality']==modality)
        kind='audio' if modality=='audio' else 'video'
        mechanism='audio_playback' if modality=='audio' else 'video_playback'
        inv_config=self.root/'inventory-config.json'
        inv_config.write_text(json.dumps({'sources':[{'logical_source_id':'p','materialization_id':'p-file',
            'path':str(parent),'selected_streams':{kind:0},'required_modalities':[modality],
            'required_intervals_seconds':{modality:wanted},'preferred':True}]}))
        inv=self.root/'inv';data=inventory(inv_config,inv);s=data['sources'][0]
        derivative=[]
        for a,b in wanted:
            for seg in m['segments']:
                pa,pb=seg['parent_start_seconds'],seg['parent_end_seconds']
                da,db=seg['derivative_start_seconds'],seg['derivative_end_seconds']
                lo,hi=max(a,pa),min(b,pb)
                if lo<hi: derivative.append([da+(lo-pa)*(db-da)/(pb-pa),da+(hi-pa)*(db-da)/(pb-pa)])
        receipt=self.root/'receipt';receipt.write_text('Synthetic declaration only; no perception occurred.')
        rr={'kind':'human_attestation','path':str(receipt),'sha256':sha256(receipt)}
        who={'id':'tester','type':'human','name':'Synthetic fixture'}
        ev={'kind':'derivative','run_manifest_path':str(admitted/'run.json'),'run_manifest_sha256':sha256(admitted/'run.json'),
            'artifact_path':m['artifact_path'],'artifact_sha256':m['artifact_sha256'],
            'derivative_stream_index':m['derivative_stream_index'],'derivative_intervals_seconds':derivative}
        base={'logical_source_id':'p','materialization_id':'p-file','source_sha256':s['sha256'],
              'stream_index':0,'modality':modality,'capability_id':'cap','intervals_seconds':wanted,'evidence':ev}
        cap={'capability_id':'cap','reviewer':who,'modality':modality,'mechanism':mechanism,
             'verified_at':'2025-01-01T00:00:00Z','receipt':rr}
        pre=dict(base,presentation_id='pres',reviewer_id='tester',reviewer_type='human',mechanism=mechanism,
                 presented_at='2025-01-01T00:01:00Z',receipt=rr)
        rec=dict(base,schema='ave.review.v1',review_id='review',reviewer=who,reviewed_at='2025-01-01T00:02:00Z',
                 presentation_id='pres',observation='Synthetic regression, not listening.')
        cp=self.root/'caps.json';rp=self.root/'reviews.json'
        cp.write_text(json.dumps({'schema':'ave.capabilities.v1','capabilities':[cap],'presentations':[pre]}))
        rp.write_text(json.dumps([rec]))
        return validate_reviews(rp,inv,cp,self.root/'reviewed')

    def test_whole_aac_m4a_to_mka_priming_admission_and_review(self):
        parent=self.audio();clip=self.root/'c.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-i',parent,'-c','copy',clip])
        out=self.root/'admitted';admit_external_clip(parent,clip,out,parent_start=0,parent_end=3)
        data=read_json(out/'external_clip.json')
        self.assertEqual(data['overall_status'],'VERIFIED_ALL_SELECTED_MODALITIES')
        self.assertTrue(data['modalities'][0]['packet_side_data']['parent'])
        self.assertGreaterEqual(min(s['parent_start_seconds'] for s in data['review_mappings'][0]['segments']),0)
        self.assertTrue(self.review(parent,out,[[0,3]])['metadata']['full_required_coverage'])

    def test_interior_aac_copy_quantized_mapping_and_review(self):
        parent=self.audio();clip=self.root/'c.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-ss','0.5','-to','1.5','-i',parent,'-c','copy',clip])
        out=self.root/'admitted';admit_external_clip(parent,clip,out,parent_start=.5,parent_end=1.5)
        data=read_json(out/'external_clip.json')
        self.assertEqual(data['overall_status'],'VERIFIED_ALL_SELECTED_MODALITIES')
        self.assertTrue(self.review(parent,out,[[.5,1.5]])['metadata']['full_required_coverage'])

    def test_external_internal_audio_gap_is_not_full_coverage(self):
        parent=self.root/'gapped.mka';clip=self.root/'copy.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             'sine=frequency=457:sample_rate=48000:duration=6','-af',"aselect=not(between(t\\,2\\,4))",'-c:a','flac',parent])
        run(['ffmpeg','-v','error','-nostdin','-n','-i',parent,'-c','copy','-metadata','title=copy',clip])
        out=self.root/'admitted';admit_external_clip(parent,clip,out,parent_start=.5,parent_end=5.5)
        data=read_json(out/'external_clip.json')
        self.assertEqual(data['overall_status'],'PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED')
        gaps=data['modalities'][0]['uncovered_parent_intervals_seconds']
        self.assertTrue(any(a<3<b for a,b in gaps))
        with self.assertRaisesRegex(AVError,'gap|mapping'):
            self.review(parent,out,[[.5,5.5]])

    def test_forensic_stereo_wav_to_practical_is_exact_file_copy(self):
        parent=self.audio('p.flac','flac',1);f=self.root/'forensic'
        extract_audio(parent,f,storage_profile='forensic')
        out=self.root/'practical';extract_audio(f/'audio.wav',out,storage_profile='practical')
        data=read_json(out/'audio.json')
        self.assertEqual(data['artifact_channel_layout'],'stereo')
        self.assertEqual(data['container_identity'],'EXACT_FILE_SHA256_MATCH')
        self.assertEqual(sha256(f/'audio.wav'),sha256(out/'audio.wav'))
        verify_run(out)

    def test_default_bundle_accepts_forensic_stereo_wav(self):
        parent=self.audio('p.flac','flac',1);f=self.root/'forensic'
        extract_audio(parent,f,storage_profile='forensic')
        out=self.root/'bundle';build_bundle(f/'audio.wav',out)
        self.assertEqual(read_json(out/'audio/audio.json')['channel_layout_preservation'],'MATCH')

    def test_native_f64_matroska_layout_follows_admitted_source(self):
        parent=self.audio('p.flac','flac',1);f=self.root/'forensic'
        extract_audio(parent,f,storage_profile='forensic');mka=self.root/'p.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-i',f/'audio.wav','-c','copy',mka])
        out=self.root/'practical';extract_audio(mka,out,storage_profile='practical')
        # Matroska PCM layout reporting differs across FFmpeg versions. A
        # layout explicitly admitted by the source probe must be preserved;
        # an unknown layout must remain unknown. Count alone proves neither.
        result=read_json(out/'audio.json')
        admitted=result['identity']['channel_layout']
        self.assertEqual(result['artifact_channel_layout'],admitted)
        self.assertEqual(result['channel_layout_preservation'],
                         'MATCH' if admitted is not None else 'SOURCE_LAYOUT_UNKNOWN')
        self.assertTrue(result['sample_preservation_verified'])

    def test_native_f64_matroska_known_mono_uses_mask_preservation(self):
        parent=self.root/'mono.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             'sine=frequency=457:sample_rate=48000:duration=1','-c:a','pcm_f64le',parent])
        out=self.root/'mono-practical';extract_audio(parent,out,storage_profile='practical')
        self.assertEqual(read_json(out/'audio.json')['artifact_channel_layout'],'mono')

    def test_estimated_video_tail_propagates_from_clip_producer_to_review(self):
        from avevidence.clips import clip_av
        from avevidence.common import probe_source
        parent=self.root/'video.mkv'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             'testsrc2=size=64x64:rate=10:duration=1','-c:v','ffv1',parent])
        def no_duration_fields(args, **kw):
            value=run(args,**kw)
            if '-show_frames' in args:
                payload=json.loads(value.stdout)
                for frame in payload.get('frames',[]):
                    frame.pop('duration_time',None);frame.pop('pkt_duration_time',None)
                value.stdout=json.dumps(payload)
            return value
        def no_derivative_endpoint(path):
            value=probe_source(path)
            if Path(path).name=='clip.mkv': value['stream_timeline_bounds']=[]
            return value
        out=self.root/'clip'
        with patch('avevidence.clips.run',side_effect=no_duration_fields), patch('avevidence.clips.probe_source',side_effect=no_derivative_endpoint):
            clip_av(parent,out,start=0,end=1)
        mapping=read_json(out/'clip.json')['review_mappings'][0]
        self.assertTrue(mapping['extent_derivation']['final_frame_extent_estimated'])
        self.assertAlmostEqual(mapping['estimated_parent_intervals_seconds'][0][0],.9)
        result=self.review(parent,out,[[0,1]],modality='motion')
        self.assertFalse(result['metadata']['full_required_exact_extent_coverage'])
        report=read_json(self.root/'reviewed/review_validation.json')
        self.assertEqual(report['status'],'FULL_DECLARED_COVERAGE_WITH_ESTIMATED_EXTENT')

    def test_h264_bframe_aac_coarse_clock_copy_remains_reviewable(self):
        parent=self.root/'parent.mp4';clip=self.root/'copy.mkv'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
            'testsrc2=size=128x96:rate=24:duration=4','-f','lavfi','-i',
            'sine=frequency=457:sample_rate=48000:duration=4','-c:v','libx264','-g','24',
            '-bf','2','-c:a','aac',parent])
        run(['ffmpeg','-v','error','-nostdin','-n','-ss','1.2','-to','3.0',
             '-i',parent,'-map','0:v:0','-map','0:a:0','-c','copy',clip])
        out=self.root/'admitted';admit_external_clip(parent,clip,out,parent_start=1.2,parent_end=3)
        data=read_json(out/'external_clip.json')
        self.assertEqual(data['overall_status'],'VERIFIED_ALL_SELECTED_MODALITIES')
        video=next(m for m in data['modalities'] if m['modality']=='motion')
        self.assertIn('decoded_video_coverage',video)
        self.assertTrue(self.review(parent,out,[[1.2,3]],modality='motion')['metadata']['full_required_coverage'])

    def test_overlap_remains_safe_refusal_not_implicit_clock_repair(self):
        parent=self.root/'overlap.mka'
        run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i',
             'sine=frequency=457:sample_rate=48000:duration=1','-af','asetpts=PTS/2','-c:a','pcm_s16le',parent])
        with self.assertRaisesRegex(AVError,'Overlapping audio timestamps'):
            extract_audio(parent,self.root/'refused',storage_profile='practical')

if __name__=='__main__':unittest.main()
