"""Conservative within-character distributions with recorded-performance deduplication."""
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, read_json, write_json


def build_baselines(config, reuse_database, output, *, revision=None, minimum=8):
    import numpy as np
    from .reuse.graph import snapshot
    from .analysis_schema import flags
    from .evidence_claims import _artifact
    data = read_json(config)
    graph = snapshot(reuse_database, revision)
    if type(minimum) is not int or minimum < 3:
        raise AVError("Baseline minimum must be at least three independent performances")
    members = {o: p for p in graph["performances"] for o in p["occurrence_ids"]}
    occurrences = {o["occurrence_id"]: o for o in graph["occurrences"]}
    groups, excluded, dependencies = {}, [], {}
    for row in data.get("rows", []):
        occurrence = row.get("occurrence_id")
        identity = members.get(occurrence)
        original = occurrences.get(occurrence, {})
        qflags = flags(row.get("quality_flags", []))
        reason = None
        if not identity or original.get("vocal_status") != "verified_voice" or not original.get("speaker") or original.get("unit_kind") != "performance":
            reason = "UNKNOWN_PERFORMANCE_OR_SPEAKER"
        elif original.get("quality") != "isolated_voice_asset" or row.get("segment_type") != "non_singing_dialogue":
            reason = "NOT_VERIFIED_ISOLATED_NON_SINGING_DIALOGUE"
        elif qflags or row.get("quality_reviewed") is not True:
            reason = "QUALITY_NOT_ACCEPTED"
        if reason:
            excluded.append({"occurrence_id": occurrence, "reason": reason}); continue
        _artifact(row.get("measurement_artifact"), Path(config).resolve().parent, dependencies)
        key = (original["speaker"], identity["performance_id"])
        if key in groups:
            excluded.append({"occurrence_id": occurrence, "reason": "DUPLICATE_RECORDED_PERFORMANCE"}); continue
        groups[key] = row
    result = []
    for speaker in sorted({k[0] for k in groups}):
        rows = [r for (s,p),r in groups.items() if s == speaker]
        fields = {}
        for feature in ("f0_median_hz", "pitch_span_semitones", "rate_mora_per_s", "pause_ratio", "rms_dbfs", "HNR_db"):
            values = [r.get("features", {}).get(feature) for r in rows]
            values = np.array([v for v in values if type(v) in (int,float) and np.isfinite(v)], dtype=float)
            if len(values) < minimum:
                fields[feature] = {"status": "OPEN", "n": len(values), "flag": "INSUFFICIENT_INDEPENDENT_PERFORMANCES"}
                continue
            med = float(np.median(values)); mad = float(np.median(np.abs(values-med)))
            fields[feature] = {"status": "MEASURED_DISTRIBUTION", "n": len(values), "median": med, "MAD": mad,
                "p10": float(np.quantile(values,.1)), "p90": float(np.quantile(values,.9)),
                "robust_z_definition": "0.67448975 * (x-median)/MAD; undefined when MAD=0", "emotion_label": None}
        result.append({"speaker": speaker, "independent_performances": len(rows), "features": fields})
    with output_transaction(output, [config, reuse_database]) as stage:
        write_json(stage/"baselines.json", {"schema": "ave.vocal-baselines.v1", "reuse_revision": graph["revision"], "baselines": result,
            "excluded": excluded, "context_labels_used_for_clustering": False, "quality_scope": "Operator-reviewed isolated non-singing voice assets only"})
        return finish_run(stage, "vocal-baseline", [file_record(config), file_record(reuse_database)] + list(dependencies.values()),
                          metadata={"result_file": "baselines.json"})
