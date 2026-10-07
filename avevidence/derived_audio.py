"""Visible source-separation witnesses; the raw mix remains the reference."""
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, probe_source, read_json, select_stream, sha256, write_json
from .inventory import digest_value, verify_run


def admit_separation(raw_witness_run, separated_audio, config, output):
    import numpy as np
    from .audio import _decode, _scratch
    from .performance_contours import compute_contours
    raw = Path(raw_witness_run).resolve(); verify_run(raw)
    witness = read_json(raw/"witness.json")
    settings = read_json(config)
    if settings.get("parent_clip_sha256") != witness["artifact_sha256"]:
        raise AVError("Separation does not identify the exact raw mix clip")
    digest_value(settings.get("model_sha256"))
    if not settings.get("model") or not settings.get("model_version") or not settings.get("parameters"):
        raise AVError("Separation needs a model identity, version/hash and parameters")
    source = probe_source(separated_audio); stream = select_stream(source,"audio")
    with output_transaction(output,[raw,separated_audio,config]) as stage:
        results = []
        with _scratch(stage) as scratch:
            for name,path in (("raw",raw/"audio.wav"),("separated",Path(separated_audio))):
                src = probe_source(path); st = select_stream(src,"audio")
                decoded = _decode(src,st,scratch,name,max_output_bytes=536870912,timeout_s=120)
                if len(decoded["segments"])!=1:
                    raise AVError("Separated witness requires continuous source-mapped coverage")
                signal = np.memmap(decoded["raw_path"],dtype="<f8",mode="r",shape=(decoded["sample_frames"],decoded["channels"]))
                mono = np.asarray(signal.mean(axis=1)); del signal
                meta = {"identity":decoded,"segments":decoded["segments"]}
                tracks = compute_contours(mono,meta)
                np.savez_compressed(stage/(name+"-contours.npz"),**tracks)
                pitch = tracks["pitch_hz"]; voiced = pitch[np.isfinite(pitch)]
                results.append({"label":name,"sample_frames":decoded["sample_frames"],"rate":decoded["sample_rate_hz"],
                    "channels":decoded["channels"],"F0_median_hz":float(np.median(voiced)) if len(voiced) else None,
                    "median_mix_rms_dbfs":float(np.median(tracks["rms_dbfs"])),"pcm_sha256":decoded["pcm_sha256"]})
            if results[0]["rate"]!=results[1]["rate"] or results[0]["sample_frames"]!=results[1]["sample_frames"]:
                raise AVError("Separation changed rate/length; a verified retiming/resampling mapping is required")
        a,b = results
        delta = 12*np.log2(b["F0_median_hz"]/a["F0_median_hz"]) if a["F0_median_hz"] and b["F0_median_hz"] else None
        report = {"schema":"ave.separated-witness.v1","source_sha256":witness["source_sha256"],
            "source_interval_seconds":witness["source_interval_seconds"],"raw_witness_manifest_sha256":sha256(raw/"run.json"),
            "separated_sha256":source["sha256"],"separation":settings,"results":results,
            "F0_median_change_semitones":float(delta) if delta is not None else None,
            "quality_flags":["UNVERIFIED_SOURCE_SEPARATION"]+(["SEPARATION_ARTIFACT"] if delta is None or abs(delta)>2 else []),
            "preference":"NO_AUTOMATIC_PROMOTION","scope":"Derived witness only; HNR/timing differences require eligible segment comparisons"}
        write_json(stage/"separation.json",report)
        return finish_run(stage,"admit-separation",[file_record(raw/"run.json"),file_record(raw/"audio.wav"),source,file_record(config)],
                          metadata={"result_file":"separation.json"})
