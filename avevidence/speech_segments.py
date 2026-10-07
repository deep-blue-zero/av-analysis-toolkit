"""Timing authority and optional voice-quality measurements on eligible segments."""
from __future__ import annotations

import math
from pathlib import Path
import re

from .common import AVError, file_record, finish_run, output_transaction, read_json, write_json
from .evidence_queries import PHONETIC
from .inventory import verify_run


def mora_count(reading):
    if not isinstance(reading,str) or re.search(r"[^ぁ-ゖァ-ヺー\s]",reading):
        raise AVError("Mora counts require an explicit kana reading; kanji/character counts are not mora counts")
    small = set("ゃゅょぁぃぅぇぉャュョァィゥェォゎヮ")
    return sum(not c.isspace() and c not in small for c in reading)


def timing_comparison(previous, current):
    gap = float(current["start_s"])-float(previous["end_s"])
    verified = all(r.get("boundary_origin") in PHONETIC for r in (previous,current))
    return {"boundary_interval_s": gap, "speech_response_latency_s": gap if verified else None,
            "origin_A": previous.get("boundary_origin"), "origin_B": current.get("boundary_origin"),
            "quality_flags": [] if verified else ["ALIGNMENT_UNCERTAIN"],
            "definition": "Negative values indicate overlap of the declared spans. Audible overlapping speakers require separate verification."}


def segment_features(witness_run, config, output):
    import numpy as np
    import parselmouth
    from parselmouth.praat import call
    folder = Path(witness_run).resolve(); verify_run(folder)
    witness = read_json(folder/"witness.json"); settings = read_json(config)
    if settings.get("source_sha256") != witness["source_sha256"]:
        raise AVError("Segment feature configuration is bound to another source")
    # An explicit source classification is insufficient without recorded review.
    eligible = (settings.get("source_class") == "isolated_voice_asset" and settings.get("isolation_reviewed") is True
                and settings.get("segment_type") == "non_singing_dialogue" and not settings.get("quality_flags"))
    sound = parselmouth.Sound(str(folder/"audio.wav"))
    if sound.n_channels != 1:
        eligible = False
    rate = int(sound.sampling_frequency)
    samples = sound.values[0] if sound.n_channels == 1 else sound.values.mean(axis=0)
    duration = len(samples)/rate
    report = {"schema":"ave.segment-features.v1","source_sha256":witness["source_sha256"],
        "source_interval_seconds":witness["source_interval_seconds"],"clip_sha256":witness["artifact_sha256"],
        "boundary_origin":settings.get("boundary_origin","estimated_boundary"), "duration_s":duration,
        "voice_quality_status":"ELIGIBLE" if eligible else "OPEN_INELIGIBLE_SOURCE",
        "HNR_db":None,"jitter_local":None,"shimmer_local":None,"spectral_tilt_db_per_octave":None,
        "CPP":None,"CPP_status":"NOT_IMPLEMENTED_WITH_A_VALIDATED_STANDARD_BACKEND",
        "mora_per_s":None,"quality_flags":settings.get("quality_flags",[]),
        "limitations":["Voice quality requires reviewed isolated monophonic speech; eligibility is a declaration, not proof",
            "Spectral tilt is a whole-segment descriptive slope, not a calibrated glottal-source measure",
            "Jitter/shimmer depend on steady voiced periodic speech; values do not establish emotion"]}
    reading = settings.get("kana_reading")
    if reading and settings.get("boundary_origin") in PHONETIC:
        report["mora_per_s"] = mora_count(reading)/duration
        report["mora_rate_definition"] = "Explicit reading mora count / verified utterance span including internal pauses; not syllable articulation rate"
    if eligible:
        floor = float(settings.get("pitch_floor",75)); ceiling = float(settings.get("pitch_ceiling",600))
        if not 20 <= floor < ceiling <= 2000:
            raise AVError("Voice-quality pitch bounds are invalid")
        pitch = sound.to_pitch_ac(time_step=.01,pitch_floor=floor,pitch_ceiling=ceiling)
        f0 = pitch.selected_array["frequency"]
        voiced = f0[f0>0]
        fraction = float(np.mean(f0>0)) if len(f0) else 0
        report["voiced_fraction"] = fraction
        if fraction < .8 or len(voiced)<10:
            report["voice_quality_status"] = "OPEN_LOW_VOICED_FRACTION"
            report["quality_flags"] = sorted(set(report["quality_flags"]+["LOW_F0_CONFIDENCE"]))
        else:
            h = sound.to_harmonicity_cc(time_step=.01,minimum_pitch=floor)
            valid = h.values[(h.values > -100) & np.isfinite(h.values)]
            report["HNR_db"] = float(np.median(valid)) if len(valid) else None
            # Cycle perturbation is meaningful only for separately selected steady voicing.
            if settings.get("steady_voicing_reviewed") is True and duration >= .1:
                pp = call(sound,"To PointProcess (periodic, cc)",floor,ceiling)
                jitter = call(pp,"Get jitter (local)",0,0,1/ceiling,1/floor,1.3)
                shimmer = call([sound,pp],"Get shimmer (local)",0,0,1/ceiling,1/floor,1.3,1.6)
                report["jitter_local"] = float(jitter) if math.isfinite(jitter) else None
                report["shimmer_local"] = float(shimmer) if math.isfinite(shimmer) else None
            n = min(len(samples),rate*30)
            frequencies = np.fft.rfftfreq(n,1/rate)
            power = np.abs(np.fft.rfft(samples[:n]*np.hanning(n)))**2
            use = (frequencies>=300)&(frequencies<=min(5000,rate*.45))&(power>np.max(power)*1e-10)
            if int(use.sum())>=10:
                report["spectral_tilt_db_per_octave"] = float(np.polyfit(np.log2(frequencies[use]),10*np.log10(power[use]),1)[0])
    with output_transaction(output,[folder,config]) as stage:
        write_json(stage/"segment-features.json",report)
        return finish_run(stage,"segment-features",[file_record(folder/"run.json"),file_record(folder/"audio.wav"),file_record(config)],
                          metadata={"result_file":"segment-features.json"})
