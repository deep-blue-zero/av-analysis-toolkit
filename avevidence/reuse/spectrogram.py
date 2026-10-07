"""Bounded transform hypotheses from changing spectral structure, not embeddings."""
from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve, stft, find_peaks


def features(y, rate=16000):
    window, hop = 512, 80
    if len(y) < window + 4*hop:
        return np.empty((72, 0))
    f, _, z = stft(y, fs=rate, nperseg=window, noverlap=window-hop, boundary=None, padded=False)
    bands = np.geomspace(100, 7400, 72)
    magnitude = np.abs(z)
    values = np.array([np.interp(bands, f, col) for col in magnitude.T]).T
    # Compression permits gain/EQ changes; matching removes each band's local
    # mean, so stationary accompaniment alone is not sufficient evidence.
    return np.log1p(values / (np.sqrt(np.mean(y*y))*.03 + 1e-12))


def matrix_ncc(template, target):
    n = template.shape[1]
    if n < 5 or target.shape[1] < n:
        return np.array([])
    t = template - template.mean(axis=1, keepdims=True)
    power = float(np.sum(t*t))
    if power < 1e-9:
        return np.array([])
    numerator = fftconvolve(target, t[:, ::-1], mode="valid", axes=1).sum(axis=0)
    sums = np.c_[np.zeros(len(target)), np.cumsum(target, axis=1)]
    squares = np.c_[np.zeros(len(target)), np.cumsum(target*target, axis=1)]
    energy = np.maximum(0., (squares[:, n:]-squares[:, :-n] - (sums[:, n:]-sums[:, :-n])**2/n).sum(axis=0))
    return np.divide(numerator, np.sqrt(power*energy), out=np.zeros_like(numerator), where=energy > 1e-12)


def warp(values, ratio, semitones, sample_count, rate=16000):
    bands = np.geomspace(100, 7400, len(values))
    pitch = 2**(semitones/12)
    shifted = np.array([np.interp(bands/pitch, bands, col, left=0, right=0) for col in values.T]).T
    n = max(5, 1 + int((sample_count*ratio-512)//80))
    return np.array([np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(row)), row) for row in shifted])


def local_support(template, target):
    """Bounded monotone frame correspondence tolerates stretch synthesis jitter.

    This is a second diagnostic, not independent probabilistic evidence. Keep
    the original global score and the permitted local displacement in reports.
    """
    a = template-template.mean(axis=1, keepdims=True)
    b = target-target.mean(axis=1, keepdims=True)
    a /= np.maximum(np.linalg.norm(a, axis=0), 1e-12)
    b /= np.maximum(np.linalg.norm(b, axis=0), 1e-12)
    values, previous, displacements = [], 0, []
    for i in range(a.shape[1]):
        lo, hi = max(previous, i-7), min(b.shape[1], i+8)
        if lo >= hi: break
        scores = a[:, i] @ b[:, lo:hi]
        best = int(np.argmax(scores - .004*np.abs(np.arange(lo, hi)-i)))
        previous = lo+best
        values.append(float(scores[best]))
        displacements.append(abs(previous-i))
    return {"local_frame_median_cosine": float(np.median(values)) if values else 0.,
            "local_frame_support_fraction": float(np.mean(np.array(values) >= .8)) if values else 0.,
            "maximum_local_displacement_s": max(displacements, default=0)*.005}


def transformed_matches(x, y, rate=16000, threshold=.78):
    """Search a declared finite grid. Scores are not calibrated probabilities."""
    if len(x) < .35*rate or len(x) > 12*rate:
        return []
    ratios = [.95, 1/1.05, 1., 1.05, 1/.95]
    hypotheses = {(r, p) for r in ratios for p in (-1., 0., 1.)}
    hypotheses.update((r, -12*np.log2(r)) for r in ratios)
    rows = []
    tx, ty = features(x, rate), features(y, rate)
    if not tx.size or not ty.size:
        return rows
    for ratio, pitch in sorted(hypotheses):
        template = warp(tx, ratio, pitch, len(x), rate)
        scores = matrix_ncc(template, ty)
        if not len(scores):
            continue
        peaks, _ = find_peaks(scores, height=threshold, distance=max(1, int(len(template[0])*.7)))
        peaks = sorted(set(peaks.tolist() + ([0] if scores[0] >= threshold else []) +
                           ([len(scores)-1] if scores[-1] >= threshold else [])), key=lambda p: -scores[p])[:24]
        for offset in peaks:
            start = offset*.005
            end = start + len(x)/rate*ratio
            if end <= len(y)/rate+.02:
                support = local_support(template, ty[:, offset:offset+template.shape[1]])
                probable = scores[offset] >= .9 or (scores[offset] >= .8 and
                    support["local_frame_median_cosine"] >= .9 and support["local_frame_support_fraction"] >= .8)
                rows.append({"query_start_s": 0., "query_end_s": len(x)/rate,
                    "target_start_s": start, "target_end_s": min(end, len(y)/rate),
                    "metrics": {"spectral_temporal_ncc": float(scores[offset]),
                                "matched_duration_s": len(x)/rate, **support},
                    "duration_ratio_b_over_a": float(ratio), "pitch_shift_semitones_b_minus_a": float(pitch),
                    "alignment_tolerance_s": .04,
                    "method": "log-frequency STFT temporal correspondence; bounded duration/pitch grid",
                    "classification": "PROBABLE_DERIVATIVE" if probable else "POSSIBLE_DERIVATIVE",
                    "exact_native_pcm": False, "auto_performance_link": False,
                    "transform_authority": "estimated_hypothesis_not_processing_history"})
    # Retain the best hypothesis for a region, never count each grid point as a
    # separate reuse occurrence.
    selected = []
    for row in sorted(rows, key=lambda r: -r["metrics"]["spectral_temporal_ncc"]):
        if not any(abs(row["target_start_s"]-p["target_start_s"]) < .15 for p in selected):
            selected.append(row)
    return sorted(selected, key=lambda r: r["target_start_s"])
