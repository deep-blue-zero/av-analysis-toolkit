"""Public, source-bound queries of retained contours. No emotional classification."""
from __future__ import annotations

import math
from pathlib import Path

from .common import AVError, file_record, finish_run, interval, output_transaction, read_json, sha256, write_json
from .inventory import verify_run
from .mapping import missing_intervals

BOUNDARIES = {"subtitle_boundary", "forced_aligned_candidate", "forced_aligned_verified",
              "ASR_boundary", "VAD_boundary", "manual_verified", "manual_scene_boundary", "estimated_boundary"}
PHONETIC = {"forced_aligned_verified", "manual_verified"}


def load_contours(run_dir):
    import numpy as np
    folder = Path(run_dir).resolve()
    verify_run(folder)
    manifest = read_json(folder / "run.json")
    meta = read_json(folder / "contours.json")
    if (folder / "performance.json").is_file():
        report = read_json(folder / "performance.json")
        audio = report["audio"]
    else:
        audio = read_json(folder / "audio.json")
    if meta.get("clock") != "original_pts_minus_source_origin" or audio.get("clock") != meta["clock"]:
        raise AVError("Contour query requires a source-relative clock")
    source_hash = audio.get("source_sha256", audio.get("parent_source_sha256"))
    if not any(s["sha256"] == source_hash for s in manifest["sources"]):
        raise AVError("Contour audio identity is not admitted by the run")
    if meta["segments"] != audio["segments"] or meta["sample_rate_hz"] != audio["identity"]["sample_rate_hz"]:
        raise AVError("Contour coverage differs from the native audio mapping")
    with np.load(folder / "contours.npz", allow_pickle=False) as archive:
        tracks = {k: archive[k] for k in archive.files}
    needed = {"time_s", "pitch_hz", "rms_dbfs", "sample_start", "sample_end", "segment_id"}
    if not needed <= tracks.keys() or len({len(x) for x in tracks.values()}) != 1:
        raise AVError("Malformed contour columns")
    if not np.isfinite(tracks["time_s"]).all() or not (np.diff(tracks["time_s"]) > 0).all():
        raise AVError("Nonmonotonic/nonfinite contour clock")
    return folder, meta, audio, tracks


def query_tracks(tracks, meta, audio, start, end, *, boundary_origin="estimated_boundary",
                 selection="centers", threshold_dbfs=-50., source_class="unknown", context_flags=()):
    import numpy as np
    from .analysis_schema import flags as validate_flags
    a, b = interval(start, end)
    if boundary_origin not in BOUNDARIES or selection not in {"centers", "contained_rms_windows"}:
        raise AVError("Unknown boundary origin or window selection policy")
    threshold_dbfs = float(threshold_dbfs)
    if not math.isfinite(threshold_dbfs):
        raise AVError("Energy threshold must be finite")
    coverage = [[s["source_start_seconds"], s["source_end_seconds"]] for s in meta["segments"]]
    if missing_intervals([[a, b]], coverage):
        raise AVError("Query crosses an unresolved audio gap or lies outside decoded coverage")
    pick = (tracks["time_s"] >= a) & (tracks["time_s"] < b)
    if selection == "contained_rms_windows":
        rate = meta["sample_rate_hz"]
        for sid, segment in enumerate(meta["segments"]):
            rows = tracks["segment_id"] == sid
            lo = segment["source_start_seconds"] + (tracks["sample_start"] - segment["sample_start"]) / rate
            hi = segment["source_start_seconds"] + (tracks["sample_end"] - segment["sample_start"]) / rate
            pick[rows] &= (lo[rows] >= a) & (hi[rows] <= b)
    energy, f0, times = tracks["rms_dbfs"][pick], tracks["pitch_hz"][pick], tracks["time_s"][pick]
    if not len(times) or not np.isfinite(energy).all():
        raise AVError("No usable finite RMS windows in query")
    voiced_pick = np.isfinite(f0) & (f0 > 0)
    voiced = f0[voiced_pick]
    quality = set(validate_flags(list(context_flags)))
    if boundary_origin == "subtitle_boundary":
        quality.add("SUBTITLE_BOUNDARY_ONLY")
    elif boundary_origin not in PHONETIC:
        quality.add("ALIGNMENT_UNCERTAIN")
    isolated = source_class == "isolated_voice_asset" and audio.get("speaker_isolation_verified") is True
    if not isolated:
        quality.add("SPEAKER_ISOLATION_NOT_ESTABLISHED")
    fraction = float(voiced_pick.mean())
    if fraction < .4 or len(voiced) < 5:
        quality.add("LOW_F0_CONFIDENCE")
    floor = meta.get("configuration", {}).get("floor", 75.)
    ceiling = meta.get("configuration", {}).get("ceiling", 600.)
    if len(voiced) and np.mean((voiced < floor * 1.03) | (voiced > ceiling / 1.03)) > .1:
        quality.update({"F0_BOUNDARY_SATURATION", "LOW_F0_CONFIDENCE"})
    pair = voiced_pick[1:] & voiced_pick[:-1]
    pair &= np.diff(times) <= meta.get("configuration", {}).get("hop_ms", 10) / 1000 * 1.5
    change = np.abs(12 * np.log2(np.maximum(f0[1:], 1e-9) / np.maximum(f0[:-1], 1e-9)))
    jumps = int(np.sum(pair & (change >= 10)))
    if jumps:
        quality.update({"F0_DISCONTINUITY", "LOW_F0_CONFIDENCE"})
    strength = tracks.get("pitch_strength")
    strength_median = None
    if strength is not None:
        selected = strength[pick][voiced_pick]
        selected = selected[np.isfinite(selected)]
        strength_median = float(np.median(selected)) if len(selected) else None
        if strength_median is not None and strength_median < .6:
            quality.add("LOW_F0_CONFIDENCE")
    pitch = {"median_hz": None, "p10_hz": None, "p90_hz": None, "span_semitones": None,
             "slope_semitones_per_s": None, "phrase_final_slope_semitones_per_s": None,
             "voiced_fraction": fraction, "octave_jump_candidates": jumps,
             "candidate_strength_median": strength_median, "confidence": None,
             "confidence_note": "Quality flags are heuristics; correlation is not calibrated confidence"}
    if len(voiced) >= 5:
        p10, median, p90 = np.quantile(voiced, [.1, .5, .9])
        semitones = 12 * np.log2(voiced)
        pitch.update(median_hz=float(median), p10_hz=float(p10), p90_hz=float(p90),
                     span_semitones=float(12 * np.log2(p90 / p10)))
        def slope(t, y):
            return float(np.polyfit(t - t[0], y, 1)[0]) if len(t) >= 5 and t[-1] > t[0] else None
        pitch["slope_semitones_per_s"] = slope(times[voiced_pick], semitones)
        tail = times[voiced_pick] >= b - min(.5, (b-a) * .2)
        pitch["phrase_final_slope_semitones_per_s"] = slope(times[voiced_pick][tail], semitones[tail])
    # Quiet runs describe analysis-window centers, not speech pauses or exact event extents.
    low = energy < threshold_dbfs
    runs, first = [], None
    hop = meta.get("configuration", {}).get("hop_ms", 10) / 1000
    for i, value in enumerate(low):
        if value and first is None:
            first = i
        stop = first is not None and (not value or i == len(low)-1 or (i+1 < len(low) and times[i+1]-times[i] > hop*1.5))
        if stop:
            last = i if value else i-1
            runs.append({"first_center_s": float(times[first]), "last_center_s": float(times[last]),
                         "center_span_s": float(times[last]-times[first]), "window_count": last-first+1})
            first = None
    return {"schema": "ave.contour-query.v1", "source_sha256": audio.get("source_sha256", audio.get("parent_source_sha256")),
        "stream_index": audio.get("stream_index", audio.get("parent_stream_index")), "clock": meta["clock"],
        "interval_seconds": [a, b], "interval_length_s": b-a, "boundary_origin": boundary_origin,
        "window_selection": selection, "contour_rows": len(times), "source_class": source_class,
        "channel_transform": audio.get("transform"), "pitch": pitch,
        "energy": {"median_mixed_rms_dbfs": float(np.median(energy)),
                   "window_power_mean_dbfs": float(10*np.log10(np.mean(10**(energy/10)))),
                   "peak_window_dbfs": float(np.max(energy)), "threshold_dbfs": threshold_dbfs,
                   "fraction_below_threshold": float(low.mean()), "low_energy_runs": runs},
        "quality_flags": sorted(quality), "speaker_baseline_eligible": isolated and not quality.intersection(
            {"LOW_F0_CONFIDENCE", "SPEAKER_OVERLAP", "SEPARATION_ARTIFACT", "BGM_CONTAMINATION"}),
        "definitions": {"RMS": "40 ms or configured overlapping digital windows; includes all selected-channel sound; never isolated vocal SPL",
            "window_selection": "Centers in [a,b) can include energy outside the interval; contained mode requires RMS sample bounds within it",
            "pitch": "Candidate mixed-signal periodicity; phrase-final slope uses final min(0.5s,20%) of the requested span",
            "low_energy": "Below a declared digital threshold; neither silence, speech absence nor emotional hesitation"},
        "clock_uncertainty_s": audio["identity"].get("max_timestamp_adjustment_seconds"),
        "boundary_accuracy_s": None, "epistemic_class": "DETERMINISTIC_MEASUREMENT",
        "not_established": ["emotion", "speaker_identity", "exact_speech_latency", "instrument_identity"]}


def contour_query(run_dir, output, *, start=None, end=None, queries=None, **options):
    folder, meta, audio, tracks = load_contours(run_dir)
    config = read_json(queries) if queries else {"queries": [{"id": "interval", "start_s": start, "end_s": end, **options}]}
    rows = config.get("queries")
    if not isinstance(rows, list) or not 0 < len(rows) <= 1000:
        raise AVError("Require 1..1000 bounded queries")
    dependencies = [file_record(folder / "run.json"), file_record(folder / "contours.npz"),
                    file_record(folder / "contours.json")]
    if queries:
        dependencies.append(file_record(queries))
    results = []
    for q in rows:
        args = {k: q[k] for k in ("boundary_origin", "selection", "threshold_dbfs", "source_class", "context_flags") if k in q}
        result = query_tracks(tracks, meta, audio, q.get("start_s"), q.get("end_s"), **args)
        results.append(dict(result, query_id=q.get("id", str(len(results)+1))))
    with output_transaction(output, [folder] + ([queries] if queries else [])) as stage:
        write_json(stage / "queries.json", {"schema": "ave.contour-queries.v1", "rows": results,
            "input_manifest_sha256": sha256(folder / "run.json"), "input_npz_sha256": sha256(folder / "contours.npz")})
        return finish_run(stage, "contour-query", dependencies, metadata={"result_file": "queries.json", "query_count": len(results)})


def compare_intervals(run_dir, output, *, a_start, a_end, b_start, b_end, boundary_origin="estimated_boundary"):
    folder, meta, audio, tracks = load_contours(run_dir)
    a = query_tracks(tracks, meta, audio, a_start, a_end, boundary_origin=boundary_origin)
    b = query_tracks(tracks, meta, audio, b_start, b_end, boundary_origin=boundary_origin)
    delta = {"mixed_window_power_db_B_minus_A": b["energy"]["window_power_mean_dbfs"]-a["energy"]["window_power_mean_dbfs"],
             "pitch_median_semitones_B_minus_A": None}
    if a["pitch"]["median_hz"] and b["pitch"]["median_hz"]:
        delta["pitch_median_semitones_B_minus_A"] = 12*math.log2(b["pitch"]["median_hz"]/a["pitch"]["median_hz"])
    with output_transaction(output, [folder]) as stage:
        write_json(stage / "comparison.json", {"schema": "ave.contrast.v1", "A": a, "B": b, "differences": delta,
            "OPEN": ["speaking_rate", "speech_pause_behavior", "visual_expression", "movement", "shot_grammar", "auditory_delivery"],
            "interpretation": None, "scope": "Contrast of retained mixed-signal measurements; other witnesses can be linked through the claim report"})
        return finish_run(stage, "compare", [file_record(folder / "run.json"), file_record(folder / "contours.npz")],
                          metadata={"result_file": "comparison.json"})
