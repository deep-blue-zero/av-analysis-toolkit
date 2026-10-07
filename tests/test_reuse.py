"""Recording-reuse regressions. Synthetic signals do not validate acting perception."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
    from scipy.io.wavfile import write as wavwrite, read as wavread
    from scipy.signal import butter, sosfilt
except ImportError as exc:
    raise unittest.SkipTest("Install the [reuse] extra for numerical reuse regressions") from exc

from avevidence.common import AVError, read_json, run
from avevidence.inventory import verify_run
from avevidence.reuse.correlate import compare
from avevidence.reuse.fingerprint import Index, descriptors
from avevidence.reuse.graph import ingest, snapshot, decide, promote, annotate
from avevidence.reuse.media import occurrence, proxy
from avevidence.reuse.periodicity import periodicity
from avevidence.reuse.policy import audit
from avevidence.reuse.workflow import run_scan, match_record, export


RATE = 16000


def signal(seed=1, seconds=1.5):
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds*RATE))/RATE
    nodes = rng.uniform(120, 300, 8)
    f0 = np.interp(t, np.linspace(0, seconds, len(nodes)), nodes)
    phase = 2*np.pi*np.cumsum(f0)/RATE
    envelope = (.15+.85*np.sin(np.pi*t*(3+seed*.13))**4)*np.minimum(1, t/.03)*np.minimum(1, (seconds-t)/.03)
    harmonic = sum(np.sin(phase*k+rng.uniform(-.1, .1))/k for k in range(1, 12))
    breath = sosfilt(butter(3, [300, 7000], btype='bandpass', fs=RATE, output='sos'), rng.normal(size=len(t)))
    return (.15*envelope*harmonic+.012*breath)[:, None]


def record(label, x, quality='isolated_voice_asset', kind='performance', vocal='verified_voice'):
    from avevidence.performance_cache import digest
    return {'occurrence_id': label, 'source_sha256': digest(label), 'audio_stream': 0,
            'sample_start': 0, 'sample_end': len(x), 'source_start_s': 0., 'source_end_s': len(x)/RATE,
            'sample_rate_hz': RATE, 'channels': x.shape[1], 'speaker': 'Synthetic actor label',
            'quality': quality, 'unit_kind': kind, 'vocal_status': vocal,
            'vocal_basis': 'Synthetic truth fixture, not listening', 'measurement_exclusions': [],
            'codec_observed': 'pcm_f64le', 'label': label, 'source_path': label, 'context': 'synthetic'}


class MatchingTests(unittest.TestCase):
    def test_exact_gain_trim_and_repeated_loop(self):
        a = signal()
        for variant, expected in ((a.copy(), 'EXACT'), (a*.4, 'NEAR_EXACT'), (-a, 'NEAR_EXACT')):
            rows, _ = compare(a, variant, fragments=False)
            self.assertEqual(rows[0]['classification'], expected)
            self.assertTrue(rows[0]['auto_performance_link'])
        trim = a[3200:11200]
        rows, _ = compare(a, trim, fragments=False)
        self.assertTrue(any(r['classification'] == 'EXACT' for r in rows))
        self.assertAlmostEqual(rows[0]['query_start_s'], .2, places=4)
        loop = np.concatenate([a, np.zeros((4000, 1))]*4)
        rows, _ = compare(a, loop, fragments=False)
        exact = [r for r in rows if r['classification'] == 'EXACT']
        self.assertEqual(len(exact), 4)
        self.assertEqual([round(r['target_start_s'], 2) for r in exact], [0, 1.75, 3.5, 5.25])
        candidates = periodicity(loop)['candidates']
        self.assertTrue(any(abs(r['period_s']-1.75) < .03 for r in candidates))

    def test_two_distinct_takes_and_silence_do_not_merge(self):
        a, b = signal(1), signal(2)
        rows, stats = compare(a, b)
        self.assertFalse(any(r['auto_performance_link'] for r in rows))
        self.assertEqual(stats['nonmatch_status'], 'UNRESOLVED')
        self.assertEqual(compare(np.zeros((16000,1)), np.zeros((32000,1)))[0], [])
        tone = np.sin(2*np.pi*250*np.arange(RATE)/RATE)[:,None]
        rows, _ = compare(tone, tone, fragments=False)
        self.assertEqual(rows[0]['classification'], 'EXACT')
        self.assertFalse(rows[0]['auto_performance_link'])

    def test_partial_composite_and_unmatched_remainder(self):
        a, b = signal(1), signal(7)
        edit = np.concatenate([a[3200:11200], np.zeros((1600,1)), b[4800:12800], np.zeros((1600,1))])
        ma, _ = compare(a, edit)
        mb, _ = compare(b, edit)
        self.assertTrue(any(r['classification'] == 'EXACT' for r in ma))
        self.assertTrue(any(r['classification'] == 'EXACT' for r in mb))
        self.assertFalse(any(r['query_start_s'] == 0 and r['query_end_s'] >= 1.49 for r in ma))

    def test_full_copy_does_not_hide_additional_partial_occurrence(self):
        a=signal(3)
        b=np.concatenate([a,np.zeros((4000,1)),a[4000:14000]])
        rows,_=compare(a,b)
        self.assertTrue(any(r['classification']=='EXACT' and r['target_start_s']<.01 for r in rows))
        self.assertTrue(any(r['classification']=='EXACT' and r['target_start_s']>=1.75 for r in rows))
        rows,_=compare(a,a[3200:11200]*.4,fragments=False)
        self.assertAlmostEqual(rows[0]['metrics']['fitted_gain_b_over_a'],.4,places=6)

    def test_added_background_and_channel_swap(self):
        a = signal(1)
        music = signal(8)*.08
        rows, _ = compare(a, a+music, fragments=False)
        self.assertTrue(any(r['classification'] in {'NEAR_EXACT', 'PROBABLE_DERIVATIVE'} for r in rows))
        stereo = np.column_stack([a[:,0], signal(2)[:,0]])
        rows, _ = compare(stereo, stereo[:, ::-1], fragments=False)
        self.assertTrue(any(r['channel_a'] != r['channel_b'] for r in rows))
        self.assertFalse(any(r['exact_native_pcm'] for r in rows))

    def test_shared_music_cannot_auto_establish_vocal_identity(self):
        music = signal(9)*5
        a, b = music+signal(1)*.05, music+signal(2)*.05
        rows, _ = compare(a, b, fragments=False)
        self.assertTrue(rows)  # This is deliberately a hard acoustic confound.
        oa, ob = record('a', a, 'mixed_scene'), record('b', b, 'mixed_scene')
        matches = [match_record(oa, ob, r) for r in rows]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'g.sqlite'
            ingest(db, [oa,ob], matches, 'music-confound')
            g = snapshot(db)
            self.assertEqual(len(g['performances']), 2)
            self.assertTrue(all(e['identity_decision'] != 'same_performance' for e in g['matches']))

    def test_native_sample_rate_difference_never_exact(self):
        a = signal()
        from scipy.signal import resample_poly
        b = resample_poly(a, 3, 2, axis=0)
        rows, _ = compare(a, b, RATE, 24000, fragments=False)
        self.assertTrue(rows)
        self.assertFalse(any(r['exact_native_pcm'] for r in rows))
        self.assertTrue(all(r['verification_representation']=='per_channel_16khz_search_proxy' for r in rows))

    def test_transformed_reuse_pitch_time_and_speed(self):
        a = signal(3, 2.4)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'source.wav'
            wavwrite(source, RATE, a)
            filters = {'stretch_up': 'atempo=1.05', 'stretch_down': 'atempo=0.95',
                       'pitch_up': f'asetrate={RATE*2**(1/12)},aresample={RATE},atempo={2**(-1/12)}',
                       'pitch_down': f'asetrate={RATE*2**(-1/12)},aresample={RATE},atempo={2**(1/12)}',
                       'speed': f'asetrate={RATE*1.05},aresample={RATE}'}
            for name, filterspec in filters.items():
                with self.subTest(name=name):
                    out = root/(name+'.wav')
                    run(['ffmpeg','-v','error','-i',source,'-af',filterspec,'-c:a','pcm_f64le',out])
                    rate, b = wavread(out)
                    rows, _ = compare(a, b[:,None] if b.ndim == 1 else b, RATE, rate, fragments=False, transforms=True)
                    self.assertTrue(any(r['classification'] == 'PROBABLE_DERIVATIVE' for r in rows), [(r['classification'],r['metrics']) for r in rows])

    def test_eq_reverb_and_transformed_partial_reuse(self):
        a=signal(3,2.4)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            wavwrite(root/'a.wav',RATE,a)
            for name,fx in [('eq','equalizer=f=800:t=q:w=1:g=6'),('reverb','aecho=0.8:0.8:25:0.2')]:
                run(['ffmpeg','-v','error','-i',root/'a.wav','-af',fx,'-c:a','pcm_f64le',root/(name+'.wav')])
                _,b=wavread(root/(name+'.wav'))
                rows,_=compare(a,b[:,None],fragments=False,transforms=True)
                self.assertTrue(any(r['classification'] in {'NEAR_EXACT','PROBABLE_DERIVATIVE'} for r in rows),name)
            fragment=a[6400:22400]
            wavwrite(root/'fragment.wav',RATE,fragment)
            run(['ffmpeg','-v','error','-i',root/'fragment.wav','-af',f'asetrate={RATE*2**(1/12)},aresample={RATE},atempo={2**(-1/12)}','-c:a','pcm_f64le',root/'shift.wav'])
            _,shift=wavread(root/'shift.wav')
            b=np.concatenate([np.zeros((8000,1)),shift[:,None],np.zeros((RATE,1))])
            rows,_=compare(a,b,transforms=True)
            self.assertTrue(any(r['classification']=='PROBABLE_DERIVATIVE' and abs(r['pitch_shift_semitones_b_minus_a']-1)<.1 for r in rows))


class LedgerTests(unittest.TestCase):
    def test_unknown_stereo_window_cannot_bridge_simultaneous_voices(self):
        x,y=signal(1),signal(2)
        mixed=np.column_stack([x[:,0],y[:,0]])
        a,b=record('voice-a',x),record('voice-b',y)
        a['speaker']='A';b['speaker']='B'
        m=record('mixed-window',mixed,'mixed_scene',kind='window',vocal='unknown');m['speaker']=None
        edges=[]
        for o,data in ((a,x),(b,y)):
            rows,_=compare(data,mixed,fragments=False)
            edges.extend(match_record(o,m,r) for r in rows)
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'g.sqlite'
            ingest(db,[a,b,m],edges,'simultaneous-voices')
            g=snapshot(db)
            self.assertEqual(len(g['performances']),2)
            self.assertEqual(len(g['media_edits']),1)
            self.assertFalse(any(e['whole_unit_equivalence'] for e in g['matches']))
            self.assertEqual(audit(g)['independent_resolved_unit_count'],2)

    def test_late_clean_witness_preserves_id_and_identical_materializations_count_once(self):
        x=signal(4)
        a=record('occ-first',x,'mixed_scene')
        b=record('occ-second',x,'isolated_voice_asset')
        c=record('occ-third',x,'isolated_voice_asset')
        # Same media bytes at three paths are three occurrences of one recording.
        b['source_sha256']=c['source_sha256']=a['source_sha256']
        rows,_=compare(x,x,fragments=False)
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'g.sqlite'
            ingest(db,[a],[],'first')
            pid=snapshot(db)['performances'][0]['performance_id']
            ingest(db,[b,c],[match_record(a,b,rows[0]),match_record(a,c,rows[0])],'later')
            g=snapshot(db)
            self.assertEqual(len(g['performances']),1)
            self.assertEqual(g['performances'][0]['performance_id'],pid)
            self.assertIn(g['performances'][0]['canonical_occurrence_id'],['occ-second','occ-third'])
            self.assertEqual(audit(g)['independent_resolved_unit_count'],1)

    def test_old_decision_survives_new_measurement_and_annotation_is_versioned(self):
        x=signal()
        a,b=record('occ-a',x),record('occ-b',x)
        rows,_=compare(x,x,fragments=False)
        edge=match_record(a,b,rows[0])
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'g.sqlite'
            ingest(db,[a,b],[edge],'first')
            rev=decide(db,edge['match_id'],'distinct','Correction fixture','Tester')['revision']
            changed=dict(edge,match_id='another-observation',method='Another measurement of the same correspondence')
            ingest(db,[a,b],[changed],'second')
            g=snapshot(db)
            self.assertEqual(len(g['matches']),1)
            self.assertEqual(g['matches'][0]['identity_decision'],'distinct')
            self.assertEqual(len(g['matches'][0]['observation_history']),2)
            annotate(db,'occ-a',{'speaker':'Corrected label'},'Attribution correction','Tester')
            self.assertEqual(next(o for o in snapshot(db)['occurrences'] if o['occurrence_id']=='occ-a')['speaker'],'Corrected label')
            self.assertEqual(next(o for o in snapshot(db,rev)['occurrences'] if o['occurrence_id']=='occ-a')['speaker'],'Synthetic actor label')
            with self.assertRaises(AVError): annotate(db,'occ-a',{'sample_start':8},'Cannot edit evidence','Tester')

    def test_composite_does_not_bridge_identities_and_baseline_dedup(self):
        a, b = signal(1), signal(2)
        edit = np.concatenate([a,a,b])
        oa, ob, oe = record('occ-a',a), record('occ-b',b), record('occ-edit',edit,kind='composite')
        matches = []
        for o, x in ((oa,a),(ob,b)):
            rows, _ = compare(x, edit, fragments=False)
            matches += [match_record(o, oe, r) for r in rows]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'g.sqlite'
            ingest(db,[oa,ob,oe],matches,'composite')
            g = snapshot(db)
            self.assertEqual(len(g['performances']),2)
            self.assertEqual(len(g['media_edits']),1)
            self.assertIsNone(g['media_edits'][0]['performance_id'])
            self.assertEqual(len(g['fragment_occurrences']),3)
            self.assertEqual(len(g['media_edits'][0]['components']),3)
            self.assertFalse(any(e['whole_unit_equivalence'] for e in g['matches']))
            result = audit(g)
            self.assertEqual(result['independent_resolved_unit_count'],2)
            self.assertAlmostEqual(result['unique_declared_vocal_seconds'],3.)
            edited = next(p for p in result['performances'] if 'occ-edit' in p['occurrence_ids'])
            self.assertEqual(edited['independent_unit_contribution'],0)
            self.assertEqual(edited['relationship'],'composite')

    def test_reversible_decision_and_stable_promotion_and_revision(self):
        x = signal()
        a, b = record('occ-a',x, 'mixed_scene',vocal='candidate'), record('occ-b',x, 'clean_derived',vocal='candidate')
        rows,_ = compare(x,x,fragments=False)
        edge = match_record(a,b,rows[0])
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'g.sqlite'
            first = ingest(db,[a,b],[edge],'first')
            self.assertEqual(len(snapshot(db)['performances']),2)
            decide(db,edge['match_id'],'same_performance','Known fixture origin','Test fixture')
            merged = snapshot(db)
            self.assertEqual(len(merged['performances']),1)
            pid = merged['performances'][0]['performance_id']
            promote(db,pid,'occ-a','Exercise explicit witness selection','Test fixture')
            self.assertEqual(snapshot(db)['performances'][0]['performance_id'],pid)
            self.assertEqual(len(snapshot(db,first)['performances']),2)
            decide(db,edge['match_id'],'distinct','Exercise correction','Test fixture')
            self.assertEqual(len(snapshot(db)['performances']),2)
            decide(db,edge['match_id'],'reset','Restore automatic policy','Test fixture')
            self.assertEqual(len(snapshot(db)['performances']),2)

    def test_gain_and_pitch_eligibility_are_distinct(self):
        x = signal()
        a,b = record('occ-a',x),record('occ-b',x*.5)
        rows,_ = compare(x,x*.5,fragments=False)
        with tempfile.TemporaryDirectory() as d:
            db = Path(d)/'g.sqlite'
            ingest(db,[a,b],[match_record(a,b,rows[0])],'gain')
            g = snapshot(db)
            pid = g['performances'][0]['performance_id']
            promote(db,pid,'occ-b','Gain fixture','Test fixture')
            result = audit(snapshot(db),[{'occurrence_id':'occ-a'},{'occurrence_id':'occ-b'}])
            self.assertEqual(result['independent_resolved_unit_count'],1)
            p = result['performances'][0]
            self.assertTrue(p['baseline_eligibility']['pitch']['eligible'])
            self.assertTrue(p['baseline_eligibility']['level']['eligible'])
            self.assertEqual(p['baseline_eligibility']['level']['witness_occurrence_id'],'occ-a')
            self.assertEqual(result['existing_baseline_rows'][1]['status'],'DUPLICATE_WHOLE_UNIT')
            promote(db,pid,'occ-b','Explicit changed-level witness','Test fixture',feature='level')
            self.assertFalse(audit(snapshot(db))['performances'][0]['baseline_eligibility']['level']['eligible'])

    def test_partial_overlap_chain_never_merges_whole_units(self):
        x,y = signal(1),signal(2)
        mid = np.concatenate([x[4000:12000],y[4000:12000]])
        a,b,c = record('a',x),record('b',mid),record('c',y)
        matches=[]
        for source, data in ((a,x),(c,y)):
            rows,_=compare(data,mid)
            matches += [match_record(source,b,r) for r in rows]
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'g.sqlite'
            ingest(db,[a,b,c],matches,'chain')
            self.assertEqual(len(snapshot(db)['performances']),3)


class WorkflowTests(unittest.TestCase):
    def test_parquet_and_cli_operations(self):
        import contextlib, io
        from avevidence.cli import main
        from avevidence.reuse.workflow import read_baseline
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest('Optional reuse-parquet extra not installed')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=signal(6)
            wavwrite(root/'a.wav',RATE,a)
            wavwrite(root/'b.wav',RATE,a*.7)
            cfg={'schema':'ave.reuse.config.v1','occurrences':[{'id':n,'source':n,'speaker':'Fixture','quality':'isolated_voice_asset',
                'unit_kind':'performance','vocal_status':'verified_voice','vocal_basis':'Synthetic truth'} for n in ['a.wav','b.wav']]}
            (root/'config.json').write_text(json.dumps(cfg))
            db=root/'ledger.sqlite'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['reuse','compare',str(root/'config.json'),'a.wav','b.wav',str(root/'comp'),
                    '--database',str(db),'--cache-dir',str(root/'cache'),'--preview-count','0']),0)
                graph=snapshot(db)
                pid=graph['performances'][0]['performance_id']
                oid=graph['occurrences'][0]['occurrence_id']
                self.assertEqual(main(['reuse','promote',str(db),pid,oid,'--reason','Test','--reviewer','Fixture']),0)
                self.assertEqual(main(['reuse','decide',str(db),graph['matches'][0]['match_id'],'--decision','unresolved','--reason','Test','--reviewer','Fixture']),0)
                (root/'changes.json').write_text(json.dumps({'context':'Test correction'}))
                self.assertEqual(main(['reuse','annotate',str(db),oid,str(root/'changes.json'),'--reason','Test','--reviewer','Fixture']),0)
                self.assertEqual(main(['reuse','graph',str(db),str(root/'graph'),'--revision','1']),0)
                pq.write_table(pa.Table.from_pylist([{'occurrence_id':o['occurrence_id']} for o in graph['occurrences']]),root/'baseline.parquet')
                self.assertEqual(len(read_baseline(root/'baseline.parquet')),2)
                self.assertEqual(main(['reuse','audit',str(db),str(root/'audit'),'--baseline',str(root/'baseline.parquet')]),0)
            verify_run(root/'graph'); verify_run(root/'audit')

    def test_incremental_target_retrieval_rebuild_and_search_attachment(self):
        from avevidence.reuse.lookup import attach
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=signal(5)
            wavwrite(root/'ref.wav',RATE,a)
            wavwrite(root/'target.wav',RATE,np.concatenate([np.zeros((8000,1)),a,np.zeros((8000,1))]))
            def config(name,source,role,kind):
                p=root/name
                p.write_text(json.dumps({'schema':'ave.reuse.config.v1','occurrences':[{'id':role,'source':source,'role':role,'unit_kind':kind,
                    'speaker':'Fixture','vocal_status':'verified_voice','vocal_basis':'Constructed fixture','quality':'isolated_voice_asset'}]}))
                return p
            first=config('first.json','ref.wav','reference','performance')
            second=config('second.json','target.wav','target','composite')
            db=root/'ledger.sqlite'
            run_scan(first,root/'first',cache_dir=root/'cache-1',database=db,preview_count=0)
            run_scan(second,root/'second',cache_dir=root/'cache-2',database=db,preview_count=0)
            g=snapshot(db)
            self.assertTrue(g['matches'])
            self.assertTrue(any(e['identity_decision']=='same_performance' for e in g['matches']))
            o=g['occurrences'][0]
            found=attach([{'source_sha256':o['source_sha256'],'audio_stream':o['audio_stream'],
                           'start_s':o['source_start_s'],'end_s':o['source_end_s']}],db)
            self.assertEqual(found[0]['reuse']['status'],'LINKED')

    def test_gap_cannot_be_bridged_and_unsafe_interval_rejected(self):
        from avevidence.reuse.media import extract
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            data=signal(2,2.)
            raw=root/'native.f64le'
            data.astype('<f8').tofile(raw)
            decoded={'raw_path':raw,'sample_rate_hz':RATE,'sample_frames':len(data),'channels':1,
                'segments':[{'sample_start':0,'sample_end':RATE,'source_start_seconds':0.,'source_end_seconds':1.},
                            {'sample_start':RATE,'sample_end':2*RATE,'source_start_seconds':2.,'source_end_seconds':3.}]}
            with self.assertRaises(AVError): extract(decoded,.5,2.5)
            selected,part=extract(decoded,2.,2.5)
            self.assertEqual(part['input_sample_start'],RATE)
            self.assertEqual(len(selected),8000)

    def test_complete_scan_cache_aac_export_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=signal(4)
            wavwrite(root/'a.wav',RATE,a)
            wavwrite(root/'copy.wav',RATE,a)
            run(['ffmpeg','-v','error','-i',root/'a.wav','-c:a','aac','-b:a','128k',root/'copy.m4a'])
            config={'schema':'ave.reuse.config.v1','occurrences':[
                {'id':name,'source':name,'start_s':0,'end_s':1.5,'unit_kind':'performance',
                 'speaker':'Fixture','quality':'isolated_voice_asset','vocal_status':'verified_voice','vocal_basis':'Synthetic fixture'}
                for name in ('a.wav','copy.wav','copy.m4a')]}
            (root/'input.json').write_text(json.dumps(config),encoding='utf-8')
            args=dict(cache_dir=root/'cache',database=root/'ledger.sqlite',preview_count=1)
            run_scan(root/'input.json',root/'cold',**args)
            verify_run(root/'cold',verify_sources=True)
            result=read_json(root/'cold'/'reuse.json')
            self.assertTrue(any(m['classification']=='EXACT' for m in result['matches']))
            self.assertTrue(any(m['classification']=='NEAR_EXACT' for m in result['matches']))
            self.assertEqual(read_json(root/'cold'/'audit.json')['independent_resolved_unit_count'],1)
            run_scan(root/'input.json',root/'warm',**args)
            self.assertEqual(read_json(root/'warm'/'reuse.json')['search']['fingerprint_cache_hits'],3)
            self.assertEqual(read_json(root/'warm'/'reuse.json')['search']['comparison_cache_hits'],3)
            self.assertEqual(len(snapshot(root/'ledger.sqlite')['occurrences']),3)
            (root/'baseline.json').write_text(json.dumps([{'occurrence_id':o['occurrence_id']} for o in result['occurrences']]))
            export(root/'ledger.sqlite',root/'audit',baseline=root/'baseline.json',kind='audit')
            verify_run(root/'audit')
            self.assertTrue((root/'cold'/'audio'/'match-01-a.wav').is_file())
            cache=next((root/'cache'/'reuse-features').glob('*/features.npz'))
            cache.write_bytes(b'corrupt')
            with self.assertRaises(AVError): run_scan(root/'input.json',root/'bad',**args)

    def test_index_retrieval_contains_trimmed_and_gain_copies(self):
        a=signal(2)
        with tempfile.TemporaryDirectory() as d:
            index=Index(Path(d)/'search.sqlite')
            try:
                index.add('copy',descriptors(a*.3),'1')
                index.add('different',descriptors(signal(8)),'2')
                found,_=index.candidates(descriptors(a[3200:12800]),{'copy','different'})
                self.assertIn('copy',found)
            finally:index.close()


if __name__=='__main__': unittest.main()
