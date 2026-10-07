"""Bind browser-playable copied AV packets to a measured episode audio witness."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .common import (AVError, file_record, finish_run, output_transaction, probe_source,
                     read_json, run, select_stream, sha256, write_json)
from .inventory import verify_run


def _pcm16_hash(path, stream):
    result = run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', f'0:{stream}',
                  '-vn', '-sn', '-dn', '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'], timeout=300)
    value = result.stdout.strip()
    if not value.startswith('SHA256=') or len(value) != 71:
        raise AVError('Unexpected decoded audio hash result')
    return value[7:]


def _packets(path):
    command = ['ffprobe', '-v', 'error', '-show_packets', '-show_data_hash', 'sha256',
               '-show_entries', 'packet=stream_index,pts_time,dts_time,data_hash', '-of', 'json', str(path)]
    return json.loads(run(command, timeout=300).stdout)['packets']


def check_audio_frames(frames, rate, expected_samples, tolerance_s=.0011):
    """Bind each decoded audio frame to the zero-based sample clock, not container duration."""
    samples, maximum = 0, 0.
    if not math.isfinite(tolerance_s) or not 0 < tolerance_s <= .02:
        raise AVError('Declared audio clock tolerance must be positive and at most 20 ms')
    if not frames or rate <= 0 or expected_samples <= 0:
        raise AVError('Audio clock requires decoded frames and a positive sample shape')
    for frame in frames:
        try:
            pts, count = float(frame['pts_time']), int(frame['nb_samples'])
        except (KeyError, TypeError, ValueError) as exc:
            raise AVError('Audio frame lacks a bound timestamp/sample count') from exc
        if not math.isfinite(pts) or count <= 0:
            raise AVError('Invalid decoded audio frame')
        delta = abs(pts-samples/rate)
        if delta > tolerance_s:
            raise AVError(f'Video soundtrack is not on the witness sample clock: {delta:g} seconds')
        maximum = max(maximum, delta)
        samples += count
    if samples != expected_samples:
        raise AVError('Video soundtrack sample count differs from measured witness')
    return {'verified': True, 'decoded_frame_count': len(frames), 'sample_frames': samples,
        'sample_rate_hz': rate, 'maximum_frame_pts_difference_from_sample_clock_s': maximum,
        'declared_alignment_tolerance_seconds': tolerance_s,
        'clock': 'Every decoded frame compared with the zero-based native sample clock; the measured residual is retained, not treated as exact synchrony.'}


def audit_audio_clock(source, stream_index, identity, tolerance_s=.0011):
    stream = select_stream(source, 'audio', stream_index)
    if (int(stream['sample_rate']), stream['channels']) != (identity['sample_rate_hz'], identity['channels']):
        raise AVError('Video soundtrack rate/channels differ from measured witness')
    command = ['ffprobe', '-v', 'error', '-select_streams', str(stream['index']), '-show_frames',
               '-show_entries', 'frame=pts_time,nb_samples', '-of', 'json', source['path']]
    frames = json.loads(run(command, timeout=300).stdout)['frames']
    return check_audio_frames(frames, identity['sample_rate_hz'], identity['sample_frames'], tolerance_s)


def compare_packets(parent, child, parent_indices, child_indices, tolerance_s=.0011):
    if not math.isfinite(tolerance_s) or not 0 <= tolerance_s <= .0011:
        raise AVError('Packet comparison tolerance must be finite and at most 1.1 ms')
    receipts = {}
    for kind in ('video', 'audio'):
        left = [p for p in parent if p['stream_index'] == parent_indices[kind]]
        right = [p for p in child if p['stream_index'] == child_indices[kind]]
        if not left or len(left) != len(right):
            raise AVError(f'{kind} remux packet count changed')
        deltas = []
        for a, b in zip(left, right):
            if not a.get('data_hash') or a['data_hash'] != b.get('data_hash'):
                raise AVError(f'{kind} remux changed encoded packet payloads')
            for field in ('pts_time', 'dts_time'):
                if field not in a or field not in b:
                    raise AVError(f'{kind} remux has unbound packet timestamps')
                try:
                    values = float(a[field]), float(b[field])
                except (TypeError, ValueError) as exc:
                    raise AVError(f'{kind} remux has invalid packet timestamps') from exc
                if not all(math.isfinite(value) for value in values):
                    raise AVError(f'{kind} remux has non-finite packet timestamps')
                deltas.append(abs(values[0]-values[1]))
        maximum = max(deltas)
        if maximum > tolerance_s:
            raise AVError(f'{kind} remux moved timestamps by {maximum:g} seconds')
        receipts[kind] = {'packet_count': len(left), 'encoded_payloads_equal': True,
            'ordered_payload_hash_list_sha256': hashlib.sha256('\n'.join(p['data_hash'] for p in left).encode()).hexdigest(),
            'maximum_pts_or_dts_difference_seconds': maximum, 'allowed_timebase_rounding_seconds': tolerance_s}
    return receipts


def bind_video(input, performance_run, output, *, video_stream=None, audio_stream=None, audio_clock_tolerance_ms=1.1):
    folder = Path(performance_run).resolve()
    verify_run(folder, verify_sources=True)
    report = read_json(folder/'performance.json')
    source = probe_source(input)
    video = select_stream(source, 'video', video_stream)
    audio = select_stream(source, 'audio', audio_stream)
    if video['codec_name'] != 'h264' or video.get('pix_fmt') != 'yuv420p' or audio['codec_name'] != 'aac':
        raise AVError('This packet-copy browser route supports H.264 yuv420p plus AAC only')
    if abs(source['origin_seconds']) > 1e-9 or abs(report['source_origin_seconds']) > 1e-9:
        raise AVError('This conservative video binding requires zero-based episode clocks')
    with output_transaction(output, [input, folder]) as stage:
        audio_clock = audit_audio_clock(source, audio['index'], report['audio']['identity'], audio_clock_tolerance_ms/1000)
        reference_hash = _pcm16_hash(report['source']['path'], report['audio_stream_index'])
        original_hash = _pcm16_hash(source['path'], audio['index'])
        if reference_hash != original_hash:
            raise AVError('Video audio does not equal the existing witness after PCM16 conversion')
        destination = stage/'episode.mp4'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-copyts', '-i', source['path'],
             '-map', f'0:{video["index"]}', '-map', f'0:{audio["index"]}', '-c', 'copy', '-sn', '-dn',
             '-map_metadata', '-1', '-map_chapters', '-1', '-avoid_negative_ts', 'disabled',
             '-video_track_timescale', '1000', '-movflags', '+faststart', str(destination)], timeout=300)
        derivative = probe_source(destination)
        dv, da = select_stream(derivative, 'video'), select_stream(derivative, 'audio')
        if abs(derivative['origin_seconds']) > .0011:
            raise AVError('Browser video does not retain the zero-based episode origin')
        packet_receipt = compare_packets(_packets(source['path']), _packets(destination),
            {'video': video['index'], 'audio': audio['index']}, {'video': dv['index'], 'audio': da['index']})
        derivative_hash = _pcm16_hash(destination, da['index'])
        if derivative_hash != reference_hash:
            raise AVError('Browser video decode differs from the measured PCM16 audio witness')
        result = {'schema': 'ave.performance-video.v1', 'source': source,
            'performance_run': str(folder), 'performance_key': report['performance_key'],
            'audio_witness_sha256': report['source']['sha256'], 'path': 'episode.mp4',
            'sha256': sha256(destination), 'duration_seconds': derivative['duration_seconds'],
            'source_clock_offset_s': 0., 'audio_pcm16_sha256': reference_hash,
            'audio_pcm16_identity_verified': True, 'audio_clock': audio_clock,
            'packet_verification': packet_receipt,
            'transform': 'Full-episode H.264 and AAC packet copy to MP4; no transcoding, resizing, burned captions, normalization or subtitle stream included.',
            'inspection_status': 'NOT_PERFORMED',
            'limits': 'PCM16 equality verifies the existing 16-bit witness, not preservation of every floating-point AAC decoder bit. Packet timestamps are checked separately within recorded timebase rounding. Source synchrony is preserved, not perceptually adjudicated.'}
        write_json(stage/'video.json', result)
        verify_run(folder)
        return finish_run(stage, 'performance-video', [source, report['source'], file_record(folder/'run.json')],
            {'video_stream': video['index'], 'audio_stream': audio['index'],
             'explicit_audio_clock_tolerance_ms': audio_clock_tolerance_ms},
            {'result_file': 'video.json', 'view_file': 'episode.mp4', 'inspection_status': 'NOT_PERFORMED'})
