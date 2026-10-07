"""Task-scoped auditory questions and review, never a global hearing certificate."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, write_json
from .inventory import text_value, verify_run

PROFILE_REVISION = "auditory-task-profiles-v1"
DEFAULT_PROFILE = "SPEECH_PERFORMANCE"
TASK_PROMPTS = {
    DEFAULT_PROFILE: "Describe only audible pitch movement, pace, loudness, hesitation, articulation and vocal tension. Separate acoustic descriptions from emotional readings. Give timed observations, alternatives, interference and abstentions. Do not infer speaker identity from filenames, subtitles or camera focus.",
    "NONVERBAL_VOCAL": "Describe audible nonverbal vocal events, including breaths, cries, screams, laughter, gasps, groans and sighs where supportable. Distinguish vocal sounds from ambiguous effects. Give timed observations, interference, alternatives and abstentions; do not infer identity or plot.",
    "MUSIC_STRUCTURE": "Describe audible musical entries, exits, continuity, section changes, recurrence, relative density and dynamics. Name instrumentation only when supportable. Give timed observations, alternatives, interference and abstentions; do not infer narrative meaning.",
    "PERFORMANCE_MUSIC": "Describe audible sung realization and changes in phrasing, vocal development, singer/instrument relationship, ensemble density, intensity and audience sound across this bounded musical section. Distinguish audible features from dramatic interpretation and state interference, alternatives and abstentions. Do not identify a singer from context.",
    "SOUNDSCAPE": "Describe audible ambience, impacts, mechanical sounds, effects, noise, near-silence and foreground/background relationships. Give timed observations, alternatives, interference and abstentions. Obscured speech need not prevent soundscape observations; do not infer visible causes.",
    "AV_SYNC": "Locate audible event onsets, changes, impacts, vocal effort, music changes and silence within this clip. Give timing uncertainty and interference. Audio alone does not establish a visual event, contact, movement order or audiovisual synchronization; leave those relationships for separately cited temporal evidence.",
}
TASK_PROFILES = tuple(TASK_PROMPTS)


def task_profile(value=None):
    value = DEFAULT_PROFILE if value is None else value
    if not isinstance(value, str) or value not in TASK_PROMPTS:
        raise AVError("Unknown auditory task profile")
    return value


def profile_prompt(value=None):
    return TASK_PROMPTS[task_profile(value)]


def request_policy(request=None):
    """An explicit musical section expands bounds, never authorization or money."""
    request = {} if request is None else request
    if not isinstance(request, dict):
        raise AVError("Auditory task policy must be a request object")
    profile = task_profile(request.get("task_profile"))
    mode = request.get("audio_mode", "microclip")
    if not isinstance(mode, str) or mode not in {"microclip", "coherent_section"}:
        raise AVError("Audio mode must be microclip or coherent_section")
    if mode == "coherent_section":
        if profile != "PERFORMANCE_MUSIC" or request.get("allow_long_section") is not True:
            raise AVError("Coherent sections require PERFORMANCE_MUSIC and explicit allow_long_section: true")
        if "clips" in request:
            raise AVError("A coherent musical section is one source witness, not a comparison")
        import math
        budget = request.get("section_budget_usd")
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise AVError("Coherent sections require an explicit positive section_budget_usd ceiling")
        return {"task_profile": profile, "audio_mode": mode, "max_clip_seconds": 120.,
                "max_total_seconds": 120., "max_clips": 1, "max_audio_bytes": 128*1024*1024,
                "explicit_request_budget_required": True, "section_budget_usd": budget}
    if "allow_long_section" in request or "section_budget_usd" in request:
        raise AVError("Long-section opt-in applies only to coherent_section requests")
    return {"task_profile": profile, "audio_mode": mode, "max_clip_seconds": 30.,
            "max_total_seconds": 60., "max_clips": 4, "max_audio_bytes": 64*1024*1024,
            "explicit_request_budget_required": False}


def validate_audio_bounds(durations, policy):
    import math
    if (not isinstance(durations, (list, tuple)) or not 1 <= len(durations) <= policy["max_clips"]
        or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 < x <= policy["max_clip_seconds"] for x in durations)
        or sum(durations) > policy["max_total_seconds"]):
        raise AVError("Auditory request exceeds its explicit clip, section or comparison bounds")


def capability_profile(backend_identity, *, historical_probes=(), scoped_reviews=()):
    """Summarize actual history without turning input influence into task skill.

    Historical records are retained verbatim by reference/hash. A scoped owner
    review supplies no backend-wide validation, even if broadly accurate.
    """
    if not isinstance(backend_identity, dict) or not isinstance(backend_identity.get("model_revision"), str):
        raise AVError("Task capability profile requires an attributed backend identity")
    text_value(backend_identity["model_revision"], "backend model revision")
    if not isinstance(historical_probes, (list, tuple)) or not isinstance(scoped_reviews, (list, tuple)):
        raise AVError("Capability histories must be explicit record lists")
    probes, reviews = [], []
    for value in historical_probes:
        if (not isinstance(value, dict) or not isinstance(value.get("status"), str)
            or value["status"] not in {"PROBE_PASSED", "PROBE_FAILED", "NOT_TESTED", "UNRESOLVED"}):
            raise AVError("Historical probe status must be preserved explicitly")
        probes.append(copy.deepcopy(value))
    for value in scoped_reviews:
        if not isinstance(value, dict) or value.get("schema") != "ave.scoped-human-auditory-review.v1" or value.get("global_backend_qualification") is not False:
            raise AVError("Scoped human review cannot qualify the backend globally")
        reviews.append(copy.deepcopy(value))
    return {"schema": "ave.auditory-task-capabilities.v1", "backend_identity": copy.deepcopy(backend_identity),
        "profile_revision": PROFILE_REVISION,
        "tasks": {name: {"status": "UNQUALIFIED", "test_status": "NOT_TESTED"} for name in TASK_PROFILES},
        "lexical_transcription": "NON_AUTHORITATIVE", "exact_word_timing": "PROVISIONAL",
        "historical_probes": probes, "scoped_human_reviews": reviews,
        "scope": "Task proficiency is unqualified until actually tested; historical probe outcomes and scoped human assessments are separate",
        "authorizes_hosted_submission": False}


def scoped_human_review(observation_run, config, output):
    """Record a supplied human judgment for one immutable observation/clip scope."""
    folder, config = Path(observation_run).resolve(), Path(config).resolve()
    verify_run(folder)
    observation_path = folder/"observation.json"
    observation = read_json(observation_path)
    if observation.get("schema") not in {"ave.auditory-observation.v1", "ave.auditory-comparison.v1"}:
        raise AVError("Scoped review requires an attributed auditory observation run")
    value = read_json(config)
    required = {"reviewer_type", "reviewer", "scope", "judgment", "exceptions", "date", "inspected_intervals"}
    if (not isinstance(value, dict) or set(value) != required or not isinstance(value["reviewer_type"], str)
        or value["reviewer_type"] not in {"owner", "human"}):
        raise AVError("Scoped review needs the explicit reviewer, scope, judgment, exceptions, date and inspected intervals")
    for name in ("reviewer", "scope"):
        text_value(value[name], name)
    if not isinstance(value["judgment"], str) or value["judgment"] not in {"broadly_accurate", "partly_accurate", "inaccurate", "uncertain", "abstain"}:
        raise AVError("Unknown scoped listening judgment")
    if not isinstance(value["exceptions"], list) or len(value["exceptions"]) > 100:
        raise AVError("Scoped review exceptions must be a bounded list")
    for exception in value["exceptions"]:
        text_value(exception, "review exception")
    try:
        if date.fromisoformat(value["date"]).isoformat() != value["date"]:
            raise ValueError("Use the explicit YYYY-MM-DD calendar form")
    except (ValueError, TypeError):
        raise AVError("Scoped review date must be an ISO calendar date") from None
    clips = observation.get("clips") or [observation]
    intervals = value["inspected_intervals"]
    if not isinstance(intervals, list) or not intervals or len(intervals) > 100:
        raise AVError("Human review must explicitly declare its inspected source intervals")
    from .analysis_schema import locator
    from .mapping import missing_intervals
    for loc in intervals:
        locator(loc, modality="audio")
        matching = [c for c in clips if c.get("source_sha256") == loc["source_sha256"] and c.get("stream_index") == loc["stream_index"]]
        if not matching or missing_intervals(loc["intervals_seconds"], [c["source_interval_seconds"] for c in matching]):
            raise AVError("Human review exceeds this observation's source/stream/clip scope")
    with output_transaction(output, [folder, config]) as stage:
        record = {"schema": "ave.scoped-human-auditory-review.v1", **value,
            "task_profile": task_profile(observation.get("task_profile")),
            "observation_sha256": sha256(observation_path), "backend_identity": observation.get("backend_identity"),
            "observation_validation_status": observation.get("validation_status"),
            "global_backend_qualification": False, "changes_original_response": False,
            "scope_note": "Supplied listening declaration for these intervals only; not global model validation or automatic claim support"}
        write_json(stage/"human-review.json", record)
        return finish_run(stage, "scoped-auditory-review", [file_record(observation_path), file_record(config)],
                          metadata={"result_file": "human-review.json", "global_backend_qualification": False})
