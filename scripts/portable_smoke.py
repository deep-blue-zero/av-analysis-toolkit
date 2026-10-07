#!/usr/bin/env python3
"""Exercise the installed wheel using generated fixtures; no real-media perception."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import wave


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='NEW generated-fixture directory')
    parser.add_argument('--runtime', type=Path, help='Private cloud runtime instead of the current environment')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    if args.runtime:
        sys.path.insert(0, str(args.runtime.resolve()))
    import numpy as np
    commands = []

    def ave(*arguments):
        launch = ([str(Path(__file__).resolve().parent / 'cloud_run.py'), '--runtime', str(args.runtime.resolve())]
                  if args.runtime else ['-m', 'avevidence'])
        command = [sys.executable, '-I', '-X', 'utf8', *launch, *map(str, arguments)]
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                encoding='utf-8', errors='replace')
        index = len(commands) + 1
        (root / f'command-{index:02d}.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
        commands.append({'command': list(map(str, arguments)), 'returncode': result.returncode})
        if result.returncode:
            raise RuntimeError(f'Command {index} failed; inspect command-{index:02d}.txt')
        return json.loads(result.stdout) if result.stdout.strip().startswith('{') else result.stdout

    rate = 16000
    t = np.arange(rate * 2) / rate
    f0 = 220 + 35 * np.sin(t * 2 * np.pi * .6)
    phase = np.cumsum(f0) * 2 * np.pi / rate
    envelope = .16 * (.2 + .8 * np.sin(np.pi * t * 2.3) ** 4)
    test_signal = envelope * (np.sin(phase) + .2 * np.sin(2 * phase))
    samples = (np.r_[test_signal, np.zeros(rate // 2), test_signal[:rate * 3 // 2]] * 32767).astype('<i2')

    def wav(path, data):
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(data.tobytes())

    source = root / 'synthetic.wav'
    wav(source, samples)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    transcript = root / 'transcript.json'
    dump(transcript, {'source_sha256': source_hash, 'clock': 'original_pts_minus_source_origin',
                     'text_authority': 'synthetic_test_text_not_audible_speech',
                     'utterances': [{'utterance_id': 'cue-1', 'start_s': .2, 'end_s': 1.8,
                                     'text': 'これは合成テストです。', 'speaker': 'Synthetic fixture'}]})
    ave('doctor')
    cold, warm = root / 'performance-cold', root / 'performance-warm'
    for output in (cold, warm):
        ave('performance', source, output, '--cache-dir', root / 'cache', '--transcript', transcript)
        ave('verify', output, '--verify-sources')
    report = json.loads((cold / 'performance.json').read_text(encoding='utf-8'))
    cached = json.loads((warm / 'performance.json').read_text(encoding='utf-8'))
    assert report['alignment_counts'] == {'NOT_REQUESTED': 1}
    assert report['perceptual_review'] == 'NOT_PERFORMED'
    assert cached['cache']['audio_hit'] and cached['cache']['contour_hit']
    assert math.isfinite(report['cues'][0]['measurements']['f0_median_hz'])
    ave('performance-index', cold, root / 'evidence.sqlite')
    search = ave('performance-search', root / 'evidence.sqlite', '合成')
    assert search['count'] == 1 and search['matches'][0]['run_manifest_status'] == 'MATCH'
    ave('audio-track', source, root / 'audio-track', '--profile', 'speech')
    ave('verify', root / 'audio-track', '--verify-sources')
    config = root / 'review-config.json'
    dump(config, {'schema': 'ave.performance-review.config.v1', 'title': 'Synthetic setup review',
                  'reviews': [{'review_id': 'SETUP-01', 'source_id': 'SYNTHETIC', 'source_sha256': source_hash,
                               'run': str(cold), 'start_s': .25, 'end_s': 1.75,
                               'questions': ['Synthetic fixture only: no human or AI listening is asserted.']}]})
    ave('performance-review', config, root / 'review')
    ave('verify', root / 'review', '--verify-sources')
    ave('performance-review-import', root / 'review', root / 'review/notes-template.json', root / 'imported-notes')
    # Known digital copies, not natural same-line acting or a voice-identification test.
    reference = root / 'reference.wav'
    copy = root / 'copy.wav'
    wav(reference, samples[:rate * 3 // 2])
    copy.write_bytes(reference.read_bytes())
    reuse_config = root / 'reuse-config.json'
    dump(reuse_config, {'schema': 'ave.reuse.config.v1', 'occurrences': [
        {'id': label, 'source': str(path), 'start_s': 0., 'end_s': 1.5,
         'quality': 'isolated_voice_asset', 'unit_kind': 'performance',
         'vocal_status': 'verified_voice', 'vocal_basis': 'Synthetic ground-truth test only',
         'speaker': 'Synthetic fixture'} for label, path in [('reference', reference), ('copy', copy)]]})
    ave('reuse', 'scan', reuse_config, root / 'reuse', '--cache-dir', root / 'reuse-cache',
        '--database', root / 'reuse.sqlite', '--preview-count', '1')
    ave('verify', root / 'reuse', '--verify-sources')
    reuse = json.loads((root / 'reuse/reuse.json').read_text(encoding='utf-8'))
    assert any(m['classification'] == 'EXACT' for m in reuse['matches'])
    ave('reuse', 'audit', root / 'reuse.sqlite', root / 'reuse-audit')
    ave('pack', cold, root / 'evidence.zip')
    ave('verify-archive', root / 'evidence.zip')
    video = root / 'synthetic.mp4'
    video_command = ['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
                     'testsrc2=size=160x90:rate=10:duration=4', '-i', str(source),
                     '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-shortest', str(video)]
    subprocess.run(video_command, capture_output=True, check=True)
    ave('frame-index', video, root / 'frame-index', '--video-stream', '0')
    ave('frames', video, root / 'frames', '--mode', 'dense', '--fps', '5', '--start', '0.5',
        '--end', '2.0', '--video-stream', '0', '--frame-index-run', root / 'frame-index')
    ave('contacts', root / 'frames', root / 'contacts')
    ave('verify', root / 'frames', '--verify-sources')
    if args.runtime:
        imported = json.loads(subprocess.run([sys.executable, '-I', '-X', 'utf8',
                    str(Path(__file__).resolve().parent/'cloud_run.py'), '--runtime', str(args.runtime.resolve()),
                    '--where'], capture_output=True, text=True, encoding='utf-8', check=True).stdout)['module']
    else:
        imported = subprocess.run([sys.executable, '-I', '-c',
                                   'import avevidence; print(avevidence.__file__)'], capture_output=True,
                                  text=True, check=True).stdout.strip()
    result = {'schema': 'ave.portable.smoke.v1', 'passed': True,
              'installed_module': imported, 'python': sys.version,
              'packages': {n: importlib.metadata.version(n) for n in
                           ['av-evidence-toolkit', 'Pillow', 'numpy', 'scipy', 'praat-parselmouth']},
              'commands': commands, 'command_count': len(commands),
              'cache_reuse': True, 'japanese_search': True, 'exact_copy_reuse': True,
              'alignment_model_used': False, 'perceptual_review': 'NOT_PERFORMED',
              'elapsed_s': time.perf_counter() - started,
              'scope': 'Generated fixtures, installed wheel, cached contours/search/review/reuse/frames/archive; not real-media perception'}
    dump(root / 'SMOKE_RESULT.json', result)
    print(json.dumps({k: result[k] for k in ['passed', 'command_count', 'installed_module', 'elapsed_s', 'scope']}, indent=2))


if __name__ == '__main__':
    main()
