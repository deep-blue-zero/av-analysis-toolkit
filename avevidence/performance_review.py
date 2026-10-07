"""Join measured runs to explicit listening questions, never to invented listening."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import html
import os
from pathlib import Path
import shutil
import wave

from .audio import _decode, _raw_input, _scratch, _selection, _slice
from .common import (AVError, file_record, finite, finish_run, output_transaction,
                     read_json, run, select_stream, sha256, write_json)
from .inventory import identifier, verify_run
from .performance_cache import digest


def _resolve(base, value):
    if not isinstance(value, str) or not value.strip():
        raise AVError('Expected a local path')
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _load_config(config):
    config = Path(config).resolve()
    data = read_json(config)
    if data.get('schema') != 'ave.performance-review.config.v1':
        raise AVError('Unsupported performance review configuration')
    rows = data.get('reviews')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise AVError('Require between 1 and 1000 review intervals')
    seen = set()
    for row in rows:
        key = identifier(row.get('review_id'), 'review ID')
        if key in seen:
            raise AVError('Duplicate review ID')
        seen.add(key)
        a, b = finite(row.get('start_s'), 'start', 0), finite(row.get('end_s'), 'end', 0)
        if a >= b or b-a > 600:
            raise AVError('Review intervals must be positive and at most ten minutes')
        pa = finite(row.get('audition_start_s', a), 'audition start', 0)
        pb = finite(row.get('audition_end_s', b), 'audition end', 0)
        if not pa <= a < b <= pb or a-pa > 30 or pb-b > 30:
            raise AVError('Audition context must contain the review interval and be within 30 seconds of its edges')
        if not isinstance(row.get('questions'), list) or not row['questions'] or any(
                not isinstance(q, str) or not q.strip() for q in row['questions']):
            raise AVError('Each interval needs explicit review questions')
    return config, data


def _tracks_for_interval(tracks, start, end):
    import numpy as np
    mask = np.flatnonzero((tracks['time_s'] >= start) & (tracks['time_s'] < end))
    # Retain whole-resolution data in the parent run; this is a 50 ms display only.
    step = max(1, round(.05 / float(np.median(np.diff(tracks['time_s']))))) if len(tracks['time_s']) > 1 else 1
    selected = mask[::step]
    return {k: [float(v) if np.isfinite(v) else None for v in tracks[name][selected]]
            for k, name in [('time', 'time_s'), ('pitch', 'pitch_hz'), ('level', 'rms_dbfs')]}


def _write_audition(decoded, start, end, scratch, target):
    """Keep native channels/rate; declare and verify any 16-bit audition quantization."""
    import numpy as np
    selection = _selection(decoded, start, end)
    if selection['status'] != 'COMPLETE' or len(selection['parts']) != 1:
        raise AVError('Review playback requires one continuously covered audio interval')
    clipped = _slice(decoded, selection, scratch/'selection.f64le')
    run(['ffmpeg', '-v', 'error', '-nostdin', '-n', *_raw_input(clipped),
         '-c:a', 'pcm_s16le', '-fflags', '+bitexact', str(target)])
    with wave.open(str(target), 'rb') as source:
        count, rate, channels = source.getnframes(), source.getframerate(), source.getnchannels()
        if (count, rate, channels, source.getsampwidth()) != (
                clipped['sample_frames'], clipped['sample_rate_hz'], clipped['channels'], 2):
            raise AVError('Audition WAV sample shape differs from selected audio')
        pcm = np.frombuffer(source.readframes(count), dtype='<i2').reshape(-1, channels)
    raw = np.memmap(clipped['raw_path'], dtype='<f8', mode='r', shape=(count, channels))
    maximum_error, exact = 0., True
    try:
        for first in range(0, count, 262144):
            original = np.asarray(raw[first:first+262144])
            difference = np.abs(original - pcm[first:first+262144].astype(np.float64)/32768.)
            maximum_error = max(maximum_error, float(difference.max()))
            exact = exact and bool((difference == 0).all())
    finally:
        del original, difference, raw
        Path(clipped['raw_path']).unlink()
    return {'path': target.name, 'sha256': sha256(target), 'sample_rate_hz': rate,
        'channels': channels, 'sample_frames': count, 'sample_width_bits': 16,
        'selection': selection, 'source_start_s': selection['parts'][0]['source_start_seconds'],
        'source_end_s': selection['parts'][0]['source_end_seconds'],
        'native_samples_equal': exact, 'max_absolute_sample_error': maximum_error,
        'transform': 'Native rate and channel order; no normalization/downmix. PCM16 audition copy; equality is tested, otherwise quantization is reported.'}


def build_review(config, output):
    import numpy as np
    config, data = _load_config(config)
    groups = defaultdict(list)
    sources = [file_record(config, 'review_config')]
    for row in data['reviews']:
        groups[_resolve(config.parent, row.get('run'))].append(row)
    reports = {}
    for folder, rows in groups.items():
        verify_run(folder, verify_sources=True)
        manifest = read_json(folder/'run.json')
        if manifest['operation'] != 'performance':
            raise AVError('Review inputs must be performance runs')
        report = read_json(folder/'performance.json')
        if report.get('clock') != 'original_pts_minus_source_origin':
            raise AVError('Unsupported review source clock')
        for row in rows:
            if row.get('source_sha256') != report['source']['sha256']:
                raise AVError('Review source hash does not match its performance run')
            if row.get('audition_end_s', row['end_s']) > report['source']['duration_seconds'] + 1e-6:
                raise AVError('Review interval exceeds source duration')
        reports[folder] = report
        sources.extend([file_record(folder/'run.json', 'performance_run'), report['source']])
    videos = {}
    for folder, rows in groups.items():
        for row in rows:
            if not row.get('video_run'):
                continue
            video_folder = _resolve(config.parent, row['video_run'])
            if video_folder not in videos:
                verify_run(video_folder, verify_sources=True)
                video = read_json(video_folder/'video.json')
                if video.get('schema') != 'ave.performance-video.v1' or not video.get('audio_pcm16_identity_verified'):
                    raise AVError('Video must come from a verified performance-video run')
                if video.get('path') != 'episode.mp4' or sha256(video_folder/'episode.mp4') != video.get('sha256'):
                    raise AVError('Video artifact differs from its binding receipt')
                if not video.get('audio_clock', {}).get('verified'):
                    # Earlier bindings compared container duration, which can include subtitle tails.
                    from .performance_video import audit_audio_clock
                    parameters = read_json(video_folder/'run.json')['parameters']
                    video['audio_clock'] = audit_audio_clock(video['source'], parameters['audio_stream'],
                        reports[folder]['audio']['identity'],
                        finite(row.get('video_audio_clock_tolerance_ms', 1.1), 'video audio clock tolerance', 0)/1000)
                videos[video_folder] = video
                sources.extend([file_record(video_folder/'run.json', 'video_run'),
                                file_record(video_folder/'episode.mp4', 'browser_video'), video['source']])
            video = videos[video_folder]
            if (video['audio_witness_sha256'] != reports[folder]['source']['sha256']
                    or video['performance_key'] != reports[folder]['performance_key']):
                raise AVError('Video is bound to a different performance/audio witness')
            tolerance = finite(row.get('video_audio_clock_tolerance_ms',
                video['audio_clock']['declared_alignment_tolerance_seconds']*1000), 'video audio clock tolerance', 0)/1000
            if not 0 < tolerance <= .02 or video['audio_clock']['maximum_frame_pts_difference_from_sample_clock_s'] > tolerance:
                raise AVError('Video clock exceeds the bound declared by this review row')
    for ref in data.get('context_sources', []):
        path = _resolve(config.parent, ref['path'])
        if sha256(path) != ref.get('sha256'):
            raise AVError('Context document changed after configuration')
        sources.append(file_record(path, 'review_context'))
    for row in data['reviews']:
        for frame in row.get('frames', []):
            path = _resolve(config.parent, frame['path'])
            if sha256(path) != frame.get('sha256'):
                raise AVError('Review frame changed after configuration')
            t = finite(frame.get('source_time_s'), 'frame time', 0)
            if not row['start_s'] <= t < row['end_s'] or frame.get('source_id') != row.get('source_id'):
                raise AVError('Frame is outside its declared source/interval')
            if path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
                raise AVError('Review frames must be supported static images')
            sources.append(file_record(path, 'review_frame'))
    sources = list({(x['path'], x['sha256']): x for x in sources}.values())
    # Returned notes must not carry over if video changes while measured audio stays the same.
    bundle_key = digest({'config': sha256(config),
        'inputs': [(s['path'], s['sha256']) for s in sources],
        'video_clocks': {str(p): v['audio_clock'] for p, v in videos.items()}})
    with output_transaction(output, [x['path'] for x in sources] + list(groups)) as stage:
        (stage/'audio').mkdir()
        (stage/'images').mkdir()
        (stage/'context').mkdir()
        (stage/'video').mkdir()
        for index, ref in enumerate(data.get('context_sources', [])):
            path = _resolve(config.parent, ref['path'])
            shutil.copyfile(path, stage/'context'/f'{index:02d}-{path.name}')
        assembled = []
        materialized_videos = {}
        for folder, rows in groups.items():
            report = reports[folder]
            with np.load(folder/'contours.npz', allow_pickle=False) as archive:
                tracks = {k: archive[k] for k in archive.files}
            with _scratch(stage) as scratch:
                stream = select_stream(report['source'], 'audio', report['audio_stream_index'])
                parent_manifest = read_json(folder/'run.json')
                tolerance = parent_manifest.get('parameters', {}).get('explicit_timestamp_tolerance_ms')
                decoded = _decode(report['source'], stream, scratch, 'review-parent',
                    timestamp_tolerance_seconds=tolerance/1000 if tolerance is not None else None)
                if decoded['pcm_sha256'] != report['audio']['identity']['pcm_sha256']:
                    raise AVError('Review decoding differs from the measured native source')
                for row in rows:
                    a, b = row['start_s'], row['end_s']
                    clip = _write_audition(decoded, row.get('audition_start_s', a), row.get('audition_end_s', b),
                                           scratch, stage/'audio'/f'{row["review_id"]}.wav')
                    clip['path'] = 'audio/' + clip['path']
                    cues = [c for c in report['cues'] if c['start_s'] < b and c['end_s'] > a]
                    frames = []
                    for frame in row.get('frames', []):
                        source = _resolve(config.parent, frame['path'])
                        relative = 'images/' + frame['sha256'] + source.suffix.lower()
                        if not (stage/relative).exists():
                            shutil.copyfile(source, stage/relative)
                        frames.append(dict(frame, path=relative))
                    video_binding = None
                    if row.get('video_run'):
                        video_folder = _resolve(config.parent, row['video_run'])
                        video = videos[video_folder]
                        if video_folder not in materialized_videos:
                            video_name = 'video/' + video['sha256'] + '.mp4'
                            # Read-only artifacts may share storage with the verified materialization.
                            # The published files remain ordinary portable files, never symlinks.
                            try:
                                os.link(video_folder/'episode.mp4', stage/video_name)
                                materialization = 'hardlink_to_verified_video_run'
                            except OSError:
                                shutil.copyfile(video_folder/'episode.mp4', stage/video_name)
                                materialization = 'copied_verified_video'
                            vtt_name = 'video/' + video['sha256'] + '.ja.vtt'
                            def stamp(t):
                                ms = round(t*1000)
                                return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d}.{ms%1000:03d}'
                            lines = ['WEBVTT', '']
                            for cue in sorted(report['cues'], key=lambda c: c['start_s']):
                                lines.extend([cue['cue_id'], stamp(cue['start_s'])+' --> '+stamp(cue['end_s']),
                                              html.escape(cue['text'], quote=False), ''])
                            (stage/vtt_name).write_text('\n'.join(lines)+'\n', encoding='utf-8')
                            materialized_videos[video_folder] = {'path': video_name, 'subtitles': vtt_name,
                                'sha256': video['sha256'], 'parent_source_sha256': video['source']['sha256'],
                                'audio_witness_sha256': video['audio_witness_sha256'],
                                'source_clock_offset_s': video['source_clock_offset_s'],
                                'audio_clock': video['audio_clock'],
                                'materialization': materialization, 'packet_verification': video['packet_verification'],
                                'inspection_status': 'NOT_PERFORMED', 'subtitle_authority': 'Existing aligned Japanese captions; not newly adjudicated speech.'}
                        video_binding = materialized_videos[video_folder]
                    assembled.append({k: v for k, v in row.items() if k not in {'run', 'frames'}} | {
                        'audio': clip, 'video': video_binding, 'frames': frames, 'cues': cues,
                        'contours': _tracks_for_interval(tracks, a, b),
                        'performance_run': str(folder), 'performance_key': report['performance_key'],
                        'alignment_counts': dict(Counter(c['alignment']['status'] for c in cues)),
                        'review_status': 'PENDING', 'observed_auditory_feature': None,
                        'observations': [], 'inspection_route': 'NOT_PERFORMED'})
                    print('Prepared ' + row['review_id'], flush=True)
        package = {'schema': 'ave.performance-review.v1', 'bundle_key': bundle_key,
            'title': data.get('title', 'Vocal/performance review'), 'reviews': assembled,
            'provenance': data.get('provenance', {}),
            'scope': 'Computed evidence and an unfilled listening worksheet. No human/model listening or continuous AV inspection is established. Claim/debt IDs are dependencies, not conclusions.',
            'listening_status': 'NOT_PERFORMED', 'continuous_video_status': 'NOT_PERFORMED',
            'summary': {'intervals': len(assembled), 'sources': len(groups),
                'requested_seconds': sum(r['end_s']-r['start_s'] for r in assembled),
                'video_bound_intervals': sum(r['video'] is not None for r in assembled),
                'audition_copies_sample_equal': all(r['audio']['native_samples_equal'] for r in assembled)}}
        write_json(stage/'review.json', package)
        write_json(stage/'notes-template.json', {'schema': 'ave.performance-review.notes.v1',
            'bundle_key': bundle_key, 'records': [empty_note(r) for r in assembled]})
        from .performance_review_view import render_review
        render_review(stage/'review.html', package)
        # Recheck parent artifact membership and content after joining, not just manifest bytes.
        for folder in [*groups, *videos]:
            verify_run(folder)
        return finish_run(stage, 'performance-review', sources,
            {'config_sha256': sha256(config)}, {'result_file': 'review.json', 'view_file': 'review.html',
                'bundle_key': bundle_key, 'review_count': len(assembled), 'listening_status': 'NOT_PERFORMED'})


def empty_note(row):
    return {'review_id': row['review_id'], 'status': 'PENDING', 'reviewer': '',
        'inspected_intervals': [], 'observation': '', 'speaker_notes': '', 'timing_notes': '',
        'uncertainty': '', 'interpretation': '', 'alternative': '', 'needs_video': 'uncertain',
        'inspection_mode': 'audio_only'}


def validate_notes(package, notes):
    """Validate attributable declared observations; never certify their truth or close debts."""
    if notes.get('schema') != 'ave.performance-review.notes.v1' or notes.get('bundle_key') != package['bundle_key']:
        raise AVError('Notes are not bound to this exact review bundle')
    rows = {r['review_id']: r for r in package['reviews']}
    result, seen = [], set()
    if not isinstance(notes.get('records'), list):
        raise AVError('Notes require a records list')
    for note in notes['records']:
        if not isinstance(note, dict):
            raise AVError('Each note must be an object')
        key = note.get('review_id')
        if key not in rows or key in seen:
            raise AVError('Unknown or duplicate note ID')
        seen.add(key)
        row, status = rows[key], note.get('status')
        if status not in {'PENDING', 'DRAFT', 'OBSERVED'}:
            raise AVError('Invalid note status')
        if note.get('needs_video', 'uncertain') not in {'yes', 'no', 'uncertain'}:
            raise AVError('Invalid video-review requirement')
        if note.get('inspection_mode', 'audio_only') not in {'audio_only', 'synchronized_video'}:
            raise AVError('Invalid inspection mode')
        if note.get('inspection_mode') == 'synchronized_video' and not row.get('video'):
            raise AVError('Cannot declare synchronized video inspection without a bound video')
        for field in ('reviewer', 'observation', 'speaker_notes', 'timing_notes', 'uncertainty', 'interpretation', 'alternative'):
            if not isinstance(note.get(field, ''), str):
                raise AVError('Note fields must be text')
        intervals = note.get('inspected_intervals', [])
        if not isinstance(intervals, list):
            raise AVError('Inspected intervals must be a list')
        previous = -math.inf
        for pair in intervals:
            if not isinstance(pair, list) or len(pair) != 2:
                raise AVError('Invalid inspected interval')
            a, b = finite(pair[0], 'inspection start'), finite(pair[1], 'inspection end')
            if not row['start_s'] <= a < b <= row['end_s'] or a < previous:
                raise AVError('Inspected intervals must be ordered, disjoint and inside the review window')
            previous = b
        if status == 'OBSERVED' and (not note.get('reviewer', '').strip() or not note.get('observation', '').strip()
                or not note.get('uncertainty', '').strip() or not intervals):
            raise AVError('Observed notes require reviewer, actual intervals, observation and uncertainty')
        if status == 'OBSERVED' and note.get('interpretation', '').strip() and not note.get('alternative', '').strip():
            raise AVError('Interpretive notes require an alternative explanation')
        result.append({**empty_note(row), **note, 'source_id': row.get('source_id'),
            'source_sha256': row['source_sha256'], 'requested_interval': [row['start_s'], row['end_s']],
            'affected_claims': row.get('affected_claims', []), 'debt_ids': row.get('debt_ids', []),
            'language_records': row.get('language_records', []),
            'observation_class': ('DECLARED_HUMAN_AV_OBSERVATION' if note.get('inspection_mode') == 'synchronized_video'
                else 'DECLARED_HUMAN_LISTENING_OBSERVATION') if status == 'OBSERVED' else 'NO_COMPLETED_OBSERVATION',
            'independent_verification': 'NOT_PERFORMED', 'canonical_adoption': 'PENDING_SEPARATE_ANALYTICAL_REVIEW'})
    return result


def import_notes(review_run, notes, output):
    folder, notes = Path(review_run).resolve(), Path(notes).resolve()
    verify_run(folder)
    package = read_json(folder/'review.json')
    if package.get('schema') != 'ave.performance-review.v1':
        raise AVError('Unsupported review bundle')
    records = validate_notes(package, read_json(notes))
    with output_transaction(output, [folder, notes]) as stage:
        write_json(stage/'observations.json', {'schema': 'ave.performance-review.observations.v1',
            'bundle_key': package['bundle_key'], 'records': records,
            'scope': 'Attributed, declared notes. Structural validation does not certify hearing, truth, complete coverage, or closure of a project debt.'})
        return finish_run(stage, 'performance-review-import', [file_record(folder/'run.json'), file_record(notes)],
            metadata={'result_file': 'observations.json', 'records': len(records),
                'declared_observations': sum(r['status'] == 'OBSERVED' for r in records),
                'debt_closure': 'NOT_PERFORMED'})
