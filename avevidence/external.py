"""Admission of externally prepared stream-copy review clips.

Packet identity can establish a source/derivative byte relationship for selected
encoded streams. It does not establish perceptual equivalence, complete AV
synchronization, or literary interpretation.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from pathlib import Path
import shutil
import json

from .mapping import (EPS, timing_basis, validated_segments, union_intervals,
                      missing_intervals, contiguous_offset_spread)

from .common import (AVError, finite, finish_run, interval, output_transaction,
                     probe_source, run, select_stream, sha256, write_json)


def _time_base(stream):
    try:
        value = Fraction(stream["time_base"])
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise AVError("Selected stream has no valid time base") from exc
    if value <= 0:
        raise AVError("Selected stream has a nonpositive time base")
    return value


def _packet_rows(source, stream, folder, stem):
    """Hash packet payloads without retaining packet bytes in the run."""
    destination = Path(folder) / f"{stem}.packets.json"
    command = ["ffprobe", "-v", "error", "-select_streams", str(stream["index"]),
               "-show_packets", "-show_data_hash", "sha256", "-show_entries",
               "packet=stream_index,pts,dts,duration,pts_time,dts_time,duration_time,size,flags,data_hash:packet_side_data=side_data_type,skip_samples,discard_padding",
               "-of", "json", source["path"]]
    run(command, stdout_file=destination, timeout=600)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    rows = []
    tb = _time_base(stream)
    for fields in payload.get("packets", []):
        if fields.get("stream_index") not in (None, stream["index"]):
            raise AVError("Packet scan returned an unexpected stream")
        digest = fields.get("data_hash", "")
        if not digest.startswith("SHA256:") or len(digest) != 71:
            raise AVError("Packet payload hash is unavailable")
        if fields.get("pts") in (None, "N/A"):
            raise AVError("Packet has no presentation timestamp")
        pts = float(int(fields["pts"]) * tb)
        duration = float(int(fields["duration"]) * tb) if fields.get("duration") not in (None, "N/A") else 0.0
        rows.append({"pts": pts, "pts_ticks": int(fields["pts"]),
                     "dts_ticks": fields.get("dts"), "duration": duration,
                     "hash": digest[7:].lower(), "size_bytes": int(fields.get("size", 0) or 0),
                     "flags": fields.get("flags", ""), "side_data": fields.get("side_data_list", [])})
    destination.unlink()
    if not rows:
        raise AVError("Selected stream contains no hashable packets")
    tb = _time_base(stream)
    tolerance = max(0.000002, abs(float(tb)) * 1.01)
    endpoint = None
    for bound in source.get("stream_timeline_bounds", []):
        if bound.get("stream_index") == stream.get("index") and bound.get("end_seconds") is not None:
            endpoint = finite(bound["end_seconds"], "stream endpoint")
            break
    methods = Counter()
    ordered_pts = sorted(set(r["pts"] for r in rows))
    successors = dict(zip(ordered_pts, ordered_pts[1:]))
    for i, row in enumerate(rows):
        if row["duration"] > 0:
            method = "explicit_packet_duration"
        elif row["pts"] in successors:
            row["duration"] = successors[row["pts"]] - row["pts"]
            method = "next_packet_pts_delta"
        elif endpoint is not None and endpoint > row["pts"]:
            row["duration"] = endpoint - row["pts"]
            method = "verified_stream_endpoint"
        elif len(rows) > 1 and row["pts"] > rows[-2]["pts"]:
            row["duration"] = row["pts"] - rows[-2]["pts"]
            method = "previous_packet_pts_delta_estimate"
        else:
            raise AVError("Packet extent is unavailable")
        if row["duration"] <= 0:
            raise AVError("Packet extent is nonpositive")
        row["extent_method"] = method
        row["canonical_start"] = row["pts"] - source["origin_seconds"]
        row["canonical_end"] = row["canonical_start"] + row["duration"]
        methods[method] += 1
    return rows, tolerance, {"method_counts": dict(sorted(methods.items())),
                             "policy": "explicit packet duration, next PTS, verified endpoint, final previous-delta estimate"}


def _matches(parent, candidate):
    target = [row["hash"] for row in candidate]
    first = target[0]
    possible = [i for i, row in enumerate(parent) if row["hash"] == first and i + len(target) <= len(parent)]
    return [i for i in possible if [row["hash"] for row in parent[i:i+len(target)]] == target]


def _segments(parent, candidate, start, tolerance):
    segments, offsets = [], []
    pairs = sorted(zip(parent[start:start+len(candidate)], candidate),
                   key=lambda pair: (pair[0]["canonical_start"], pair[1]["canonical_start"]))
    for p, c in pairs:
        offset = p["canonical_start"] - c["canonical_start"]
        offsets.append(offset)
        item = {"parent_start_seconds": p["canonical_start"],
                "parent_end_seconds": p["canonical_end"],
                "derivative_start_seconds": c["canonical_start"],
                "derivative_end_seconds": c["canonical_start"] + p["duration"]}
        estimated = "estimate" in p["extent_method"] or "estimate" in c["extent_method"]
        item["extent_confidence"] = "ESTIMATED" if estimated else "SOURCE_DERIVED"
        if segments:
            prior = segments[-1]
            prior_offset = prior["parent_start_seconds"]-prior["derivative_start_seconds"]
            if (abs(prior["parent_end_seconds"]-item["parent_start_seconds"]) <= EPS
                and abs(offset-prior_offset) <= tolerance
                and prior["extent_confidence"] == item["extent_confidence"]):
                prior["parent_end_seconds"] = item["parent_end_seconds"]
                prior["derivative_end_seconds"] = item["parent_end_seconds"]-prior_offset
                continue
        segments.append(item)
    return segments, max(offsets)-min(offsets) if offsets else 0.0


def _playable_audio(source, stream):
    # Decoder sample extents incorporate priming/discard behavior. They are not
    # a claim of subjective listening or a decoded-sample identity comparison.
    from .clips import _windows
    windows, tolerance, adjustment, extent = _windows(source, stream)
    bound = next((b for b in source.get("stream_timeline_bounds", [])
                  if b.get("stream_index") == stream["index"]), {})
    declared = bound.get("basis") in {"container_stream_presentation_interval", "intrinsic_audio_sample_count"}
    lower = max(0.0, bound.get("canonical_start_seconds", 0) or 0) if declared else 0.0
    upper = bound.get("canonical_end_seconds") if declared else source.get("duration_seconds")
    playable = []
    for a,b in windows:
        a, b = max(lower, a-source["origin_seconds"]), b-source["origin_seconds"]
        if upper is not None:
            b = min(b, upper)
        if b > a:
            playable.append([a,b])
    return union_intervals(playable), {"basis": "decoded_sample_extents_intersect_nonnegative_source_clock_and_declared_presentation_bounds",
        "declared_presentation_bounds_applied": declared, "decoder_extent_derivation": extent,
        "timestamp_quantization_tolerance_seconds": tolerance, "maximum_timestamp_adjustment_seconds": adjustment}


def _intersect_playable(segments, parent_windows, derivative_windows):
    result = []
    for s in segments:
        pa,pb,da = s["parent_start_seconds"],s["parent_end_seconds"],s["derivative_start_seconds"]
        offset = pa-da
        for x,y in parent_windows:
            for u,v in derivative_windows:
                lo,hi = max(pa,x,u+offset),min(pb,y,v+offset)
                if hi-lo > EPS:
                    result.append(dict(s, parent_start_seconds=lo, parent_end_seconds=hi,
                                       derivative_start_seconds=lo-offset, derivative_end_seconds=hi-offset))
    return result


def _modality(parent_source, clip_source, parent_stream, clip_stream, artifact_path,
              artifact_hash, claimed, work, label):
    parent_rows, parent_tolerance, parent_extent = _packet_rows(parent_source, parent_stream, work, f"parent-{label}")
    clip_rows, clip_tolerance, clip_extent = _packet_rows(clip_source, clip_stream, work, f"clip-{label}")
    basis = timing_basis(parent_stream, clip_stream)
    tolerance = basis["endpoint_tolerance_seconds"]
    result = {"modality": "motion" if label == "video" else "audio",
              "parent_stream_index": parent_stream["index"],
              "derivative_stream_index": clip_stream["index"],
              "parent_codec": parent_stream.get("codec_name"),
              "derivative_codec": clip_stream.get("codec_name"),
              "parent_packet_count": len(parent_rows), "derivative_packet_count": len(clip_rows),
              "timestamp_tolerance_seconds": tolerance,
              "parent_extent_derivation": parent_extent,
              "derivative_extent_derivation": clip_extent,
              "claimed_parent_interval_seconds": list(claimed),
              "packet_identity_scope": "Encoded packet payload SHA-256 sequence; container metadata and packet timestamps are evaluated separately.",
              "perceptual_equivalence": "NOT_ESTABLISHED"}
    if parent_stream.get("codec_name") != clip_stream.get("codec_name"):
        result.update(status="DIFFERENT_CODEC", matches=[])
        return result, None
    matches = _matches(parent_rows, clip_rows)
    result["matches"] = matches
    if not matches:
        result["status"] = "INDETERMINATE_NO_PACKET_SUBSEQUENCE"
        return result, None
    if len(matches) != 1:
        result["status"] = "INDETERMINATE_AMBIGUOUS_PACKET_SUBSEQUENCE"
        return result, None
    index = matches[0]
    observed = [(p["canonical_start"], p["canonical_end"],
                 c["canonical_start"], c["canonical_end"])
                for p, c in zip(parent_rows[index:index+len(clip_rows)], clip_rows)]
    offsets = [pa-da for pa, _, da, _ in observed]
    spread = max(offsets)-min(offsets)
    contiguous_spread = contiguous_offset_spread(observed)
    duration_disagreement = max(abs((pb-pa)-(db-da)) for pa, pb, da, db in observed)
    result["observed_packet_timing"] = {
        "maximum_contiguous_onset_offset_spread_seconds": contiguous_spread,
        "maximum_packet_duration_disagreement_seconds": duration_disagreement,
        "onset_offset_budget_seconds": tolerance,
        "video_duration_budget_seconds": 2*tolerance,
        "policy": "Check observed packet onsets within each connected parent interval before normalization. Video packet durations must also fit the two-endpoint clock budget. Audio packet padding remains subject to decoded playable coverage."}
    result["encoded_packet_spans_seconds"] = {
        "parent": [min(p["canonical_start"] for p in parent_rows[index:index+len(clip_rows)]),
                   max(p["canonical_end"] for p in parent_rows[index:index+len(clip_rows)])],
        "derivative": [min(c["canonical_start"] for c in clip_rows), max(c["canonical_end"] for c in clip_rows)]}
    result["normalization_policy"] = ("Validate observed timing before anchoring groups at matching packet onsets and using parent-clock extents; "
        "bound contiguous onset offset spread by the derived clock budget, then intersect playable coverage; "
        "packet timestamps/side data are preserved separately. Parent packet gaps are never bridged.")
    result["packet_side_data"] = {
        "parent": [{"packet_index": i, "canonical_start": r["canonical_start"], "side_data": r.get("side_data", [])}
                   for i,r in enumerate(parent_rows) if r.get("side_data") or r["canonical_start"] < 0],
        "derivative": [{"packet_index": i, "canonical_start": r["canonical_start"], "side_data": r.get("side_data", [])}
                       for i,r in enumerate(clip_rows) if r.get("side_data") or r["canonical_start"] < 0]}
    result.update(status="VERIFIED_PACKET_SUBSEQUENCE", matched_parent_packet_index=index,
                  first_parent_packet_keyframe="K" in parent_rows[index]["flags"],
                  maximum_mapping_offset_spread_seconds=spread, timing_basis=basis)
    try:
        if contiguous_spread > tolerance + EPS:
            raise AVError("Retimed packet onsets exceed the derived clock budget within a contiguous parent interval")
        if label == "video" and duration_disagreement > 2*tolerance + EPS:
            raise AVError("Retimed video packet durations exceed the derived two-endpoint clock budget")
        segments, _ = _segments(parent_rows, clip_rows, index, tolerance)
        result["packet_anchored_normalized_segments"] = [dict(s) for s in segments]
        if label == "audio":
            p_windows, p_basis = _playable_audio(parent_source, parent_stream)
            c_windows, c_basis = _playable_audio(clip_source, clip_stream)
            result["playable_audio"] = {"parent_intervals_seconds": p_windows,
                "derivative_intervals_seconds": c_windows, "parent_basis": p_basis, "derivative_basis": c_basis}
            segments = _intersect_playable(segments, p_windows, c_windows)
        else:
            # Nonnegative canonical review clock; encoded pre-roll remains above.
            p_end = parent_source.get("duration_seconds")
            c_end = clip_source.get("duration_seconds")
            def packet_union(rows, upper):
                return union_intervals([[max(0.0, q["canonical_start"]),
                    min(q["canonical_end"], upper) if upper is not None else q["canonical_end"]] for q in rows])
            from .clips import _windows
            windows, clip_tick, adjustment, extent = _windows(clip_source, clip_stream)
            decoded_windows = union_intervals([[max(0.0,x-clip_source["origin_seconds"]),
                min(y-clip_source["origin_seconds"],c_end) if c_end is not None else y-clip_source["origin_seconds"]]
                for x,y in windows])
            result["decoded_video_coverage"] = {"derivative_intervals_seconds":decoded_windows,
                "extent_derivation":extent, "timestamp_quantization_tolerance_seconds":clip_tick,
                "maximum_timestamp_adjustment_seconds":adjustment,
                "policy":"Decoded frame extents join only within one derivative clock tick; parent packet gaps remain explicit."}
            segments = _intersect_playable(segments,
                packet_union(parent_rows[index:index+len(clip_rows)], p_end), decoded_windows)
            for segment in segments:
                if any(max(segment["derivative_start_seconds"],x-clip_source["origin_seconds"]) <
                       min(segment["derivative_end_seconds"],y-clip_source["origin_seconds"])
                       for x,y in extent.get("estimated_intervals_absolute_seconds", [])):
                    segment["extent_confidence"] = "ESTIMATED"
        mapping = {"parent_source_sha256": parent_source["sha256"],
                   "parent_stream_index": parent_stream["index"],
                   "derivative_stream_index": clip_stream["index"],
                   "modality": result["modality"], "artifact_path": artifact_path,
                   "artifact_sha256": artifact_hash, "segments": segments,
                   "timing_basis": basis, "timestamp_tolerance_seconds": tolerance,
                   "mapping_semantics": "rate_one_normalized_within_packet_clock_quantization",
                   "verification": "UNIQUE_ENCODED_PACKET_SHA256_SUBSEQUENCE_WITH_REVIEWABLE_COVERAGE",
                   "claimed_parent_interval_seconds": list(claimed),
                   "estimated_parent_intervals_seconds": [[s["parent_start_seconds"],s["parent_end_seconds"]]
                       for s in segments if s.get("extent_confidence") == "ESTIMATED" ]}
        validated_segments(mapping, parent_source.get("duration_seconds"), parent_stream, clip_stream)
    except AVError as exc:
        result.update(review_ready=False, review_readiness_status="INDETERMINATE_REVIEW_MAPPING",
                      review_limitation=str(exc), claimed_interval_covered=False,
                      uncovered_parent_intervals_seconds=[list(claimed)])
        return result, None
    covered = union_intervals([[s["parent_start_seconds"],s["parent_end_seconds"]] for s in segments])
    gaps = missing_intervals([list(claimed)], covered)
    actual_start, actual_end = covered[0][0], covered[-1][1]
    mapping["claimed_interval_covered"] = not gaps
    result.update(review_ready=True, review_readiness_status="READY_WITH_ESTIMATED_EXTENT" if mapping["estimated_parent_intervals_seconds"] else "READY",
                  actual_parent_interval_seconds=[actual_start,actual_end],
                  actual_derivative_interval_seconds=[min(s["derivative_start_seconds"] for s in segments),max(s["derivative_end_seconds"] for s in segments)],
                  covered_parent_intervals_seconds=covered, uncovered_parent_intervals_seconds=gaps,
                  claimed_interval_covered=not gaps, segment_count=len(segments),
                  boundary_delta_seconds={"start":actual_start-claimed[0],"end":actual_end-claimed[1]})

    return result, mapping


def admit_external_clip(parent, clip, output, *, parent_start, parent_end,
                         parent_video_stream=None, clip_video_stream=None,
                         parent_audio_stream=None, clip_audio_stream=None):
    """Copy and map an externally created clip by unique packet subsequences."""
    parent_source = probe_source(parent)
    clip_source = probe_source(clip)
    if parent_source["sha256"] == clip_source["sha256"]:
        raise AVError("External clip must be a distinct physical file")
    claimed = interval(parent_start, parent_end, parent_source["duration_seconds"])
    pairs = []
    for kind, p_index, c_index in (("video", parent_video_stream, clip_video_stream),
                                    ("audio", parent_audio_stream, clip_audio_stream)):
        p_stream = select_stream(parent_source, kind, p_index, required=False)
        c_stream = select_stream(clip_source, kind, c_index, required=False)
        if (p_index is not None or c_index is not None) and (p_stream is None or c_stream is None):
            raise AVError(f"Both parent and clip require the selected {kind} stream")
        if p_stream is not None and c_stream is not None:
            pairs.append((kind, p_stream, c_stream))
    if not pairs:
        raise AVError("No common selected audio or video modality is available")
    with output_transaction(output, inputs=[parent, clip]) as stage:
        suffix = Path(clip).suffix.lower()
        if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
            suffix = ".bin"
        artifact = stage / ("external_clip" + suffix)
        shutil.copyfile(clip_source["path"], artifact)
        if sha256(artifact) != clip_source["sha256"]:
            raise AVError("External clip changed while copying into the run")
        artifact_hash = sha256(artifact)
        work = stage / ".packet-working"
        work.mkdir()
        results, mappings = [], []
        try:
            for kind, parent_stream, clip_stream in pairs:
                value, mapping = _modality(parent_source, clip_source, parent_stream, clip_stream,
                                           artifact.name, artifact_hash, claimed, work, kind)
                results.append(value)
                if mapping is not None:
                    mappings.append(mapping)
        finally:
            for item in work.iterdir():
                if not item.is_file() or item.is_symlink():
                    raise AVError("Unexpected object in packet scratch directory")
                item.unlink()
            work.rmdir()
        verified = [x for x in results if x["status"] == "VERIFIED_PACKET_SUBSEQUENCE"]
        claim_covered = [x for x in verified if x.get("claimed_interval_covered") and x.get("review_ready")]
        offsets = []
        for item in verified:
            if item.get("actual_parent_interval_seconds") and item.get("actual_derivative_interval_seconds"):
                offsets.append(item["actual_parent_interval_seconds"][0] - item["actual_derivative_interval_seconds"][0])
        cross = "NOT_APPLICABLE"
        if len(offsets) > 1:
            tolerance = max(x["timestamp_tolerance_seconds"] for x in verified)
            cross = "CONSISTENT_WITHIN_TOLERANCE" if max(offsets)-min(offsets) <= tolerance else "DIFFERENT"
        if len(verified) == len(results) and len(claim_covered) == len(results):
            overall = ("VERIFIED_WITH_ESTIMATED_FINAL_EXTENT" if any(x.get("review_readiness_status") == "READY_WITH_ESTIMATED_EXTENT" for x in verified)
                       else "VERIFIED_ALL_SELECTED_MODALITIES")
        elif len(verified) == len(results) and any(not x.get("review_ready") for x in verified):
            overall = "PACKET_IDENTITY_VERIFIED_REVIEW_MAPPING_INDETERMINATE"
        elif len(verified) == len(results):
            overall = "PACKET_IDENTITY_VERIFIED_CLAIM_NOT_COVERED"
        elif verified:
            overall = "PARTIAL_VERIFICATION"
        else:
            overall = "INDETERMINATE"
        data = {"schema": "ave.external_clip.v2", "parent_source_sha256": parent_source["sha256"],
                "external_clip_source_sha256": clip_source["sha256"],
                "artifact_path": artifact.name, "artifact_sha256": artifact_hash,
                "claimed_parent_interval_seconds": list(claimed), "modalities": results,
                "review_mappings": mappings, "overall_status": overall,
                "cross_modal_offset_consistency": cross,
                "review_status": "NOT_REVIEWED",
                "scope": "Packet identity and source/derivative timing only; not complete AV or perceptual equivalence.",
                "success_policy": "Unqualified success requires unique packet identity, review-contract-compatible mappings, full interval-union coverage and no estimated extent. Packet identity alone is insufficient."}
        write_json(stage / "external_clip.json", data)
        return finish_run(stage, "admit_external_clip", [parent_source, clip_source],
                          {"parent_start": claimed[0], "parent_end": claimed[1],
                           "parent_video_stream": parent_video_stream, "clip_video_stream": clip_video_stream,
                           "parent_audio_stream": parent_audio_stream, "clip_audio_stream": clip_audio_stream},
                          {"result_file": "external_clip.json", "review_mappings": mappings,
                           "overall_status": overall, "artifact_path": artifact.name})
