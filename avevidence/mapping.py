"""Shared admission/review contract for bounded, rate-one media mappings.

Clock quantization permits bounded endpoint disagreement, NOT filling gaps.
Affine endpoint interpolation below expresses that uncertainty, not verified retiming.
"""
from __future__ import annotations

from fractions import Fraction
from .common import AVError, finite

EPS = 1e-7


def union_intervals(items):
    result = []
    for a, b in sorted(items):
        if b <= a:
            continue
        if result and a <= result[-1][1] + EPS:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def missing_intervals(required, covered):
    result = []
    for a, b in union_intervals(required):
        cursor = a
        for c, d in union_intervals(covered):
            if d <= cursor or c >= b:
                continue
            if c > cursor + EPS:
                result.append([cursor, min(c, b)])
            cursor = max(cursor, min(d, b))
        if cursor < b - EPS:
            result.append([cursor, b])
    return result


def _tick(value):
    try:
        x = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise AVError('Mapping requires a valid stream time base') from exc
    if x <= 0 or x > 1:
        raise AVError('Mapping time-base resolution is unsupported (must be >0 and <=1 second)')
    return float(x)


def timing_basis(parent_stream, derivative_stream):
    p, d = str(parent_stream['time_base']), str(derivative_stream['time_base'])
    return {'schema': 'ave.mapping_timing.v1', 'parent_time_base': p,
            'derivative_time_base': d, 'serialized_seconds_resolution': 0.000001,
            'endpoint_tolerance_seconds': _tick(p) + _tick(d) + 0.000002,
            'policy': 'One tick from each selected clock plus two serialization quanta; never a gap-fill allowance.'}


def mapping_tolerance(mapping, parent_stream=None, derivative_stream=None):
    basis = mapping.get('timing_basis')
    if basis is None:
        # Legacy mappings did not provide verifiable resolution provenance.
        return 0.000001
    if not isinstance(basis, dict) or basis.get('schema') != 'ave.mapping_timing.v1':
        raise AVError('Unsupported mapping timing basis')
    expected = _tick(basis.get('parent_time_base')) + _tick(basis.get('derivative_time_base')) + 0.000002
    if basis.get('serialized_seconds_resolution') != 0.000001:
        raise AVError('Unsupported mapping serialization resolution')
    value = finite(basis.get('endpoint_tolerance_seconds'), 'mapping tolerance', 0)
    if abs(value - expected) > 1e-12:
        raise AVError('Mapping tolerance does not match its derived clock budget')
    for stream, field in ((parent_stream, 'parent_time_base'), (derivative_stream, 'derivative_time_base')):
        if stream is not None and Fraction(str(stream.get('time_base'))) != Fraction(basis[field]):
            raise AVError('Mapping clock budget differs from the selected stream time base')
    return value


def contiguous_offset_spread(segments):
    """Largest onset-offset range inside a connected parent interval.

    A derivative discontinuity must not reset the source clock budget. Only an
    actual parent gap starts a new group; changing extent confidence does not.
    """
    maximum = 0.0
    end = None
    low = high = 0.0
    for pa, pb, da, _ in sorted(segments):
        offset = pa - da
        if end is None or pa > end + EPS:
            low = high = offset
            end = pb
        else:
            low, high = min(low, offset), max(high, offset)
            end = max(end, pb)
        maximum = max(maximum, high - low)
    return maximum


def validated_segments(mapping, parent_duration=None, parent_stream=None, derivative_stream=None):
    tolerance = mapping_tolerance(mapping, parent_stream, derivative_stream)
    result = []
    raw = mapping.get('segments')
    if not isinstance(raw, list) or not raw:
        raise AVError('Mapping has no reviewable segments')
    for s in raw:
        if not isinstance(s, dict):
            raise AVError('Malformed derivative time mapping')
        pa, pb, da, db = [finite(s.get(k), k, 0) for k in (
            'parent_start_seconds', 'parent_end_seconds',
            'derivative_start_seconds', 'derivative_end_seconds')]
        if pb <= pa or db <= da:
            raise AVError('Mapping contains a nonpositive extent')
        if parent_duration is not None and pb > parent_duration + 1e-6:
            raise AVError('Mapped parent interval exceeds its admitted presentation duration')
        if abs((pb-pa)-(db-da)) > 2*tolerance:
            raise AVError('Retimed playback needs a different mapping schema; durations exceed the derived clock budget')
        result.append((pa, pb, da, db))
    if contiguous_offset_spread(result) > tolerance + EPS:
        raise AVError('Retimed playback needs a different mapping schema; contiguous source onset offsets exceed the derived clock budget')
    # Explicitly check both clocks (they can sort identically).
    for start, end in ((0, 1), (2, 3)):
        order = sorted(result, key=lambda x: x[start])
        if any(a[end] > b[start]+EPS for a,b in zip(order,order[1:])):
            raise AVError('Overlapping derivative/parent mappings are ambiguous')
    return result, tolerance


def map_review_intervals(mapping, requested, claimed, parent_duration=None,
                         parent_stream=None, derivative_stream=None):
    segments, tolerance = validated_segments(mapping, parent_duration, parent_stream, derivative_stream)
    if missing_intervals(requested, [[da, db] for _, _, da, db in segments]):
        raise AVError('Presented derivative interval crosses unmapped padding or absent media')
    # Check both unions strictly before allowing endpoint-clock uncertainty.
    if missing_intervals(claimed, [[pa, pb] for pa, pb, _, _ in segments]):
        raise AVError('Claimed source coverage crosses an unmapped gap in the mapping')
    mapped = []
    for a, b in requested:
        for pa, pb, da, db in segments:
            lo, hi = max(a, da), min(b, db)
            if lo < hi:
                scale = (pb-pa)/(db-da)
                mapped.append([pa+(lo-da)*scale, pa+(hi-da)*scale])
    mapped, wanted = union_intervals(mapped), union_intervals(claimed)
    if len(mapped) != len(wanted) or any(abs(x-y) > tolerance for p,q in zip(mapped,wanted) for x,y in zip(p,q)):
        raise AVError('Claimed source coverage differs from derivative-to-parent interval mapping')
    return wanted


def estimated_overlap(mapping, claimed):
    values = mapping.get('estimated_parent_intervals_seconds', [])
    if not values and mapping.get('extent_derivation', {}).get('final_frame_extent_estimated'):
        # Old producers did not identify the tail precisely: qualify conservatively.
        values = [[s['parent_start_seconds'], s['parent_end_seconds']] for s in mapping['segments']]
    return union_intervals([[max(a,c), min(b,d)] for a,b in claimed for c,d in values if max(a,c)<min(b,d)])
