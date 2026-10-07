"""Source-timed deterministic audio instruments. Measurements are not listening.

The canonical decoder remains in audio.py. Tracks never normalize, downmix,
resample, interpolate across gaps, or identify speakers/instruments/emotions.
"""
from __future__ import annotations

import csv
import importlib.metadata
import json
import math
from pathlib import Path
import re
import shutil

from .audio import (_FRAME, _cue_input, _db, _identity, _number, _raw_input, _scratch,
                    _selection, _slice)
from .common import (AVError, file_record, finite, finish_run, interval,
                     output_transaction, probe_source, read_json, run, sha256,
                     write_csv, write_json)

SCHEMA = "ave.audio_tracks.v1"
BASE_FIELDS = ["segment_id", "channel_index", "input_sample_start", "input_sample_end",
               "source_start_seconds", "source_end_seconds", "source_center_seconds"]
TRACK_FIELDS = BASE_FIELDS + ["sample_min", "sample_max", "dc_offset", "rms_amplitude",
    "rms_dbfs", "sample_peak_dbfs", "crest_factor_db", "zero_crossing_rate", "low_energy",
    "spectral_centroid_hz", "spectral_bandwidth_hz", "spectral_rolloff85_hz",
    "spectral_flatness_power", "spectral_entropy_normalized", "spectral_flux_l1",
    "onset_strength_amplitude", "difference_support_start_seconds"]
STEREO_FIELDS = BASE_FIELDS + ["left_rms_dbfs", "right_rms_dbfs", "mid_rms_dbfs",
    "side_rms_dbfs", "right_minus_left_db", "side_energy_fraction", "correlation",
    "mid_side_energy_identity_error"]
CHROMA_FIELDS = BASE_FIELDS + [f"chroma_{x}" for x in
    ("C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B")] + ["chroma_change_l1"]
WAVE_FIELDS = BASE_FIELDS + ["sample_min", "sample_max", "rms_dbfs"]
PROFILES = {"general": (40., 10.), "speech": (64., 10.), "music": (2048/22050*1000, 10.)}


def _numpy():
    try:
        import numpy as np
        return np
    except ImportError as exc:
        raise AVError('audio-track requires NumPy; install with pip install ".[audio]"') from exc


def _coordinates(part, rate, first, last, channel):
    start = part["source_start_seconds"] + first/rate
    end = part["source_start_seconds"] + last/rate
    return {"segment_id": part["segment_id"], "channel_index": channel,
            "input_sample_start": part["input_sample_start"]+first,
            "input_sample_end": part["input_sample_start"]+last,
            "source_start_seconds": start, "source_end_seconds": end,
            "source_center_seconds": (start+end)/2}


def _validate_segments(decoded):
    """A mapped extraction must account for every compact sample exactly once."""
    offset, previous_end = 0, None
    rate = decoded["sample_rate_hz"]
    for s in decoded["segments"]:
        a, b = s["sample_start"], s["sample_end"]
        start, end = s["source_start_seconds"], s["source_end_seconds"]
        if (type(a) is not int or type(b) is not int or a != offset or b <= a
                or not math.isfinite(start) or not math.isfinite(end)
                or abs((end-start)-(b-a)/rate) > 1e-7
                or (previous_end is not None and start < previous_end-1e-9)):
            raise AVError("Invalid or retimed canonical PCM segment map")
        offset, previous_end = b, end
    if offset != decoded["sample_frames"]:
        raise AVError("Canonical PCM map does not cover every sample")


def _preflight(input, start, end, max_duration):
    """Fail early on unbounded long inputs and clearly insufficient scratch disk."""
    path = Path(input).resolve()
    folder = path if path.is_dir() else path.parent
    if (folder/"audio.json").is_file() and (folder/"run.json").is_file():
        receipt = read_json(folder/"audio.json")
        ident = receipt["identity"]
        seconds = ident["sample_frames"]/ident["sample_rate_hz"]
        estimated_bytes = ident["pcm_bytes"]
        witness = {"audio_mapping_sha256": sha256(folder/"audio.json"),
                   "codec_metadata": "See verified parent audio.json and source probe"}
    else:
        source = probe_source(path)
        seconds = source.get("duration_seconds")
        streams = [s for s in source["streams"] if s.get("codec_type") == "audio"]
        # Upper bound across possible selected streams; no channel-layout guesses.
        estimated_bytes = max((float(seconds or 0)*int(s.get("sample_rate", 0))*int(s.get("channels", 0))*8
                               for s in streams), default=0)
        witness = {"source_sha256": source["sha256"], "audio_streams": streams,
                   "packet_side_data": "NOT_INDEXED; probe fields only, not a priming/padding audit"}
    if start is None and end is None and seconds and seconds > max_duration:
        raise AVError(f"Whole input exceeds max-duration={max_duration:g}s; select --start/--end or raise the explicit bound")
    return estimated_bytes, witness


def _spectral_rows(x, rate, n, hop, part, channel, threshold, *, overview_cols=1200, overview_bins=384, music=False, mel=False):
    """Yield rows in bounded FFT batches; return the overview via the last item.

    Only complete noncentered windows. Max-pooled spectra are NAVIGATION views;
    sample supports, not image pixels, define measurement intervals.
    """
    np = _numpy()
    count = max(0, 1+(len(x)-n)//hop)
    if not count:
        yield ("end", {"status": "INSUFFICIENT_SAMPLES", "analysis_frames": 0,
                       "unmeasured_tail_samples": len(x)}, None)
        return
    window = np.hanning(n)
    freq = np.fft.rfftfreq(n, 1/rate)
    cols, bins = min(overview_cols, count), min(overview_bins, len(freq))
    fstarts = np.floor(np.arange(bins)*len(freq)/bins).astype(int)
    pooled = np.zeros((bins, cols), dtype=np.float64)
    first_frames = np.ceil(np.arange(cols)*count/cols).astype(int)
    last_frames = np.ceil((np.arange(cols)+1)*count/cols).astype(int)-1
    previous, previous_norm, previous_chroma = None, None, None
    if mel:
        # Explicit toolkit-owned HTK filterbank. This is a visualization, not a
        # drop-in representation for a learned model with different preprocessing.
        mel_edges = np.linspace(0, 2595*np.log10(1+(rate/2)/700), 66)
        hz_edges = 700*(10**(mel_edges/2595)-1)
        weights = np.maximum(0, np.minimum(
            (freq[None,:]-hz_edges[:-2,None])/(hz_edges[1:-1,None]-hz_edges[:-2,None]),
            (hz_edges[2:,None]-freq[None,:])/(hz_edges[2:,None]-hz_edges[1:-1,None])))
        totals = weights.sum(axis=1, keepdims=True)
        weights /= np.where(totals > 0, totals, 1)
        mel_pool = np.zeros((64, cols), dtype=np.float64)
    if music:
        chosen = (freq >= 50) & (freq <= min(8000, rate/2))
        pitchclasses = np.rint(69+12*np.log2(freq[chosen]/440)).astype(int) % 12
    for batch_start in range(0, count, 64):
        stops = min(count, batch_start+64)
        offsets = np.arange(batch_start, stops)*hop
        batch = np.stack([x[i:i+n] for i in offsets])
        if not np.isfinite(batch).all():
            raise AVError("Nonfinite PCM cannot be measured")
        amplitude = np.abs(np.fft.rfft(batch*window, axis=1))/window.sum()
        amplitude[:, 1: -1 if n % 2 == 0 else None] *= 2
        grouped = np.maximum.reduceat(amplitude, fstarts, axis=1)
        mel_power = (amplitude*amplitude) @ weights.T if mel else None
        for j, first in enumerate(offsets):
            frame_no = batch_start+j
            col = min(cols-1, frame_no*cols//count)
            pooled[:, col] = np.maximum(pooled[:, col], grouped[j])
            if mel:
                mel_pool[:, col] = np.maximum(mel_pool[:, col], mel_power[j])
            y, mag = batch[j], amplitude[j]
            power = mag*mag
            magnitude_sum, power_sum = float(mag.sum()), float(power.sum())
            rms = float(np.sqrt(np.mean(y*y)))
            peak = float(np.max(np.abs(y)))
            if not math.isfinite(power_sum) or not math.isfinite(rms):
                raise AVError("Audio energy exceeds the numeric measurement range")
            norm = mag/magnitude_sum if magnitude_sum > 0 else np.zeros_like(mag)
            pp = power/power_sum if power_sum > 0 else np.zeros_like(power)
            centroid = float(np.sum(norm*freq)) if magnitude_sum > 0 else None
            bandwidth = float(np.sqrt(np.sum(norm*(freq-centroid)**2))) if centroid is not None else None
            flatness = float(np.exp(np.mean(np.log(np.maximum(power, np.finfo(float).tiny))))/(power_sum/len(power))) if power_sum > 0 else None
            entropy = float(-np.sum(pp[pp>0]*np.log(pp[pp>0]))/np.log(len(pp))) if power_sum > 0 else None
            row = _coordinates(part, rate, int(first), int(first+n), channel)
            row.update(sample_min=float(y.min()), sample_max=float(y.max()), dc_offset=float(y.mean()),
                rms_amplitude=rms, rms_dbfs=_db(rms), sample_peak_dbfs=_db(peak),
                crest_factor_db=_db(peak/rms) if rms else None,
                zero_crossing_rate=float(np.mean(np.signbit(y[1:]) != np.signbit(y[:-1]))),
                low_energy=bool(rms < 10**(threshold/20)), spectral_centroid_hz=centroid,
                spectral_bandwidth_hz=bandwidth,
                spectral_rolloff85_hz=float(freq[min(len(freq)-1, np.searchsorted(np.cumsum(mag), .85*magnitude_sum))]) if magnitude_sum > 0 else None,
                spectral_flatness_power=flatness, spectral_entropy_normalized=entropy,
                spectral_flux_l1=float(np.maximum(norm-previous_norm, 0).sum()) if previous is not None else None,
                onset_strength_amplitude=float(np.maximum(mag-previous, 0).sum()) if previous is not None else None,
                difference_support_start_seconds=part["source_start_seconds"]+(int(first)-hop)/rate if previous is not None else None)
            yield ("track", row, None)
            if music:
                chroma = np.bincount(pitchclasses, weights=power[chosen], minlength=12)
                total = chroma.sum()
                chroma = chroma/total if total > 0 else np.zeros(12)
                cr = _coordinates(part, rate, int(first), int(first+n), channel)
                cr.update({k: float(v) if total>0 else None for k,v in zip(CHROMA_FIELDS[7:-1], chroma)})
                cr["chroma_change_l1"] = float(np.abs(chroma-previous_chroma).sum()) if previous_chroma is not None and total>0 else None
                previous_chroma = chroma if total>0 else None
                yield ("chroma", cr, None)
            previous, previous_norm = mag.copy(), norm.copy()
    payload = {"amplitude": pooled, "frequency_low_hz": freq[fstarts],
               "frequency_high_hz": np.r_[freq[fstarts[1:]], freq[-1]],
               "source_support_start_seconds": part["source_start_seconds"]+first_frames*hop/rate,
               "source_support_end_seconds": part["source_start_seconds"]+(last_frames*hop+n)/rate}
    if mel:
        payload.update(mel_power=mel_pool, mel_center_hz=hz_edges[1:-1],
                       mel_edges_hz=hz_edges, mel_empty_filters=(totals[:,0] == 0))
    yield ("end", {"status": "MEASURED", "analysis_frames": count,
                   "unmeasured_tail_samples": len(x)-((count-1)*hop+n),
                   "overview_time_bins": cols, "overview_frequency_bins": bins}, payload)


def _onsets_and_tempo(rows, rate, hop):
    """Conservative spectral-change candidates, NOT note/drum/beat attribution."""
    np = _numpy()
    strength = np.array([r["onset_strength_amplitude"] or 0 for r in rows])
    if len(strength) < 3 or float(strength.max()) <= 1e-10:
        return [], {"status": "NO_SALIENT_ONSET_VARIATION", "candidates": []}
    median = float(np.median(strength))
    cutoff = median + max(3*float(np.median(np.abs(strength-median))), .1*float(strength.max()))
    candidates = [i for i in range(1,len(strength)-1)
                  if strength[i] > cutoff and strength[i] > strength[i-1] and strength[i] >= strength[i+1]]
    # Strongest peak wins within an 80 ms neighborhood; not the earliest weak peak.
    selected = []
    refractory = max(1, round(.08*rate/hop))
    for i in sorted(candidates, key=lambda i: (-strength[i], i)):
        if all(abs(i-j) >= refractory for j in selected):
            selected.append(i)
    events = []
    for i in sorted(selected):
        r = rows[i]
        events.append({"event_type": "SPECTRAL_ONSET_CANDIDATE", "segment_id": r["segment_id"],
            "channel_index": r["channel_index"], "source_center_seconds": r["source_center_seconds"],
            "support_start_seconds": r["difference_support_start_seconds"],
            "support_end_seconds": r["source_end_seconds"],
            "strength_amplitude": float(strength[i]), "threshold_amplitude": cutoff,
            "timing_scope": "Peak of difference between overlapping windows; not exact physical onset",
            "authority": "DETERMINISTIC_ESTIMATE_NOT_REVIEWED"})
    if len(events)<4 or len(strength)*hop/rate<4:
        return events, {"status": "INSUFFICIENT_RHYTHMIC_EVIDENCE", "candidates": []}
    v = strength-strength.mean()
    size = 1 << (2*len(v)-1).bit_length()
    ft = np.fft.rfft(v, n=size)
    ac = np.fft.irfft(ft*np.conj(ft), n=size)[:len(v)]
    if ac[0] <= 0:
        return events, {"status": "NO_SALIENT_ONSET_VARIATION", "candidates": []}
    ac /= ac[0]
    lo, hi = max(2, math.ceil(60*rate/(240*hop))), min(len(ac)-2, math.floor(60*rate/(40*hop)))
    peaks = [k for k in range(lo,hi+1) if ac[k]>.1 and ac[k]>=ac[k-1] and ac[k]>ac[k+1]]
    peaks = sorted(peaks, key=lambda k: (-ac[k], k))[:3]
    return events, {"status": "CANDIDATES_ONLY" if peaks else "NO_STABLE_PERIOD_CANDIDATE",
        "candidates": [{"bpm": 60*rate/(k*hop), "lag_frames": k, "normalized_autocorrelation": float(ac[k])} for k in peaks],
        "scope": "40-240 BPM periodicity hypotheses; half/double-time aliases retained; score is not confidence; no downbeat/meter inference"}


def _stereo_rows(samples, part, rate, n, hop):
    np = _numpy()
    for first in range(0, len(samples)-n+1, hop):
        left, right = samples[first:first+n,0], samples[first:first+n,1]
        mid, side = (left+right)/2, (left-right)/2
        le,re,me,se = [float(np.mean(v*v)) for v in (left,right,mid,side)]
        lc,rc = left-left.mean(), right-right.mean()
        # Scaling cancels in Pearson correlation and avoids overflow in the
        # product of two sum-of-squares terms on finite high-amplitude inputs.
        ls,rs = float(np.max(np.abs(lc))),float(np.max(np.abs(rc)))
        ln,rn = lc/ls if ls else lc,rc/rs if rs else rc
        denominator = float(np.sqrt(np.sum(ln*ln)*np.sum(rn*rn)))
        row = _coordinates(part,rate,first,first+n,None)
        row.update(left_rms_dbfs=_db(math.sqrt(le)), right_rms_dbfs=_db(math.sqrt(re)),
            mid_rms_dbfs=_db(math.sqrt(me)), side_rms_dbfs=_db(math.sqrt(se)),
            right_minus_left_db=10*math.log10(re/le) if le>0 and re>0 else None,
            side_energy_fraction=se/(me+se) if me+se>0 else None,
            correlation=max(-1.,min(1.,float(np.sum(ln*rn))/denominator)) if denominator>0 else None,
            mid_side_energy_identity_error=abs((me+se)-(le+re)/2))
        yield row


def _waveform_and_summary(samples, part, rate, channel):
    np = _numpy()
    rows, energy, peak, lo, hi = [], 0., 0., math.inf, -math.inf
    bin_samples = max(1, round(.01*rate))
    for first in range(0,len(samples),bin_samples):
        y = samples[first:first+bin_samples]
        if not np.isfinite(y).all():
            raise AVError("Nonfinite PCM cannot be measured")
        e = float(np.sum(y*y))
        if not math.isfinite(e):
            raise AVError("Audio energy exceeds the numeric measurement range")
        a,b = float(y.min()), float(y.max())
        energy += e; peak = max(peak,abs(a),abs(b)); lo = min(lo,a); hi = max(hi,b)
        row = _coordinates(part,rate,first,first+len(y),channel)
        row.update(sample_min=a,sample_max=b,rms_dbfs=_db(math.sqrt(e/len(y))))
        rows.append(row)
    return rows, {"channel_index": channel, "sample_frames": len(samples), "rms_dbfs": _db(math.sqrt(energy/len(samples))),
        "sample_peak_dbfs": _db(peak), "sample_min": lo, "sample_max": hi,
        "digital_zero": energy==0, "method": "All selected canonical f64 samples, including final partial waveform bin; no s16 conversion"}


def _ebu(decoded, part, scratch, stage):
    """Reset the FFmpeg meter per contiguous segment, never across missing time."""
    rate = decoded["sample_rate_hz"]
    selected = _slice(decoded,{"parts":[part]},scratch/"ebu.f64le")
    try:
        args = ["ffmpeg","-hide_banner","-nostdin","-nostats","-v","info"] + _raw_input(selected)
        result = run(args+["-af","ebur128=metadata=1:peak=true,ashowinfo,ametadata=print:file=-","-f","null","-"], timeout=600)
    finally:
        Path(selected["raw_path"]).unlink(missing_ok=True)
    raw_path = f"ebu_raw_{part['segment_id']}.txt"
    (stage/raw_path).write_text(result.stdout+"\n--- STDERR ---\n"+result.stderr,encoding="utf-8")
    observed = {int(m[1]): {"pts": int(m[2]), "rate": int(m[3]), "count": int(m[4])}
                for m in _FRAME.finditer(result.stderr)}
    frames=[]; current=None
    for line in result.stdout.splitlines():
        m = re.match(r"frame:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([-+\d.eE]+)",line)
        if m:
            current={"frame":int(m[1]),"pts":int(m[2]),"pts_time":float(m[3]),"values":{}}
            frames.append(current)
        elif line.startswith("lavfi.r128.") and current is not None:
            k,v=line.split("=",1); current["values"][k]=_number(v)
    if not frames and selected["sample_frames"]>=math.ceil(rate*.1):
        raise AVError("FFmpeg ebur128 produced no parseable metadata")
    rows=[]
    for f in frames:
        # ebur128 output PTS identifies the START of a 100 ms block. The meter
        # values describe the state at its END, not its PTS or its center.
        witness=observed.get(f["frame"])
        if witness is None or witness["rate"]!=rate or witness["pts"]!=f["pts"]:
            raise AVError("Cannot bind loudness metadata to an observed audio frame")
        first=f["pts"]; last=first+witness["count"]
        if first<0 or last<=first or last>selected["sample_frames"] or abs(f["pts_time"]-first/rate)>1e-5:
            raise AVError("Unexpected ebur128 metadata time base")
        t=part["source_start_seconds"]+last/rate
        values=f["values"]
        m=values.get("lavfi.r128.M"); s=values.get("lavfi.r128.S"); integrated=values.get("lavfi.r128.I")
        row={"segment_id":part["segment_id"],"source_block_start_seconds":part["source_start_seconds"]+first/rate,
            "source_measurement_end_seconds":t,"input_sample_end":part["input_sample_start"]+last,
            "momentary_support_start_seconds":max(part["source_start_seconds"],t-.4),
            "short_term_support_start_seconds":max(part["source_start_seconds"],t-3.),
            "momentary_lufs":m if last>=round(rate*.4) and m is not None and m>-120 else None,
            "short_term_lufs":s if last>=round(rate*3) and s is not None and s>-120 else None,
            "integrated_since_segment_start_lufs":integrated if last>=round(rate*.4) and integrated is not None and integrated>-70 else None,
            "loudness_range_lu":values.get("lavfi.r128.LRA") if last>=round(rate*3) and integrated is not None and integrated>-70 else None,
            "cumulative_true_peak_dbtp":_db(values.get("lavfi.r128.true_peak") or 0),
            "momentary_window_complete":last>=round(rate*.4),"short_term_window_complete":last>=round(rate*3)}
        rows.append(row)
    return rows,{"status":"MEASURED" if rows else "INSUFFICIENT_SAMPLES", "raw_report":raw_path,
        "metadata_frames":len(rows),"unmetered_tail_samples":selected["sample_frames"]-(rows[-1]["input_sample_end"]-part["input_sample_start"]) if rows else selected["sample_frames"],
        "state_reset":"Start of each selected contiguous segment; no preselection history or gap bridging",
        "timing":"100 ms metadata block onset PTS converted to block END; trailing 400 ms and 3 s supports",
        "null_policy":"Warm-up and digital-silence/-70 LUFS integrated gate sentinel omitted; raw metadata retained",
        "true_peak_scope":"Running maximum since segment start; metadata amplitude rounded by FFmpeg; not per-block true peak",
        "lra_scope":"Short excerpts give weak/unstable LRA estimates; no reliability certificate"}


def audio_track(input, output, *, stream_index=None, start=None, end=None, profile="general",
                window_ms=None, hop_ms=None, spectral_scales_ms=(20.,80.), stereo="auto",
                loudness="auto", render=True, energy_threshold_dbfs=-45., max_duration=300., max_frames=250000):
    np = _numpy()
    if profile not in PROFILES or stereo not in {"auto","off","channels-0-1"} or loudness not in {"auto","off","require"}:
        raise AVError("Invalid profile, stereo or loudness mode")
    max_duration=finite(max_duration,"max-duration",.001)
    threshold=finite(energy_threshold_dbfs,"energy threshold")
    if not -300 <= threshold <= 20:
        raise AVError("energy-threshold-dbfs must be between -300 and 20")
    window_ms=finite(PROFILES[profile][0] if window_ms is None else window_ms,"window-ms",.1)
    hop_ms=finite(PROFILES[profile][1] if hop_ms is None else hop_ms,"hop-ms",.1)
    if window_ms>1000 or hop_ms>window_ms or type(max_frames) is not int or max_frames<1:
        raise AVError("Require 0 < hop-ms <= window-ms <= 1000 and a positive max-frames")
    scales=list(dict.fromkeys([window_ms]+[finite(v,"spectral scale",.1) for v in spectral_scales_ms]))
    if len(scales)>4 or max(scales)>1000 or min(scales)<hop_ms:
        raise AVError("At most four scales; require hop-ms <= each scale <= 1000")
    estimate, witness=_preflight(input,start,end,max_duration)
    with output_transaction(output,[input]) as stage:
        if shutil.disk_usage(stage).free < estimate*1.1+64*1024**2:
            raise AVError("Insufficient scratch disk for the full native f64 decode")
        with _scratch(stage) as scratch:
            decoded,parent,dependencies=_cue_input(input,stream_index,scratch)
            # _cue_input restores a parent clock for an extracted audio run.
            # Keep the parent's uncertainty budget too, not the finer WAV clock.
            for dep in dependencies:
                if dep.get("kind")=="audio_mapping":
                    if witness.get("audio_mapping_sha256") != dep["sha256"]:
                        raise AVError("Audio mapping changed after preflight")
                    mapped_identity=read_json(dep["path"])["identity"]
                    for key in ("timestamp_quantization_tolerance_seconds", "max_timestamp_adjustment_seconds"):
                        decoded[key]=max(decoded[key],mapped_identity[key])
            if witness.get("source_sha256") and witness["source_sha256"]!=parent["parent_source_sha256"]:
                raise AVError("Source changed between preflight and analysis")
            _validate_segments(decoded)
            if start is None and end is None:
                a,b=decoded["segments"][0]["source_start_seconds"],decoded["segments"][-1]["source_end_seconds"]
            else:
                a,b=interval(start,end,decoded["segments"][-1]["source_end_seconds"])
            if b-a>max_duration:
                raise AVError("Requested interval exceeds max-duration")
            selection=_selection(decoded,a,b)
            if not selection["parts"]:
                raise AVError("Requested interval contains no decoded audio")
            rate,channels=decoded["sample_rate_hz"],decoded["channels"]
            hop=max(1,round(rate*hop_ms/1000)); lengths=list(dict.fromkeys(max(4,round(rate*v/1000)) for v in scales))
            total_frames=sum(max(0,1+(p["input_sample_end"]-p["input_sample_start"]-n)//hop)*channels
                             for p in selection["parts"] for n in lengths)
            if total_frames>max_frames:
                raise AVError(f"Estimated {total_frames} feature rows exceed max-frames={max_frames}; reduce interval/scales or raise explicit bound")
            warnings=[]
            known_stereo=decoded["channel_layout"]=="stereo" and channels==2
            do_stereo=stereo!="off" and (known_stereo or stereo=="channels-0-1")
            if do_stereo and channels!=2:
                raise AVError("Stereo analysis requires exactly two channels")
            if stereo=="auto" and not known_stereo:
                warnings.append("STEREO_NOT_APPLICABLE_OR_LAYOUT_UNKNOWN; channel count alone is not stereo evidence")
            do_ebu=loudness!="off" and bool(decoded["channel_layout"])
            if loudness=="require" and not do_ebu:
                raise AVError("EBU loudness requires a known channel layout; no guessed channel weighting")
            if loudness=="auto" and not do_ebu:
                warnings.append("LOUDNESS_SKIPPED_UNKNOWN_LAYOUT")
            report={"schema":SCHEMA,**parent,"identity":_identity(decoded),"selection":selection,
                "profile":profile,"source_metadata_witness":witness,"segments":[],"spectra":[],
                "tracks":[],"stereo": {"status":"ENABLED" if do_stereo else "NOT_APPLIED",
                  "basis":"SOURCE_DECLARED_STEREO" if known_stereo else "OPERATOR_DECLARED_CHANNEL_PAIR_NOT_VERIFIED" if do_stereo else None,
                  "formula":"M=(ch0+ch1)/2; S=(ch0-ch1)/2; side_energy_fraction=E(S)/(E(M)+E(S)); correlation is mean-centered Pearson"},
                "loudness":{"requested":loudness,"segments":[]},"music":[],"warnings":warnings,
                "methods":{"numpy_version":importlib.metadata.version("numpy"),"window":"symmetric Hann", "center":False,
                  "frame_lengths_samples":lengths,"hop_length_samples":hop,"frame_ms_realized":[1000*n/rate for n in lengths],
                  "tail_policy":"No spectral padding; incomplete tail counted; waveform and global summaries include ALL samples",
                  "spectral":"Single-sided amplitude spectrum, window-sum calibrated; magnitude centroid/bandwidth/85% rolloff; power flatness and normalized entropy",
                  "flux":"Positive L1-normalized magnitude change; onset strength is positive UNNORMALIZED amplitude change; both reset at discontinuities",
                  "low_energy_threshold_dbfs":threshold,"low_energy_is_not_vad":True,
                  "chroma":"Music only: power folded into nearest A4=440 equal-tempered pitch class, 50-8000 Hz, L1 normalized; not tuning/chord/instrument recognition",
                  "overview":"Spectrograms max-pool native FFT amplitude in time/frequency; NPZ records per-column support; visualization only",
                  "decode_scope":"Full canonical source decode precedes bounded analysis; f64 scratch is removed; no source mutation",
                  "mel":"Primary scale only: 64 HTK triangular bands, normalized by discrete filter weight sum; max pooled power; empty filters flagged; no waveform resampling",
                  "waveform_normalization":False,"resampling":False,"implicit_downmix":False},
                "epistemic_class":"COMPUTED_SIGNAL_EVIDENCE", "perceptual_review":"NOT_PERFORMED",
                "limitations":["Final-mix features cannot be assigned to a singer or instrument without separate evidence",
                    "No emotion, breathiness, voice-quality, speaker, chord or downbeat identification",
                    "Estimated musical events carry window support, not a claim of sample-accurate auditory onset",
                    "No learned models or source separation are run"]}
            memory=np.memmap(decoded["raw_path"],dtype="<f8",mode="r",shape=(decoded["sample_frames"],channels))
            events=[]; wave_rows=[]; stereo_rows=[]; ebu_rows=[]
            try:
                for si,part0 in enumerate(selection["parts"]):
                    part=dict(part0,segment_id=f"s{si:03d}")
                    samples=memory[part["input_sample_start"]:part["input_sample_end"]]
                    segment={**part,"channels":[]}
                    for channel in range(channels):
                        wav,summary=_waveform_and_summary(samples[:,channel],part,rate,channel)
                        wave_rows.extend(wav); segment["channels"].append(summary)
                        for index,n in enumerate(lengths):
                            name=f"tracks_{part['segment_id']}_ch{channel}_n{n}"
                            rows=[]; chroma=[]
                            with (stage/(name+".jsonl")).open("x",encoding="utf-8",newline="\n") as jf:
                                for kind,row,payload in _spectral_rows(samples[:,channel],rate,n,hop,part,channel,threshold,music=(profile=="music" and index==0),mel=(index==0)):
                                    if kind=="track":
                                        jf.write(json.dumps(row,allow_nan=False,separators=(",",":"))+"\n")
                                        rows.append(row)
                                    elif kind=="chroma":chroma.append(row)
                                    else:
                                        track={"path":name+".csv","jsonl_path":name+".jsonl","segment_id":part["segment_id"],
                                            "channel_index":channel,"frame_length_samples":n,"primary":index==0,**row}
                                        report["tracks"].append(track)
                                        if payload is not None:
                                            np.savez_compressed(stage/(name+"_spectrum.npz"),**payload)
                                            report["spectra"].append({"path":name+"_spectrum.npz","segment_id":part["segment_id"],
                                                "channel_index":channel,"frame_length_samples":n,"has_mel":index==0,"source_start_seconds":part["source_start_seconds"],
                                                "source_end_seconds":part["source_end_seconds"]})
                            write_csv(stage/(name+".csv"),rows,TRACK_FIELDS)
                            if index==0:
                                ev,tempo=_onsets_and_tempo(rows,rate,hop); events.extend(ev)
                                if profile=="music":
                                    cp=name+"_chroma.csv";write_csv(stage/cp,chroma,CHROMA_FIELDS)
                                    report["music"].append({"segment_id":part["segment_id"],"channel_index":channel,
                                        "chroma_path":cp,"tempo":tempo})
                    report["segments"].append(segment)
                    if do_stereo:stereo_rows.extend(_stereo_rows(samples,part,rate,lengths[0],hop))
                    if do_ebu:
                        try:
                            values,meta=_ebu(decoded,part,scratch,stage)
                            ebu_rows.extend(values);report["loudness"]["segments"].append({"segment_id":part["segment_id"],**meta})
                        except AVError as exc:
                            if loudness=="require":raise
                            report["loudness"]["segments"].append({"segment_id":part["segment_id"],"status":"UNAVAILABLE","reason":str(exc)})
                            warnings.append(f"LOUDNESS_UNAVAILABLE_{part['segment_id']}")
            finally:
                # Close explicitly: Windows refuses unlinking an open mmap.
                memory._mmap.close()
            write_csv(stage/"waveform.csv",wave_rows,WAVE_FIELDS)
            if do_stereo:write_csv(stage/"stereo.csv",stereo_rows,STEREO_FIELDS)
            if ebu_rows:write_csv(stage/"loudness.csv",ebu_rows,list(ebu_rows[0]))
            write_json(stage/"audio_events.json",{"schema":"ave.audio_events.v1",**parent,"events":events,
                "status":"ESTIMATES_NOT_REVIEWED","window_support_is_not_exact_onset":True})
            report["files"]={"waveform":"waveform.csv","events":"audio_events.json",
                "stereo":"stereo.csv" if do_stereo else None,"loudness":"loudness.csv" if ebu_rows else None}
            report["artifacts_lineage"]={"parent_pcm_sha256":decoded["pcm_sha256"],
                "selected_pcm_coordinates":"Per-row input_sample_start/end in compact canonical PCM; see segment source mapping",
                "artifact_hashes":"run.json artifacts; commands.json includes exact FFmpeg invocations"}
            if render:
                from .audio_render import render_into
                report["renders"]=render_into(stage,stage,report)
            else:report["renders"]=[]
            write_json(stage/"audio_tracks.json",report)
            (stage/"README.md").write_text(_run_readme(report),encoding="utf-8")
        return finish_run(stage,"audio_track",dependencies,
            {"stream_index":stream_index,"start":start,"end":end,"profile":profile,"window_ms":window_ms,
             "hop_ms":hop_ms,"spectral_scales_ms":scales,"stereo":stereo,"loudness":loudness,
             "render":render,"max_duration":max_duration,"max_frames":max_frames,"energy_threshold_dbfs":threshold},
            {"result_file":"audio_tracks.json","coverage_status":selection["status"],"warnings":warnings})


def _run_readme(report):
    return ("# Audio instrument run\n\nStart with `audio_tracks.json`; plots are in `renders/`.\n\n"
        "Authority: **computed signal evidence, not auditory review**. Spectrograms are pooled navigation views. "
        "Use sample supports in CSV/JSONL for timing, never pixel positions. Source gaps are not filled.\n\n"
        f"Parent: `{report['parent_source_sha256']}`, audio stream {report['parent_stream_index']}. "
        f"Coverage: {report['selection']['status']}. Profile: {report['profile']}.\n\n"
        "Music events are candidates. Full-mix statistics do not identify a singer, instrument or emotion. "
        "Momentary/short-term loudness has 400 ms/3 s history; its 100 ms output cadence is not its temporal resolution.\n\n"
        "Verify with `ave verify RUN`; package with `ave pack RUN ARCHIVE.zip`. No review receipts are fabricated.\n")
