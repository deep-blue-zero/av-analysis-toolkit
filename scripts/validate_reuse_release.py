"""Verify real MAD sentinels, packaged report links and additional DSP cases."""
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from avevidence.common import read_json,sha256,write_json,run,environment
from avevidence.inventory import verify_run


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.audio_count=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='audio': self.audio_count+=1
        if tag=='a' and 'href' in attrs:self.links.append(attrs['href'])
        if tag=='audio' and 'src' in attrs:self.links.append(attrs['src'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mad-run',required=True)
    parser.add_argument('--warm-run',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    root=Path(args.output).resolve()
    root.mkdir(parents=True,exist_ok=False)
    cold,warm=Path(args.mad_run).resolve(),Path(args.warm_run).resolve()
    verified={str(p):verify_run(p,verify_sources=True) for p in (cold,warm)}
    c,w=read_json(cold/'reuse.json'),read_json(warm/'reuse.json')
    graph,policy=read_json(cold/'graph.json'),read_json(cold/'audit.json')
    old=read_json(cold.parent/'edit-measurements.json')
    sentinels=[]
    for template in old['sample_matching']:
        for previous in template['matches']:
            if 11<=previous['source_start_s']<23 and previous['normalized_correlation_ch0']>=.98:
                found=[m['match_id'] for m in c['matches'] if
                    abs(m['a']['source_start_s']-template['template_start_s'])<.002 and
                    abs(m['b']['source_start_s']-previous['source_start_s'])<.001]
                sentinels.append({'template':template['template'],'expected_source_start_s':previous['source_start_s'],'found_match_ids':found})
    assert len(sentinels)==24 and all(s['found_match_ids'] for s in sentinels)
    assert c['matches']==w['matches']
    assert w['search']['comparison_cache_hits']==len(w['search']['comparisons'])==2
    assert w['search']['fingerprint_cache_hits']==3
    assert not c['search']['retrieval_truncated_occurrences']
    assert all(not p for p in policy['baseline_contributions_by_feature'].values())
    html=Links(); html.feed((cold/'reuse.html').read_text(encoding='utf-8'))
    assert html.audio_count==12
    for link in html.links:
        target=(cold/link).resolve()
        assert target.is_relative_to(cold) and target.is_file(),link
    # Additional cases supplement the unit suite and preserve their fixtures.
    spec=importlib.util.spec_from_file_location('reuse_fixtures',ROOT/'tests/test_reuse.py')
    fixtures=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    import numpy as np
    from scipy.io.wavfile import write as wavwrite,read as wavread
    from avevidence.reuse.correlate import compare
    from avevidence.reuse.graph import ingest,snapshot
    from avevidence.reuse.workflow import match_record
    from avevidence.reuse.policy import audit
    a=fixtures.signal(3,2.4)
    wavwrite(root/'synthetic-source.wav',16000,a)
    run(['ffmpeg','-v','error','-i',root/'synthetic-source.wav','-af',
         'acompressor=threshold=0.05:ratio=3:attack=5:release=30','-c:a','pcm_f64le',root/'compressed.wav'])
    _,b=wavread(root/'compressed.wav')
    compressed,cs=compare(a,b[:,None],fragments=False,transforms=True)
    assert any(m['classification'] in {'NEAR_EXACT','PROBABLE_DERIVATIVE'} for m in compressed)
    edited=np.concatenate([a[:16000],np.zeros((4000,1)),a[16000:]])
    wavwrite(root/'silence-inserted.wav',16000,edited)
    silence,ss=compare(a,edited)
    assert any(m['classification']=='EXACT' and m['target_start_s']<1 for m in silence)
    assert any(m['classification']=='EXACT' and m['target_start_s']>1.25 for m in silence)
    oa,ob=fixtures.record('original',a),fixtures.record('edited',edited,kind='composite')
    ingest(root/'synthetic-ledger.sqlite',[oa,ob],[match_record(oa,ob,m) for m in silence],'silence-insertion')
    synthetic_audit=audit(snapshot(root/'synthetic-ledger.sqlite'))
    assert synthetic_audit['independent_resolved_unit_count']==1
    unit=fixtures.signal(2,.5)
    loop=np.concatenate([unit,unit,unit,unit])
    internal,ins=compare(loop,loop,self_compare=True)
    assert any(m['classification']=='EXACT' for m in internal)
    results={'schema':'ave.reuse.release-validation.v1','passed':True,'environment':environment(),
        'script_sha256':sha256(__file__),'fixture_generator_sha256':sha256(ROOT/'tests/test_reuse.py'),
        'verified_runs':verified,'mad_source_sha256':c['occurrences'][0]['source_sha256'],
        'prior_measurements_sha256':sha256(cold.parent/'edit-measurements.json'),
        'mad_classes':dict(Counter(m['classification'] for m in c['matches'])),
        'mad_decisions':dict(Counter(m['identity_decision'] for m in graph['matches'])),
        'mad_fragment_appearances':len(graph['fragment_occurrences']),'early_sentinels':sentinels,
        'cold_analysis_seconds':c['search']['elapsed_s'],'warm_analysis_seconds':w['search']['elapsed_s'],
        'warm_matches_identical':c['matches']==w['matches'],'warm_pair_cache_hits':w['search']['comparison_cache_hits'],
        'all_html_local_links_exist':True,'audition_players':html.audio_count,
        'compression':{'matches':compressed,'search':cs},'silence_insertion':{'matches':silence,'search':ss,'audit':synthetic_audit},
        'internal_loop':{'matches':internal,'search':ins},
        'limits':['MAD correspondence is computed, not heard. Vocal attribution remains candidate.',
                  'Synthetic independent signals do not establish error rates on naturally repeated Japanese lines.',
                  'The HTML links/structure were checked; this script does not claim a visual or auditory browser review.']}
    write_json(root/'validation.json',results)
    print(json.dumps({k:results[k] for k in ('passed','mad_classes','mad_fragment_appearances','cold_analysis_seconds','warm_analysis_seconds','warm_pair_cache_hits')},indent=2))


if __name__=='__main__':main()
