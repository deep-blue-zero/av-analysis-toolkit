"""Window correspondence with native verification and conservative match classes."""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import fftconvolve, find_peaks

from .media import proxy
from .spectrogram import transformed_matches


def ncc(template, target):
    t = np.asarray(template, dtype=float)
    y = np.asarray(target, dtype=float)
    if len(t) < 2 or len(y) < len(t):
        return np.array([])
    t = t - t.mean()
    energy = np.dot(t, t)
    if energy < 1e-14:
        return np.zeros(len(y)-len(t)+1)
    n = len(t)
    s = np.r_[0., np.cumsum(y)]
    ss = np.r_[0., np.cumsum(y*y)]
    denom = np.sqrt(np.maximum(0., ss[n:]-ss[:-n]-(s[n:]-s[:-n])**2/n)*energy)
    dot = fftconvolve(y, t[::-1], mode="valid")
    return np.clip(np.divide(dot, denom, out=np.zeros_like(dot), where=denom > 1e-12), -1., 1.)


def information(y):
    """Reject silence and simple stationary tones as automatic identity evidence."""
    z = y-y.mean()
    p = np.abs(np.fft.rfft(z*np.hanning(len(z))))**2
    p = p/(p.sum()+1e-30)
    bins = float(np.exp(-np.sum(p*np.log(p+1e-30))))
    rms = float(np.sqrt(np.mean(z*z)))
    return {"spectral_effective_bins": bins, "rms": rms,
            "sufficient": bool(rms > 1e-6 and bins >= 12 and len(y) >= 1920)}


def peaks(scores, threshold, distance):
    if not len(scores):
        return []
    absolute = np.abs(scores)
    p, _ = find_peaks(absolute, height=threshold, distance=distance)
    p = sorted(set(p.tolist()+([0] if absolute[0] >= threshold else [])+
                   ([len(scores)-1] if absolute[-1] >= threshold else [])), key=lambda i: -absolute[i])
    chosen = []
    for i in p:
        if not any(abs(i-j) < distance for j in chosen):
            chosen.append(i)
    return chosen


def native_verify(a, b, rate_a, rate_b, row):
    """Refine proxy timing locally against original f64 samples when rates agree."""
    ca, cb = row["channel_a"], row["channel_b"]
    same_rate = rate_a == rate_b
    row["verification_representation"] = "native_rate_f64" if same_rate else "per_channel_16khz_search_proxy"
    if not same_rate:
        row["method"] = "mean-centered per-channel 16 kHz waveform NCC; native PCM equality is not claimed across sample rates"
        a, b = proxy(a, rate_a), proxy(b, rate_b)
        rate_a = rate_b = 16000
    qa = round(row["query_start_s"]*rate_a)
    qb = min(len(a), round(row["query_end_s"]*rate_a))
    ta = round(row["target_start_s"]*rate_b)
    n = qb-qa
    pad = max(3, math.ceil(rate_a/16000)*2)
    left, right = max(0, ta-pad), min(len(b), ta+n+pad)
    scores = ncc(a[qa:qb, ca], b[left:right, cb])
    if not len(scores):
        return row
    ta = left+int(np.argmax(np.abs(scores)))
    tb = ta+n
    x, y = a[qa:qb, ca], b[ta:tb, cb]
    xc, yc = x-x.mean(), y-y.mean()
    gain = float(np.dot(xc, yc)/max(np.dot(xc, xc), 1e-30))
    corr = float(np.max(np.abs(scores)))
    chunks = [(u, v) for u, v in zip(np.array_split(x, 4), np.array_split(y, 4)) if len(u) > 1]
    segment_scores = [float(abs(ncc(u, v)[0])) for u, v in chunks]
    quarter_energy = [float(np.mean((u-u.mean())**2)) for u, _ in chunks]
    active = [score for score, energy in zip(segment_scores, quarter_energy) if energy >= max(quarter_energy)*.025]
    exact = bool(same_rate and a.shape[1] == b.shape[1] and np.array_equal(a[qa:qb], b[ta:tb]))
    row.update(target_start_s=ta/rate_b, target_end_s=tb/rate_b, exact_native_pcm=exact,
               alignment_tolerance_s=1/rate_a)
    row["metrics"].update({"native_ncc" if same_rate else "resampled_verification_ncc": corr})
    row["metrics"].update(fitted_gain_b_over_a=gain,
                          minimum_quarter_ncc=min(segment_scores), quarter_ncc=segment_scores,
                          active_quarter_ncc=active, active_quarter_count=len(active),
                          active_quarter_rule="Reference quarter variance at least 2.5% of strongest quarter",
                          normalized_residual_rms=float(np.linalg.norm(yc-gain*xc)/max(np.linalg.norm(yc), 1e-30)))
    row["classification"] = ("EXACT" if exact else "NEAR_EXACT" if corr >= .985 and active and min(active) >= .95
                              else "PROBABLE_DERIVATIVE" if corr >= .85 else "POSSIBLE_DERIVATIVE")
    row["auto_performance_link"] = bool(row["information"]["sufficient"] and
                                        len(active) >= 2 and row["classification"] in {"EXACT", "NEAR_EXACT"})
    return row


def compare(a, b, rate_a=16000, rate_b=16000, *, fragments=True, transforms=False,
            self_compare=False, min_fragment_s=.24, max_matches=2000):
    if min_fragment_s < .12:
        raise ValueError("Minimum fragment must be at least 120 ms")
    x, y = proxy(a, rate_a), proxy(b, rate_b)
    if len(x) > len(y) and not self_compare:
        rows, stats = compare(b, a, rate_b, rate_a, fragments=fragments, transforms=transforms,
                              min_fragment_s=min_fragment_s, max_matches=max_matches)
        for r in rows:
            r["query_start_s"], r["target_start_s"] = r["target_start_s"], r["query_start_s"]
            r["query_end_s"], r["target_end_s"] = r["target_end_s"], r["query_end_s"]
            r["channel_a"], r["channel_b"] = r["channel_b"], r["channel_a"]
            r["duration_ratio_b_over_a"] = 1/r["duration_ratio_b_over_a"]
            r["pitch_shift_semitones_b_minus_a"] *= -1
            gain = r["metrics"].get("fitted_gain_b_over_a")
            if gain is not None and abs(gain) > 1e-15:
                r["metrics"]["fitted_gain_b_over_a"] = r["metrics"].get("native_ncc", r["metrics"].get("resampled_verification_ncc"))**2/gain
            r["metrics"]["matched_duration_s"] = r["query_end_s"]-r["query_start_s"]
            r["information_evaluated_side"] = "b"
            r["search_was_reversed"] = True
        return rows, stats
    windows = [(0, len(x))] if len(x) <= 12*16000 and not self_compare else []
    if fragments:
        for length in (min_fragment_s, .5, 1.):
            n = round(length*16000)
            if n < len(x):
                step = max(1, n//2)
                windows.extend((i, i+n) for i in sorted(set(list(range(0, len(x)-n+1, step))+[len(x)-n])))
    windows = list(dict.fromkeys(windows))
    rows, capped = [], False
    # Preserve channels. Cross-channel matching detects swaps and downmix copies
    # without claiming multi-channel sample equality from a mono proxy.
    full_matches = set()
    full_coverage = {}
    for start, end in windows:
        if end-start < round(min_fragment_s*16000):
            continue
        for ca in range(x.shape[1]):
            info = information(x[start:end, ca])
            if info["rms"] <= 1e-6:
                continue
            for cb in range(y.shape[1]):
                if len(x) == len(y) and (ca, cb) in full_matches and (start, end) != (0, len(x)):
                    continue
                scores = ncc(x[start:end, ca], y[:, cb])
                for offset in peaks(scores, .72, max(1, int((end-start)*.65))):
                    if self_compare and offset < end:
                        continue
                    if (start, end) != (0, len(x)) and any(
                        offset/16000 >= lo-.002 and (offset+end-start)/16000 <= hi+.002
                        for lo, hi in full_coverage.get((ca, cb), [])):
                        continue
                    row = {"query_start_s": start/16000, "query_end_s": end/16000,
                           "target_start_s": offset/16000, "target_end_s": (offset+end-start)/16000,
                           "channel_a": ca, "channel_b": cb,
                           "metrics": {"proxy_ncc": float(abs(scores[offset])), "matched_duration_s": (end-start)/16000},
                           "information": info, "classification": "POSSIBLE_DERIVATIVE",
                           "duration_ratio_b_over_a": 1., "pitch_shift_semitones_b_minus_a": 0.,
                           "method": "mean-centered waveform NCC; per-channel 16 kHz retrieval and native f64 refinement",
                           "alignment_tolerance_s": 1/16000, "exact_native_pcm": False,
                           "auto_performance_link": False, "transform_authority": "estimated_hypothesis_not_processing_history"}
                    row = native_verify(a, b, rate_a, rate_b, row)
                    rows.append(row)
                    if start == 0 and end == len(x) and row["classification"] in {"EXACT", "NEAR_EXACT"}:
                        full_matches.add((ca, cb))
                        full_coverage.setdefault((ca, cb), []).append((row["target_start_s"], row["target_end_s"]))
                    if len(rows) >= max_matches:
                        capped = True
                        break
                if capped: break
            if capped: break
        if capped: break
    transform_windows = 0
    if transforms and not capped:
        # Full bounded reference first; use bounded fragments for longer windows.
        tw = [(0, len(x))] if len(x) <= 12*16000 else []
        if fragments:
            for size in (32000, 16000, 8000):
                if size < len(x):
                    tw.extend((i, i+size) for i in sorted(set(list(range(0, len(x)-size+1, size//2))+[len(x)-size])))
        transformed_full = False
        for start, end in tw:
            if transformed_full and len(x) == len(y) and (start,end) != (0,len(x)): continue
            transform_windows += 1
            for ca in range(x.shape[1]):
                if not information(x[start:end, ca])["sufficient"]: continue
                for cb in range(y.shape[1]):
                    for row in transformed_matches(x[start:end, ca], y[:, cb]):
                        row.update(channel_a=ca, channel_b=cb, information=information(x[start:end, ca]))
                        row["query_start_s"] += start/16000
                        row["query_end_s"] += start/16000
                        if not self_compare or row["target_start_s"] >= row["query_end_s"]:
                            rows.append(row)
                            if start == 0 and end == len(x) and row["classification"] == "PROBABLE_DERIVATIVE":
                                transformed_full = True
    rows = consolidate(rows)
    return rows[:max_matches], {"waveform_windows": len(windows), "spectral_transform_windows": transform_windows,
        "match_limit_reached": capped or len(rows) > max_matches,
        "transform_search": "bounded_duration_0.95_to_1.05263_pitch_plus_minus_1_semitone_and_speed" if transforms else "disabled",
        "nonmatch_status": "UNRESOLVED", "score_is_probability": False}


def consolidate(rows):
    rank = {"EXACT": 4, "NEAR_EXACT": 3, "PROBABLE_DERIVATIVE": 2, "POSSIBLE_DERIVATIVE": 1}
    chosen = []
    for row in sorted(rows, key=lambda r: (-rank[r["classification"]], -(r["query_end_s"]-r["query_start_s"]),
                         -r["metrics"].get("native_ncc", r["metrics"].get("spectral_temporal_ncc", 0)))):
        qa, qb, ta, tb = [row[k] for k in ("query_start_s", "query_end_s", "target_start_s", "target_end_s")]
        if any(qa >= p["query_start_s"]-.025 and qb <= p["query_end_s"]+.025 and
               ta >= p["target_start_s"]-.04 and tb <= p["target_end_s"]+.04 for p in chosen):
            continue
        chosen.append(row)
    return sorted(chosen, key=lambda r: (r["target_start_s"], r["query_start_s"]))
