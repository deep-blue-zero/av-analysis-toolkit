#!/usr/bin/env python3
"""Create synthetic audio and exercise the new instruments; never a listening test."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True

from avevidence.audio_tracks import audio_track
from avevidence.common import AVError, environment, read_csv, read_json, run, sha256, write_json
from avevidence.inventory import verify_run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',help='A NEW output directory; generated fixture is retained')
    args=parser.parse_args();out=Path(args.output).resolve()
    if out.exists():parser.error('Output already exists; use a new directory')
    out.mkdir(parents=True)
    try:
        import numpy as np
        rate=48000;t=np.arange(rate*24)/rate
        carrier=np.sin(2*np.pi*440*t)
        clicks=.06*np.sin(2*np.pi*3000*t)*np.exp(-np.mod(t,.5)/.008)
        left=.06*carrier;right=left.copy()
        second=(t>=6)&(t<12)
        left[second]=.18*carrier[second]+clicks[second]
        right[second]=.1*np.sin(2*np.pi*660*t[second])+clicks[second]
        third=(t>=12)&(t<18)
        left[third]=.12*carrier[third]+clicks[third];right[third]=-left[third]
        end=t>=18;fade=(24-t[end])/6
        left[end]=fade*(.12*carrier[end]+clicks[end]);right[end]=left[end]
        samples=np.column_stack((left,right))
        with tempfile.TemporaryDirectory(prefix='ave-smoke-input-') as tmp:
            raw=Path(tmp)/'generated.wav'
            with wave.open(str(raw),'wb') as f:
                f.setnchannels(2);f.setsampwidth(4);f.setframerate(rate)
                f.writeframes(np.rint(samples*(2**31-1)).astype('<i4').tobytes())
            source=out/'synthetic-stereo-24s.wav'
            # Explicitly write the stereo channel layout, not a guessed pair.
            run(['ffmpeg','-v','error','-nostdin','-n','-i',raw,'-channel_layout','stereo','-c:a','pcm_s32le',source])
        source_hash=sha256(source)
        audio_track(source,out/'analysis',profile='music',loudness='require')
        verify_run(out/'analysis',verify_sources=True)
        report=read_json(out/'analysis/audio_tracks.json')
        stereo=read_csv(out/'analysis/stereo.csv')
        phase=[float(r['correlation']) for r in stereo if .5<float(r['source_center_seconds'])<5.5]
        anti=[float(r['correlation']) for r in stereo if 12.5<float(r['source_center_seconds'])<17.5]
        checks={'source_unchanged':sha256(source)==source_hash,
                'source_declared_stereo':report['stereo']['basis']=='SOURCE_DECLARED_STEREO',
                'in_phase_control':bool(phase) and min(phase)>.999,
                'antiphase_control':bool(anti) and max(anti)<-.999,
                'three_scales_two_channels':len(report['tracks'])==6,
                'no_synthetic_listening_claim':report['perceptual_review']=='NOT_PERFORMED',
                'renders_exist':bool(report['renders']) and all((out/'analysis'/r['path']).is_file() for r in report['renders'])}
        data={'schema':'ave.audio_smoke.v1','passed':all(checks.values()),'checks':checks,
              'fixture':'Synthetic 24 s / 48 kHz stereo; identical, independent, antiphase and fade sections',
              'fixture_sha256':source_hash,'environment':environment(),
              'scope':'Numerical and packaging smoke only; no real media, voice acting or AV perception tested'}
        write_json(out/'SMOKE_REPORT.json',data)
        print('PASS' if data['passed'] else 'FAIL',out/'SMOKE_REPORT.json')
        return 0 if data['passed'] else 1
    except (AVError,OSError,ValueError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
