"""Portable command-line interface; all media outputs use new run directories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .common import AVError, environment, finish_run, output_transaction, probe_source, write_json


def parser():
    p = argparse.ArgumentParser(prog="ave", description="Prepare verifiable audiovisual evidence. Computation is not listening.")
    p.add_argument("--version", action="version", version=__version__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Report software dependencies; does not verify perception")
    reuse = commands.add_parser("reuse", help="Find recording reuse, preserve fragment provenance and audit performance counts")
    reuse_commands = reuse.add_subparsers(dest="reuse_command", required=True)
    for name in ("scan", "compare"):
        r = reuse_commands.add_parser(name, help="Scan configured windows or compare two configured occurrence IDs/labels")
        r.add_argument("config", help="Reuse JSON config or directory containing verified performance runs")
        if name == "compare":
            r.add_argument("a"); r.add_argument("b")
        r.add_argument("output", help="New immutable report directory")
        r.add_argument("--database", required=True, help="Mutable reuse ledger outside report directories")
        r.add_argument("--cache-dir", required=True)
        r.add_argument("--transforms", action="store_true", help="Enable bounded spectral pitch/time hypotheses; these require identity review")
        r.add_argument("--self-scan", action="store_true", help="Search internal repetitions as well as cross-window matches")
        r.add_argument("--max-candidates", type=int, default=20)
        r.add_argument("--max-window-s", type=float, default=300.)
        r.add_argument("--min-fragment-s", type=float, default=.24)
        r.add_argument("--timestamp-tolerance-ms", type=float)
        r.add_argument("--preview-count", type=int, default=6)
    for name in ("graph", "audit"):
        r = reuse_commands.add_parser(name, help="Export a reproducible graph or measurement-specific baseline audit")
        r.add_argument("database"); r.add_argument("output")
        r.add_argument("--revision", type=int); r.add_argument("--speaker")
        if name == "audit": r.add_argument("--baseline", help="Existing occurrence rows in JSON, CSV, TSV or optional Parquet")
    r = reuse_commands.add_parser("decide", help="Append a reversible identity decision; reset restores detector policy")
    r.add_argument("database"); r.add_argument("match_id")
    r.add_argument("--decision", required=True, choices=["same_performance", "audio_only", "distinct", "unresolved", "reset"])
    r.add_argument("--reason", required=True); r.add_argument("--reviewer", required=True)
    r = reuse_commands.add_parser("annotate", help="Record revised speaker, vocal attribution, context or quality without changing evidence")
    r.add_argument("database"); r.add_argument("occurrence_id"); r.add_argument("changes", help="JSON object of attribution changes")
    r.add_argument("--reason", required=True); r.add_argument("--reviewer", required=True)
    r = reuse_commands.add_parser("promote", help="Choose a better full-coverage witness without changing performance identity")
    r.add_argument("database"); r.add_argument("performance_id"); r.add_argument("occurrence_id")
    r.add_argument("--feature", choices=["default", "pitch", "level", "timbre", "timing"], default="default")
    r.add_argument("--reason", required=True); r.add_argument("--reviewer", required=True)
    d = commands.add_parser("probe", help="Record source identity, streams and source clock")
    d.add_argument("input"); d.add_argument("output")
    d = commands.add_parser("inventory", help="Admit logical sources and materializations from JSON")
    d.add_argument("config"); d.add_argument("output")
    d = commands.add_parser("extract-audio", help="Extract native decoded audio with source-time mapping")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--audio-stream", type=int)
    d.add_argument("--storage-profile", choices=["practical", "forensic", "verified-flac"])
    d.add_argument("--format", choices=["wav", "flac", "preserve"],
                   help="Compatibility alias: wav=forensic, preserve=practical, flac=verified-flac")
    d = commands.add_parser("audio-metrics", help="Bounded or whole-stream loudness measurements")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--audio-stream", type=int); d.add_argument("--start"); d.add_argument("--end")
    d = commands.add_parser("audio-track", help="Source-timed acoustic, spectral, stereo and optional music tracks")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--audio-stream", type=int); d.add_argument("--start"); d.add_argument("--end")
    d.add_argument("--profile", choices=["general", "speech", "music"], default="general")
    d.add_argument("--window-ms", type=float); d.add_argument("--hop-ms", type=float)
    d.add_argument("--spectral-scales-ms", type=float, nargs="*", default=[20., 80.],
                   help="Additional spectral window sizes; empty list disables extra scales")
    d.add_argument("--stereo", choices=["auto", "off", "channels-0-1"], default="auto")
    d.add_argument("--loudness", choices=["auto", "require", "off"], default="auto")
    d.add_argument("--no-render", action="store_true")
    d.add_argument("--energy-threshold-dbfs", type=float, default=-45.)
    d.add_argument("--max-duration", type=float, default=300.)
    d.add_argument("--max-frames", type=int, default=250000)
    d = commands.add_parser("render-audio", help="Render verified audio tracks without decoding again")
    d.add_argument("track_run"); d.add_argument("output")
    d = commands.add_parser("performance", help="Cache pitch/level tracks and optionally align Japanese text locally")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--cache-dir", required=True); d.add_argument("--audio-stream", type=int)
    texts = d.add_mutually_exclusive_group()
    texts.add_argument("--subtitle-stream", type=int)
    texts.add_argument("--subtitle-run"); texts.add_argument("--transcript")
    d.add_argument("--model-dir", help="Local Qwen3-ForcedAligner-0.6B weights; omit for contours only")
    d.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    d.add_argument("--channel", default="mean", help="Arithmetic channel mean or zero-based channel index")
    d.add_argument("--hop-ms", type=float, default=10.)
    d.add_argument("--window-ms", type=float, default=40.)
    d.add_argument("--pitch-floor", type=float, default=75.)
    d.add_argument("--pitch-ceiling", type=float, default=600.)
    d.add_argument("--max-duration", type=float, default=7200.)
    d.add_argument("--max-alignment-window", type=float, default=30.)
    d.add_argument("--alignment-padding", type=float, default=.2)
    d.add_argument("--timestamp-tolerance-ms", type=float,
                   help="Explicit recorded timestamp adjustment limit, at most 10 ms; default is source granularity")
    d.add_argument("--coverage-mode", choices=["strict", "decoded"], default="strict")
    d.add_argument("--coverage-end", type=float, help="Explicit endpoint for bounded native-decoded coverage; never a replacement container duration")
    d.add_argument("--max-output-bytes", type=int, default=2147483648)
    d.add_argument("--decode-timeout-s", type=float, default=180.)
    d = commands.add_parser("performance-index", help="Index a verified performance run in local SQLite")
    d.add_argument("run_dir"); d.add_argument("database"); d.add_argument("--label")
    d = commands.add_parser("performance-search", help="Find Japanese dialogue and measured cue candidates")
    d.add_argument("database"); d.add_argument("query", nargs="?", default="")
    d.add_argument("--source"); d.add_argument("--speaker"); d.add_argument("--status")
    d.add_argument("--min-pitch", type=float); d.add_argument("--min-rms", type=float)
    d.add_argument("--limit", type=int, default=25)
    d.add_argument("--reuse-database", help="Attach performance provenance from a reuse ledger")
    d.add_argument("--reuse-revision", type=int, help="Use a recorded reuse revision")
    d = commands.add_parser("performance-review", help="Build a source-bound listening worksheet from performance runs")
    d.add_argument("config"); d.add_argument("output")
    d = commands.add_parser("performance-review-import", help="Validate declared listening notes without closing analytical debts")
    d.add_argument("review_run"); d.add_argument("notes"); d.add_argument("output")
    d = commands.add_parser("performance-review-serve", help="Serve a verified review on this computer with media seeking")
    d.add_argument("review_run"); d.add_argument("--port", type=int, default=0)
    d = commands.add_parser("performance-video", help="Bind a browser MP4 packet copy to measured episode audio")
    d.add_argument("input"); d.add_argument("performance_run"); d.add_argument("output")
    d.add_argument("--video-stream", type=int); d.add_argument("--audio-stream", type=int)
    d.add_argument("--audio-clock-tolerance-ms", type=float, default=1.1,
                   help="Explicit bound for original decoded audio PTS versus witness sample clock; default 1.1 ms, maximum 20 ms")
    d = commands.add_parser("cue-features", help="Features for source-bound subtitle windows")
    d.add_argument("audio"); d.add_argument("dialogue_csv"); d.add_argument("output")
    d.add_argument("--audio-stream", type=int)
    d.add_argument("--pitch", action="store_true")
    d.add_argument("--confirm-isolated-speech", action="store_true")
    d.add_argument("--profile", choices=["speech", "music"], default="speech")
    d = commands.add_parser("compare-audio", help="Compare native decoded audio, layout and clocks separately")
    d.add_argument("a"); d.add_argument("b"); d.add_argument("output")
    d.add_argument("--a-stream", type=int); d.add_argument("--b-stream", type=int)
    d = commands.add_parser("frame-index", help="Create a reusable verified original-PTS video frame index")
    d.add_argument("input"); d.add_argument("output"); d.add_argument("--video-stream", type=int)
    d = commands.add_parser("frames", help="Extract frames with original source PTS")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--mode", choices=["interval", "dense", "source", "exact", "shots"], default="interval")
    d.add_argument("--start"); d.add_argument("--end")
    d.add_argument("--interval", type=float, default=2.0); d.add_argument("--fps", type=float, default=10)
    d.add_argument("--timestamps", nargs="+"); d.add_argument("--threshold", type=float, default=.35)
    d.add_argument("--width", type=int, default=1280); d.add_argument("--video-stream", type=int)
    d.add_argument("--frame-index-run")
    d.add_argument("--seek-preroll", type=float, default=2.0)
    d.add_argument("--no-input-seek", action="store_true")
    d = commands.add_parser("contacts", help="Create contact sheets from verified current-run artifacts")
    d.add_argument("frame_run"); d.add_argument("output")
    d.add_argument("--columns", type=int, default=4); d.add_argument("--rows", type=int, default=4)
    d.add_argument("--thumb-width", type=int, default=360)
    d = commands.add_parser("subtitles", help="Parse strict UTF text or extract a selected subtitle stream")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--media", action="store_true"); d.add_argument("--subtitle-stream", type=int)
    d.add_argument("--source-media"); d.add_argument("--offset", type=float, default=0)
    d.add_argument("--normalize", action="store_true", help="Preserve originals and record blank/invalid/explicit exclusions in a new ledger")
    d.add_argument("--exclude-cue-ids", type=int, nargs="*", default=[])
    d = commands.add_parser("timeline", help="Join source-bound captions, frames and optional audio features")
    d.add_argument("subtitle_run"); d.add_argument("frame_run"); d.add_argument("output")
    d.add_argument("--audio-run"); d.add_argument("--max-distance", type=float, default=2)
    for name in ("clip-audio", "clip-av"):
        d = commands.add_parser(name, help="Create a bounded review derivative with checked source mapping")
        d.add_argument("input"); d.add_argument("output")
        d.add_argument("--start", required=True); d.add_argument("--end", required=True)
        d.add_argument("--pad", type=float, default=0)
        d.add_argument("--audio-stream", type=int)
        if name == "clip-av": d.add_argument("--video-stream", type=int)
    d = commands.add_parser("admit-external-clip", help="Verify and admit an existing LosslessCut/stream-copy clip")
    d.add_argument("parent"); d.add_argument("clip"); d.add_argument("output")
    d.add_argument("--parent-start", required=True); d.add_argument("--parent-end", required=True)
    d.add_argument("--parent-video-stream", type=int); d.add_argument("--clip-video-stream", type=int)
    d.add_argument("--parent-audio-stream", type=int); d.add_argument("--clip-audio-stream", type=int)
    d = commands.add_parser("bundle", help="Prepare a source, audio, frames, captions and navigation bundle")
    d.add_argument("input"); d.add_argument("output")
    d.add_argument("--subtitles"); d.add_argument("--subtitle-stream", type=int)
    d.add_argument("--audio-stream", type=int); d.add_argument("--video-stream", type=int)
    d.add_argument("--interval", type=float, default=2); d.add_argument("--width", type=int, default=1280)
    d.add_argument("--shots", action="store_true"); d.add_argument("--cue-features", action="store_true")
    d.add_argument("--audio-storage", choices=["practical", "forensic", "verified-flac"], default="practical")
    d = commands.add_parser("verify", help="Verify artifact hashes and run structure")
    d.add_argument("run_dir"); d.add_argument("--verify-sources", action="store_true")
    d = commands.add_parser("review-templates", help="Write invalid-until-completed capability and review examples")
    d.add_argument("output")
    d = commands.add_parser("review-check", help="Validate review declarations; does not prove actual perception")
    d.add_argument("records"); d.add_argument("inventory_or_run"); d.add_argument("capabilities"); d.add_argument("output")
    d = commands.add_parser("pack", help="Create a deterministic allowlisted evidence ZIP")
    d.add_argument("run_dir"); d.add_argument("archive")
    d = commands.add_parser("verify-archive", help="Check archive membership, CRC and hashes without extracting")
    d.add_argument("archive")
    for name in ("preflight", "deep-read"):
        d = commands.add_parser(name, help="Inspect independent modalities and capabilities" if name == "preflight" else "Prepare claim-directed evidence and a reasoning-analyst handoff")
        d.add_argument("input"); d.add_argument("output")
        d.add_argument("--audio-stream", type=int); d.add_argument("--video-stream", type=int)
        d.add_argument("--external-audio"); d.add_argument("--external-audio-offset", type=float)
        d.add_argument("--transcript"); d.add_argument("--screenshots-manifest")
        d.add_argument("--direct-receipt"); d.add_argument("--observer-receipt")
        d.add_argument("--timestamp-tolerance-ms", type=float)
        if name == "preflight":
            d.add_argument("--audio-class", default="unknown")
            d.add_argument("--probe-seconds", type=float, default=2.)
        else:
            d.add_argument("--method", help="Governing UTF-8 analytical method; explicit file always overrides the built-in fallback")
            d.add_argument("--claims"); d.add_argument("--contour-run")
            d.add_argument("--episode-isolated", action="store_true", help="Limit semantic evidence to the supplied source; does not change --method")
            d.add_argument("--auditory-observer", choices=["human", "mock", "none"], default="human")
            d.add_argument("--evidence-query-planning", action="store_true", help="Planning is automatic when --claims is supplied")
            d.add_argument("--execute-local-queries", action="store_true")
    for name in ("evidence-plan", "audit"):
        d = commands.add_parser(name, help="Validate atomic claims and plan minimum evidence escalation")
        d.add_argument("config"); d.add_argument("output"); d.add_argument("--capabilities")
        d.add_argument("--flags", default="", help="Comma-separated executable uncertainty filters")
    d = commands.add_parser("contour-query", help="Query cached native-clock RMS/pitch with explicit interval authority")
    d.add_argument("run_dir"); d.add_argument("output")
    d.add_argument("--start"); d.add_argument("--end"); d.add_argument("--queries")
    d.add_argument("--boundary-origin", default="estimated_boundary")
    d.add_argument("--selection", choices=["centers", "contained_rms_windows"], default="centers")
    d.add_argument("--threshold-dbfs", type=float, default=-50.)
    d = commands.add_parser("compare", help="Contrast source-bound interval measurements; uninspected modalities stay OPEN")
    d.add_argument("run_dir"); d.add_argument("output")
    for opt in ("a-start", "a-end", "b-start", "b-end"):
        d.add_argument("--"+opt, required=True)
    d.add_argument("--boundary-origin", default="estimated_boundary")
    d = commands.add_parser("audio-witness", help="Prepare bounded native-rate/channel-quality listening audio")
    d.add_argument("input"); d.add_argument("output"); d.add_argument("--start", required=True); d.add_argument("--end", required=True)
    d.add_argument("--audio-stream", type=int); d.add_argument("--channel", default="preserve")
    d.add_argument("--timestamp-tolerance-ms", type=float)
    d = commands.add_parser("motion-window", help="Create temporal navigation, continuous video and source-PTS cut candidates")
    d.add_argument("input"); d.add_argument("output"); d.add_argument("--start", required=True); d.add_argument("--end", required=True)
    d.add_argument("--audio-stream", type=int); d.add_argument("--video-stream", type=int)
    d.add_argument("--frame-index-run"); d.add_argument("--no-clip", action="store_true")
    d.add_argument("--threshold", type=float, default=.35)
    obs = commands.add_parser("observer", help="Bounded attributed auditory witnesses; mock never establishes listening")
    oc = obs.add_subparsers(dest="observer_command", required=True)
    d = oc.add_parser("fixtures"); d.add_argument("output"); d.add_argument("--trials", type=int, default=24); d.add_argument("--seed", type=int, default=9271)
    d = oc.add_parser("observe"); d.add_argument("witness_run"); d.add_argument("request"); d.add_argument("output")
    d = oc.add_parser("human-import"); d.add_argument("review_import_run"); d.add_argument("output")
    for name in ("musical-relations", "blind-export", "modality-ablation", "evaluate-ablation"):
        d = commands.add_parser(name); d.add_argument("config"); d.add_argument("output")
        if name == "blind-export":
            d.add_argument("--method", help="Export this exact UTF-8 method; otherwise use the built-in fallback")
    d = commands.add_parser("vocal-baseline", help="Within-character robust distributions, deduplicated by the existing reuse ledger")
    d.add_argument("config"); d.add_argument("reuse_database"); d.add_argument("output")
    d.add_argument("--revision", type=int); d.add_argument("--minimum", type=int, default=8)
    d = commands.add_parser("segment-features", help="Optional HNR/timing/voice quality on reviewed eligible speech")
    d.add_argument("witness_run"); d.add_argument("config"); d.add_argument("output")
    d = commands.add_parser("admit-separation", help="Compare a declared separated witness with its exact raw mix clip")
    d.add_argument("raw_witness_run"); d.add_argument("separated_audio"); d.add_argument("config"); d.add_argument("output")
    d = commands.add_parser("game-bind", help="Bind dialogue IDs, voice assets and declared gameplay/UI states")
    d.add_argument("config"); d.add_argument("output")
    return p


def dispatch(a):
    c = a.command
    if c in {"preflight", "deep-read"}:
        options = {k: getattr(a,k) for k in ("audio_stream", "video_stream", "external_audio", "external_audio_offset", "transcript",
                    "screenshots_manifest", "direct_receipt", "observer_receipt", "timestamp_tolerance_ms")}
        if c == "preflight":
            from .analysis_preflight import preflight
            return preflight(a.input,a.output, audio_class=a.audio_class,probe_seconds=a.probe_seconds,**options)
        from .deep_read import deep_read
        return deep_read(a.input,a.output,method=a.method,claims=a.claims,contour_run=a.contour_run,
            episode_isolated=a.episode_isolated,auditory_observer=a.auditory_observer,execute_local_queries=a.execute_local_queries,**options)
    if c in {"evidence-plan", "audit"}:
        from .evidence_claims import audit_claims
        return audit_claims(a.config,a.output,capabilities=a.capabilities,filter_flags=[s.strip() for s in a.flags.split(",") if s.strip()])
    if c == "contour-query":
        from .evidence_queries import contour_query
        return contour_query(a.run_dir,a.output,start=a.start,end=a.end,queries=a.queries,boundary_origin=a.boundary_origin,
                             selection=a.selection,threshold_dbfs=a.threshold_dbfs)
    if c == "compare":
        from .evidence_queries import compare_intervals
        return compare_intervals(a.run_dir,a.output,a_start=a.a_start,a_end=a.a_end,b_start=a.b_start,b_end=a.b_end,boundary_origin=a.boundary_origin)
    if c == "audio-witness":
        from .audio_witness import audio_witness
        return audio_witness(a.input,a.output,start=a.start,end=a.end,stream_index=a.audio_stream,channel=a.channel,
                             timestamp_tolerance_ms=a.timestamp_tolerance_ms)
    if c == "motion-window":
        from .visual_temporal import motion_window
        return motion_window(a.input,a.output,start=a.start,end=a.end,audio_stream=a.audio_stream,video_stream=a.video_stream,
                             frame_index_run=a.frame_index_run,threshold=a.threshold,make_clip=not a.no_clip)
    if c == "observer":
        from .auditory_observer import make_probe,observe_clip,import_human_review
        if a.observer_command == "fixtures": return make_probe(a.output,trials=a.trials,seed=a.seed)
        if a.observer_command == "observe": return observe_clip(a.witness_run,a.request,a.output)
        if a.observer_command == "human-import": return import_human_review(a.review_import_run,a.output)
    if c == "musical-relations":
        from .musical_relations import musical_relations
        return musical_relations(a.config,a.output)
    if c in {"blind-export", "modality-ablation", "evaluate-ablation"}:
        from .analysis_benchmark import blind_export,modality_ablation,evaluate_ablation
        if c == "blind-export":
            return blind_export(a.config,a.output,method=a.method)
        return {"blind-export": blind_export,"modality-ablation": modality_ablation,"evaluate-ablation": evaluate_ablation}[c](a.config,a.output)
    if c == "vocal-baseline":
        from .vocal_baselines import build_baselines
        return build_baselines(a.config,a.reuse_database,a.output,revision=a.revision,minimum=a.minimum)
    if c == "segment-features":
        from .speech_segments import segment_features
        return segment_features(a.witness_run,a.config,a.output)
    if c == "admit-separation":
        from .derived_audio import admit_separation
        return admit_separation(a.raw_witness_run,a.separated_audio,a.config,a.output)
    if c == "game-bind":
        from .game_evidence import bind_game_events
        return bind_game_events(a.config,a.output)
    if c == "reuse":
        try:
            from .reuse import workflow, graph
        except ImportError as exc:
            raise AVError("Reuse analysis requires the local [reuse] extra (NumPy and SciPy)") from exc
        if a.reuse_command in {"scan", "compare"}:
            return workflow.run_scan(a.config, a.output, cache_dir=a.cache_dir, database=a.database,
                transforms=a.transforms, self_scan=a.self_scan,
                compare_ids=(a.a, a.b) if a.reuse_command == "compare" else None,
                max_candidates=a.max_candidates, max_window_s=a.max_window_s,
                min_fragment_s=a.min_fragment_s, timestamp_tolerance_ms=a.timestamp_tolerance_ms,
                preview_count=a.preview_count)
        if a.reuse_command in {"graph", "audit"}:
            return workflow.export(a.database, a.output, revision=a.revision, speaker=a.speaker,
                                   baseline=getattr(a, "baseline", None), kind=a.reuse_command)
        if a.reuse_command == "decide":
            return graph.decide(a.database, a.match_id, a.decision, a.reason, a.reviewer)
        if a.reuse_command == "promote":
            return graph.promote(a.database, a.performance_id, a.occurrence_id, a.reason, a.reviewer, a.feature)
        if a.reuse_command == "annotate":
            from .common import read_json
            return graph.annotate(a.database, a.occurrence_id, read_json(a.changes), a.reason, a.reviewer)
    if c == "doctor":
        return environment()
    if c == "probe":
        source = probe_source(a.input)
        with output_transaction(a.output, [a.input]) as out:
            write_json(out / "source.json", source)
            value = finish_run(out, "probe", [source], metadata={"result_file": "source.json"})
        return value
    if c in {"inventory", "verify"}:
        from .inventory import inventory, verify_run
        return inventory(a.config, a.output) if c == "inventory" else verify_run(a.run_dir, verify_sources=a.verify_sources)
    if c == "audio-track":
        from .audio_tracks import audio_track
        return audio_track(a.input, a.output, stream_index=a.audio_stream, start=a.start, end=a.end,
            profile=a.profile, window_ms=a.window_ms, hop_ms=a.hop_ms, spectral_scales_ms=a.spectral_scales_ms,
            stereo=a.stereo, loudness=a.loudness, render=not a.no_render,
            energy_threshold_dbfs=a.energy_threshold_dbfs, max_duration=a.max_duration, max_frames=a.max_frames)
    if c == "render-audio":
        from .audio_render import render_audio
        return render_audio(a.track_run, a.output)
    if c == "performance":
        from .performance import performance
        return performance(a.input, a.output, cache_dir=a.cache_dir, audio_stream=a.audio_stream,
            subtitle_stream=a.subtitle_stream, subtitle_run=a.subtitle_run, transcript=a.transcript,
            model_dir=a.model_dir, device=a.device, channel=a.channel, hop_ms=a.hop_ms,
            window_ms=a.window_ms, pitch_floor=a.pitch_floor, pitch_ceiling=a.pitch_ceiling,
            max_duration=a.max_duration, max_alignment_window=a.max_alignment_window,
            alignment_padding=a.alignment_padding, timestamp_tolerance_ms=a.timestamp_tolerance_ms,
            coverage_mode=a.coverage_mode, coverage_end=a.coverage_end, max_output_bytes=a.max_output_bytes,
            decode_timeout_s=a.decode_timeout_s)
    if c == "performance-index":
        from .performance_index import index_run
        return index_run(a.run_dir, a.database, a.label)
    if c == "performance-review":
        from .performance_review import build_review
        return build_review(a.config, a.output)
    if c == "performance-review-import":
        from .performance_review import import_notes
        return import_notes(a.review_run, a.notes, a.output)
    if c == "performance-review-serve":
        from .review_server import serve_review
        return serve_review(a.review_run, a.port)
    if c == "performance-video":
        from .performance_video import bind_video
        return bind_video(a.input, a.performance_run, a.output, video_stream=a.video_stream, audio_stream=a.audio_stream,
                          audio_clock_tolerance_ms=a.audio_clock_tolerance_ms)
    if c == "performance-search":
        from .performance_index import search
        return search(a.database, a.query, source=a.source, speaker=a.speaker, status=a.status,
                      min_pitch=a.min_pitch, min_rms=a.min_rms, limit=a.limit,
                      reuse_database=a.reuse_database, reuse_revision=a.reuse_revision)
    if c in {"extract-audio", "audio-metrics", "cue-features", "compare-audio", "clip-audio"}:
        from . import audio
        if c == "extract-audio": return audio.extract_audio(a.input, a.output, stream_index=a.audio_stream,
                                                               format=a.format, storage_profile=a.storage_profile)
        if c == "audio-metrics": return audio.measure_audio(a.input, a.output, stream_index=a.audio_stream, start=a.start, end=a.end)
        if c == "cue-features": return audio.cue_features(a.audio, a.dialogue_csv, a.output, stream_index=a.audio_stream,
                    pitch=a.pitch, confirm_isolated_speech=a.confirm_isolated_speech, profile=a.profile)
        if c == "compare-audio": return audio.compare_audio(a.a, a.b, a.output, a_stream=a.a_stream, b_stream=a.b_stream)
        if c == "clip-audio": return audio.clip_audio(a.input, a.output, stream_index=a.audio_stream, start=a.start, end=a.end, pad=a.pad)
    if c in {"frame-index", "frames", "contacts"}:
        from .visual import build_frame_index, extract_frames, contact_sheets
        if c == "frame-index": return build_frame_index(a.input, a.output, stream_index=a.video_stream)
        if c == "contacts": return contact_sheets(a.frame_run, a.output, columns=a.columns, rows=a.rows, thumb_width=a.thumb_width)
        return extract_frames(a.input, a.output, mode=a.mode, start=a.start, end=a.end, interval=a.interval,
                              fps=a.fps, timestamps=a.timestamps, threshold=a.threshold, width=a.width,
                              stream_index=a.video_stream, frame_index_run=a.frame_index_run,
                              seek_preroll=a.seek_preroll, input_seek=not a.no_input_seek)
    if c == "subtitles":
        from .subtitles import parse_subtitles
        return parse_subtitles(a.input, a.output, stream_index=a.subtitle_stream, media=a.media,
                               source_media=a.source_media, offset=a.offset, normalize=a.normalize, exclude_cue_ids=a.exclude_cue_ids)
    if c == "timeline":
        from .timeline import align_timeline
        return align_timeline(a.subtitle_run, a.frame_run, a.output, audio_run=a.audio_run, max_distance=a.max_distance)
    if c == "clip-av":
        from .clips import clip_av
        return clip_av(a.input, a.output, start=a.start, end=a.end, pad=a.pad, video_stream=a.video_stream, audio_stream=a.audio_stream)
    if c == "admit-external-clip":
        from .external import admit_external_clip
        return admit_external_clip(a.parent, a.clip, a.output,
                                   parent_start=a.parent_start, parent_end=a.parent_end,
                                   parent_video_stream=a.parent_video_stream,
                                   clip_video_stream=a.clip_video_stream,
                                   parent_audio_stream=a.parent_audio_stream,
                                   clip_audio_stream=a.clip_audio_stream)
    if c == "bundle":
        from .bundle import build_bundle
        return build_bundle(a.input, a.output, subtitles=a.subtitles, subtitle_stream=a.subtitle_stream,
                            audio_stream=a.audio_stream, video_stream=a.video_stream, interval=a.interval,
                            width=a.width, shots=a.shots, features=a.cue_features,
                            audio_storage=a.audio_storage)
    if c in {"review-templates", "review-check"}:
        from .reviews import validate_reviews, write_review_templates
        return write_review_templates(a.output) if c == "review-templates" else validate_reviews(a.records, a.inventory_or_run, a.capabilities, a.output)
    if c in {"pack", "verify-archive"}:
        from .packaging import pack_run, verify_archive
        return pack_run(a.run_dir, a.archive) if c == "pack" else verify_archive(a.archive)
    raise AVError("Unknown command")


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        value = dispatch(a)
        if isinstance(value, dict) and value.get("schema") == "ave.run.v1":
            display = {"operation": value["operation"], "output": str(Path(a.output).resolve()),
                       "status": value["status"], "metadata": value.get("metadata", {}),
                       "artifact_count": len(value["artifacts"])}
        else:
            display = value
        print(json.dumps(display, ensure_ascii=False, indent=2, allow_nan=False))
        if a.command == "compare-audio":
            status = value.get("metadata", {}).get("comparison_status")
            return {"MATCH": 0, "DIFFERENT": 1, "INDETERMINATE": 3}.get(status, 3)
        if a.command == "admit-external-clip":
            status = value.get("metadata", {}).get("overall_status")
            return 0 if status == "VERIFIED_ALL_SELECTED_MODALITIES" else 3
        if a.command == "review-check":
            meta = value.get("metadata", {})
            if not meta.get("structural_valid", False): return 2
            return 0 if meta.get("full_required_coverage", False) and not meta.get("extent_qualifications") else 3
        if a.command == "doctor":
            return 0 if all(x.get("path") for x in value["programs"].values()) else 2
        return 0
    except (AVError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. No incomplete run was published.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
