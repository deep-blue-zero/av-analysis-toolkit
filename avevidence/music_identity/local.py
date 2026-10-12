"""Adapters to the existing reuse index, correspondence ledger and music DSP."""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile

from ..common import AVError, read_json, sha256, write_json
from ..event_contracts import canonical_digest
from .contracts import candidate, query_receipt


def _query_row(context):
    scope = context["source"]
    return {"id": "music-query", "source": context["probe"]["path"], "source_sha256": scope["sha256"],
        "audio_stream": scope["audio_stream"], "start_s": scope["interval_seconds"][0],
        "end_s": scope["interval_seconds"][1], "unit_kind": "window", "role": "both"}


def _correspondences(report, target_hash, target_stream, target_interval):
    hits = []
    for match in report.get("matches", []):
        query_side = next((s for s in ("a", "b") if match[s]["source_sha256"] == target_hash and
            match[s]["audio_stream"] == target_stream and match[s]["source_start_s"] >= target_interval[0] - .002 and
            match[s]["source_end_s"] <= target_interval[1] + .002), None)
        if query_side:
            hits.append({**{k: match[k] for k in ("match_id", "correspondence_id", "classification", "a", "b", "transformations", "metrics")},
                         "status": match["classification"], "exact_native_pcm": match.get("exact_native_pcm", False),
                         "verification_representation": match.get("verification_representation"),
                         "information_sufficient": match.get("information", {}).get("sufficient", False),
                         "auto_performance_link": match.get("auto_performance_link", False)})
    return hits


def search_corpus(context):
    """Snapshot the supplied ledger read-only; never mutate its historical evidence."""
    from ..reuse.workflow import run_scan
    from ..reuse.graph import ledger
    scope, stage, database = context["source"], context["stage"], Path(context["local_corpus"]).resolve()
    receipt = query_receipt(scope, "local-reuse")
    if not database.is_file():
        raise AVError("Local corpus must be an existing reuse SQLite ledger")
    before = sha256(database)
    config = stage / "execution" / "corpus-query.json"
    write_json(config, {"schema": "ave.reuse.config.v1", "occurrences": [_query_row(context)]})
    with tempfile.TemporaryDirectory(prefix="ave-music-corpus-") as temp:
        temp = Path(temp)
        # SQLite backup includes committed WAL state; copying a live database file alone would not.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(temp / "working.sqlite")) as dst:
            with dst:
                src.backup(dst)
        snapshot_hash = sha256(temp / "working.sqlite")
        old_occurrences, _, _, _ = ledger(temp / "working.sqlite")
        run_scan(config, stage / "local-reuse", cache_dir=context.get("reuse_cache") or temp / "cache", database=temp / "working.sqlite",
                 preview_count=0, max_candidates=context.get("max_candidates", 20), transforms=context.get("transforms", False))
    if sha256(database) != before:
        raise AVError("Supplied corpus changed during music query; retry against a stable snapshot")
    report = read_json(stage / "local-reuse" / "reuse.json")
    hits = _correspondences(report, scope["sha256"], scope["audio_stream"], scope["interval_seconds"])
    context["correspondences"].extend(hits)
    candidates = [candidate(provider="local-reuse", level="RECORDING", provider_id=h["correspondence_id"],
        basis="LOCAL_CORRESPONDENCE", query_id=receipt["query_id"], interval=scope["interval_seconds"],
        relation={"match_id": h["match_id"], "correspondence_id": h["correspondence_id"], "audio_correspondence_status": h["status"],
                  "catalogue_recording_identity": "UNRESOLVED"}) for h in hits]
    known = []
    query_occurrences = report.get("occurrences", [])
    for prior in old_occurrences.values():
        if any(all(prior.get(k) == current.get(k) for k in ("source_sha256", "audio_stream", "sample_start", "sample_end", "pcm_sha256", "clock")) for current in query_occurrences):
            known.append(prior["occurrence_id"])
            candidates.append(candidate(provider="local-reuse", level="RECORDING", provider_id=prior["occurrence_id"],
                basis="LOCAL_CORPUS_SOURCE_IDENTITY", query_id=receipt["query_id"], interval=scope["interval_seconds"],
                relation={"occurrence_id": prior["occurrence_id"], "audio_correspondence_status": "EXACT",
                          "basis": "Same source hash, selected stream, native sample bounds and PCM hash", "catalogue_recording_identity": "UNRESOLVED"}))
    receipt.update(status="CANDIDATE" if candidates else "UNRESOLVED", candidate_ids=[c["candidate_id"] for c in candidates],
        normalized_metadata=[{"corpus_sha256": before, "snapshot_sha256": snapshot_hash,
            "known_occurrence_ids": known,
            "reuse_artifact": {"path": "local-reuse/reuse.json", "sha256": sha256(stage / "local-reuse" / "reuse.json")}}],
        reason="Recording correspondences preserve their existing immutable IDs; non-match does not establish a different performance or composition")
    return receipt, candidates


def reference_comparison(context, reference, number, stream_index=None, start=None, end=None):
    from ..common import probe_source, select_stream
    from ..reuse.workflow import Media, run_scan
    from ..reuse.media import extract
    from ..audio_tracks import _spectral_rows, CHROMA_FIELDS
    from ..musical_relations import ordered_chroma_candidate
    import numpy as np
    stage, scope = context["stage"], context["source"]
    reference = Path(reference).resolve()
    probe = probe_source(reference)
    stream = select_stream(probe, "audio", stream_index)
    ref_span = [0. if start is None else float(start), min(120., probe["duration_seconds"]) if end is None else float(end)]
    from ..event_contracts import span
    span(ref_span, [0., probe["duration_seconds"]])
    if ref_span[1] - ref_span[0] > 120:
        raise AVError("Reference comparison must be bounded to 120 seconds")
    reference_row = {"id": "music-reference", "source": str(reference), "source_sha256": probe["sha256"],
        "audio_stream": stream["index"], "start_s": ref_span[0], "end_s": ref_span[1], "unit_kind": "window", "role": "reference"}
    config = stage / "execution" / ("reference-%d.json" % number)
    write_json(config, {"schema": "ave.reuse.config.v1", "occurrences": [_query_row(context), reference_row]})
    output = stage / ("reference-%d" % number)
    with tempfile.TemporaryDirectory(prefix="ave-music-reference-") as temp:
        temp = Path(temp)
        run_scan(config, output, cache_dir=context.get("reuse_cache") or temp / "cache", database=temp / "reuse.sqlite",
                 compare_ids=["music-query", "music-reference"], transforms=context.get("transforms", False), preview_count=0)
        report = read_json(output / "reuse.json")
        hits = _correspondences(report, scope["sha256"], scope["audio_stream"], scope["interval_seconds"])
        context["correspondences"].extend(hits)
        # Reuse the existing source-clock chroma producer and ordered comparator, no second DSP implementation.
        media = Media(context.get("reuse_cache") or temp / "cache")
        features = []
        for path, index, bounds in ((context["probe"]["path"], scope["audio_stream"], scope["interval_seconds"]),
                                   (reference, stream["index"], ref_span)):
            _, selected, decoded = media.source(path, index)
            samples, part = extract(decoded, *bounds)
            part = {**part, "segment_id": "music-query-native-segment"}
            rate = int(selected["sample_rate"])
            rows = [row for kind, row, _ in _spectral_rows(samples[:, 0], rate, max(32, round(rate * .08)),
                max(16, round(rate * .04)), part, 0, -60., music=True) if kind == "chroma"]
            matrix = np.array([[row.get(k) or 0. for k in CHROMA_FIELDS[7:-1]] for row in rows], dtype=float)
            # Bound cost while preserving temporal order; the exact sampled indices are retained.
            indices = np.unique(np.linspace(0, len(matrix) - 1, min(600, len(matrix))).astype(int)) if len(matrix) else []
            features.append((matrix[indices], list(map(int, indices))))
        chroma = ordered_chroma_candidate(features[0][0], features[1][0]) if all(len(f[0]) for f in features) else {"status": "UNRESOLVED"}
    result = {"reference_id": "music-reference-" + canonical_digest([probe["sha256"], stream["index"], ref_span])[:24],
        "reference_source_sha256": probe["sha256"], "reference_audio_stream": stream["index"], "reference_interval_seconds": ref_span,
        "reuse_artifact": {"path": output.relative_to(stage).as_posix() + "/reuse.json", "sha256": sha256(output / "reuse.json")},
        "match_ids": [h["match_id"] for h in hits], "correspondence_ids": [h["correspondence_id"] for h in hits],
        "ordered_chroma": chroma, "chroma_sample_indices": [f[1] for f in features],
        "relation": "EDITED_RECORDING_REUSE" if hits else "UNRESOLVED", "status": "CANDIDATE" if hits else "UNRESOLVED",
        "recording_identity_effect": "NONE", "perceptual_review": "NOT_PERFORMED",
        "reason": "Reuse and ordered chroma generate relation candidates. Different realization, arrangement or composition needs separate adjudication; a non-match proves none of them."}
    return result, probe
