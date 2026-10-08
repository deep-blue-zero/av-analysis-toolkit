"""Immutable staged music identification; default execution is entirely local."""
from __future__ import annotations

import copy
from pathlib import Path

from ..common import AVError, file_record, finish_run, interval, output_transaction, probe_source, safe_member, select_stream, sha256, write_json
from ..event_contracts import CLOCK, read_event_json
from ..inventory import identifier, integer, verify_run
from . import LEVELS, SCHEMA
from .backends import AcoustIDBackend, EmbeddedMetadataBackend, LocalReuseBackend, MusicBrainzNormalizer, authorize
from .contracts import candidate, query_receipt, source_scope, validate_result
from .families import group_candidates


def load_music_run(path):
    folder = Path(path).resolve()
    if folder.is_file():
        folder = folder.parent
    manifest = verify_run(folder)
    raw = read_event_json(folder / "music-identity.json")
    validate_result(raw)
    admitted = read_event_json(folder / "run.json")
    if admitted.get("operation") not in {"music-identify", "music-adjudicate", "music-rebind"}:
        raise AVError("Music evidence must come from a verified identity workflow")
    sources = admitted.get("sources", [])
    if not any(s.get("sha256") == raw["source"]["sha256"] for s in sources) and not raw.get("parent"):
        raise AVError("Music result is not bound to its admitted source")
    for e in raw["evidence"]:
        artifact = e.get("artifact", {})
        name = artifact.get("path")
        if not isinstance(name, str):
            raise AVError("Music review evidence needs a relative run artifact")
        path = safe_member(folder, name)
        if not path.is_relative_to(folder) or path.is_symlink() or not path.is_file() or sha256(path) != artifact.get("sha256"):
            raise AVError("Music review evidence artifact differs from its frozen source")
    from .review import validate_decisions
    validate_decisions(raw)
    return folder, raw


def _auditory_candidates(path, context):
    """Import explicitly extracted guesses from an existing immutable observation."""
    from ..mapping import missing_intervals
    config_path = Path(path).resolve()
    config = read_event_json(config_path)
    if not isinstance(config, dict) or set(config) != {"observation_run", "candidates"} or not isinstance(config["candidates"], list) or len(config["candidates"]) > 100:
        raise AVError("Auditory identity input needs observation_run and a bounded candidate list")
    folder = (config_path.parent / config["observation_run"]).resolve()
    verify_run(folder)
    raw = read_event_json(folder / "observation.json")
    if raw.get("schema") not in {"ave.auditory-observation.v1", "ave.auditory-comparison.v1"} or raw.get("observer_type") == "mock":
        raise AVError("Auditory guesses require an actual attributed observation, not a mock")
    source = context["source"]
    clips = raw.get("clips") or [raw]
    if raw.get("validation_status") != "VALID" or any(c.get("source_sha256") != source["sha256"] or c.get("stream_index") != source["audio_stream"] for c in clips) or missing_intervals([source["interval_seconds"]], [c["source_interval_seconds"] for c in clips]):
        raise AVError("Auditory candidate source, stream or clip coverage differs")
    digest = sha256(folder / "observation.json")
    receipt = query_receipt(source, "attributed-auditory-observation", observation_sha256=digest,
        normalized_metadata=[{"backend_identity": raw.get("backend_identity"), "stage": raw.get("stage", 1),
            "task_profile": raw.get("task_profile"), "authority": "AUDITORY_IDENTITY_CANDIDATE"}])
    candidates = []
    for row in config["candidates"]:
        if not isinstance(row, dict) or set(row) - {"title", "artist", "level", "record_pointer"} or not isinstance(row.get("record_pointer"), str) or not row["record_pointer"].startswith("/"):
            raise AVError("Auditory guess must identify its immutable raw response pointer")
        value = raw
        try:
            for part in row["record_pointer"][1:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, ValueError, IndexError, TypeError):
            raise AVError("Auditory candidate pointer does not resolve") from None
        content = str(value).casefold()
        from .contracts import bounded_text
        bounded_text(row.get("title"))
        bounded_text(row.get("artist"))
        if not row.get("title") or row["title"].casefold() not in content:
            raise AVError("Extracted auditory title must appear in the cited immutable response value")
        if row.get("artist") and row["artist"].casefold() not in content:
            raise AVError("Extracted auditory artist must appear in the cited immutable response value")
        candidates.append(candidate(provider="attributed-auditory-observation", level=row.get("level", "UNKNOWN"),
            title=row.get("title"), artist=row.get("artist"), basis="AUDITORY_IDENTITY_CANDIDATE",
            query_id=receipt["query_id"], interval=source["interval_seconds"],
            relation={"observation_sha256": digest, "record_pointer": row["record_pointer"], "dependent_context": raw.get("stage", 1) == 2}))
    receipt.update(status="CANDIDATE" if candidates else "UNRESOLVED", candidate_ids=[c["candidate_id"] for c in candidates],
        reason="Model names remain attributed guesses; this import makes no observer call or catalogue confirmation")
    return receipt, candidates, [file_record(config_path), file_record(folder / "observation.json")]


def identify(input, output, *, audio_stream=None, start=None, end=None, alias=None, local_corpus=None,
             fingerprint=False, fpcalc=None, providers=(), allow_external_lookup=(), credential_env="ACOUSTID_API_KEY",
             normalize_musicbrainz=False, catalogue_cache=None, references=(), reference_stream=None,
             reference_start=None, reference_end=None, transforms=False, auditory_candidates=None,
             max_candidates=20, max_external_requests=10, transport=None, reuse_cache=None):
    if set(providers) - {"acoustid"} or set(allow_external_lookup) - {"acoustid", "musicbrainz"}:
        raise AVError("Unknown music provider or lookup authorization")
    integer(max_external_requests, "external request ceiling", 1)
    integer(max_candidates, "candidate ceiling", 1)
    if max_external_requests > 50 or max_candidates > 100 or len(references) > 10:
        raise AVError("Music identification exceeds its bounded query budget")
    if "acoustid" in providers:
        authorize("acoustid", allow_external_lookup)
    if normalize_musicbrainz:
        authorize("musicbrainz", allow_external_lookup)
        if not catalogue_cache:
            raise AVError("MusicBrainz normalization requires a separate local catalogue cache")
    probe = probe_source(input)
    stream = select_stream(probe, "audio", audio_stream)
    a, b = interval(start, end, probe["duration_seconds"])
    if end is None:
        b = min(b, a + 30.)
    if b - a > 120:
        raise AVError("Supply a music query interval of at most 120 seconds; use music plan for alternatives")
    scope = {"alias": alias or "source-" + probe["sha256"][:16], "sha256": probe["sha256"],
        "display_name": alias or "source-" + probe["sha256"][:16], "audio_stream": stream["index"],
        "clock": CLOCK, "interval_seconds": [a, b]}
    source_scope(scope)
    inputs = [input, *references] + ([local_corpus] if local_corpus else []) + ([auditory_candidates] if auditory_candidates else [])
    for supplied_cache in (catalogue_cache, reuse_cache):
        if not supplied_cache:
            continue
        cache = Path(supplied_cache).resolve()
        target = Path(output).resolve()
        if cache == target or cache.is_relative_to(target) or target.is_relative_to(cache):
            raise AVError("Mutable caches must be separate from the immutable music output")
    with output_transaction(output, inputs) as stage:
        result = {"schema": SCHEMA, "source": scope, "queries": [], "candidates": [], "families": [],
            "correspondences": [], "reference_comparisons": [], "evidence": [],
            "adjudication": [{"level": level, "status": "UNRESOLVED", "candidate_ids": [], "evidence_ids": [],
                "reviewer": None, "reason": "No explicit identity adjudication performed"} for level in LEVELS],
            "unresolved": ["Candidate scores are provider-native, not calibrated probabilities", "No match does not establish originality or a different composition"],
            "network_activity": {"external_requests": 0, "media_uploads": 0, "paid_calls": 0},
            "privacy": {"default": "LOCAL_ONLY", "portable_source": True, "private_bindings_file": "execution/manifest.json",
                "external_lookup_authorization_separate_from_media_upload": True, "stores_credentials": False, "stores_media_base64": False}}
        context = {"probe": probe, "stream": stream, "source": scope, "stage": stage, "local_corpus": local_corpus,
                   "max_candidates": max_candidates, "transforms": transforms, "correspondences": result["correspondences"], "reuse_cache": reuse_cache}
        sources, execution = [probe], {"source_path": probe["path"], "source": scope, "selected_stream": stream,
            "local_corpus_path": str(Path(local_corpus).resolve()) if local_corpus else None, "references": [], "fpcalc": None,
            "reuse_cache_path": str(Path(reuse_cache).resolve()) if reuse_cache else None}
        def add(pair):
            receipt, candidates = pair
            result["queries"].append(receipt)
            result["candidates"].extend(candidates)
            result["network_activity"]["external_requests"] += int(receipt.get("external_request_performed", False))
        add(EmbeddedMetadataBackend().query(context))
        if local_corpus:
            add(LocalReuseBackend().query(context))
            sources.append(file_record(local_corpus, "reuse_corpus"))
        if fingerprint or "acoustid" in providers:
            from ..audio_witness import write_witness
            from .fingerprint import calculate
            q = query_receipt(scope, "chromaprint")
            try:
                witness_dir = stage / "execution" / "fingerprint-witness"
                witness_dir.mkdir(parents=True)
                witness = write_witness(probe, stream, witness_dir, start=a, end=b)
                fp = calculate(witness_dir / "audio.wav", b - a, fpcalc)
                execution["fpcalc"] = fp
                context["fingerprint"] = fp
                write_json(witness_dir / "witness.json", witness)
                q.update(status="CANDIDATE", fingerprint_sha256=fp["fingerprint_sha256"],
                         normalized_metadata=[{"duration_seconds": fp["duration_seconds"], "witness_sha256": fp["witness_sha256"]}],
                         reason="Local fingerprint computed; this establishes no identity")
            except AVError as exc:
                q.update(status="UNRESOLVED", reason=str(exc))
            add((q, []))
            if "acoustid" in providers and "fingerprint" in context:
                add(AcoustIDBackend(allow_external_lookup, credential_env, transport).query(context))
            elif "acoustid" in providers:
                add((query_receipt(scope, "acoustid", "DERIVED_FINGERPRINT_LOOKUP", status="NOT_TESTED",
                    authorization="EXPLICIT_EXTERNAL_LOOKUP", reason="No valid local fingerprint; external request not performed"), []))
        if normalize_musicbrainz:
            normalizer = MusicBrainzNormalizer(allow_external_lookup, catalogue_cache, transport)
            ids = sorted({c["musicbrainz_recording_id"] for c in result["candidates"] if c.get("musicbrainz_recording_id")})
            for recording_id in ids:
                if sum(bool(q.get("external_request_performed") or q.get("test_double")) for q in result["queries"]) >= max_external_requests:
                    result["unresolved"].append("MusicBrainz normalization stopped at the explicit request ceiling")
                    break
                add(normalizer.query(recording_id, scope))
        if auditory_candidates:
            q, candidates, dependencies = _auditory_candidates(auditory_candidates, context)
            add((q, candidates))
            sources.extend(dependencies)
        for i, reference in enumerate(references, 1):
            from .local import reference_comparison
            comparison, ref_source = reference_comparison(context, reference, i, reference_stream, reference_start, reference_end)
            result["reference_comparisons"].append(comparison)
            sources.append(ref_source)
            execution["references"].append({"path": ref_source["path"], "sha256": ref_source["sha256"]})
        result["correspondences"] = list({c["match_id"]: c for c in result["correspondences"]}.values())
        result["families"] = group_candidates(result["candidates"])
        validate_result(result)
        write_json(stage / "execution" / "manifest.json", execution)
        write_json(stage / "music-identity.json", result)
        return finish_run(stage, "music-identify", sources,
            parameters={"audio_stream": stream["index"], "interval_seconds": [a, b], "allow_external_lookup": list(allow_external_lookup),
                "max_external_requests": max_external_requests, "transforms": transforms},
            metadata={"result_file": "music-identity.json", "candidate_count": len(result["candidates"]), "perceptual_review": "NOT_PERFORMED"})
