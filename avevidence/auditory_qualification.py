"""Recomputed, task-specific natural-audio benchmarks and scoped qualifications.

Reference and evaluator judgments are attributed declarations, never fabricated
by the observer. All scored outputs and disagreements remain inspectable.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .common import AVError, file_record, finish_run, output_transaction, sha256, write_json
from .event_contracts import array, canonical_digest, choice, object_fields, read_event_json, span, strings
from .inventory import digest_value, identifier, text_value, verify_run

REVISION = "auditory-qualification-v2"
TASK_PROPOSITIONS = {
    "SPEECH_DELIVERY": {"delivery"}, "NONVERBAL_VOCAL": {"nonverbal_vocal"},
    "MUSIC_STRUCTURE": {"music_structure"}, "PERFORMANCE_MUSIC": {"performance_music"},
    "SOUNDSCAPE": {"soundscape"}, "AUDITORY_EVENT_TIMING": {"auditory_event_timing"},
    "LEXICAL_TRANSCRIPTION": {"wording"}, "EXACT_WORD_TIMING": {"speech_timing"},
}
PROFILE_TASK = {"SPEECH_PERFORMANCE": "SPEECH_DELIVERY", "AV_SYNC": "AUDITORY_EVENT_TIMING",
                **{task: task for task in TASK_PROPOSITIONS}}
STATES = {"NOT_TESTED", "PROVISIONAL", "VALIDATED_ON_BENCHMARK", "QUALIFIED_FOR_SCOPE", "FAILED", "UNRESOLVED"}
LABELS = {"PRESENT", "ABSENT", "ABSTAIN"}
CRITERIA = {"minimum_held_out_examples": 20, "minimum_positive": 6, "minimum_negative": 6,
            "minimum_ambiguous": 2, "maximum_overall_failure_rate": .2,
            "maximum_false_positive_rate": .1, "maximum_false_negative_rate": .2,
            "maximum_control_failure_rate": .2}
TIMING_TOLERANCES = {"AUDITORY_EVENT_TIMING": .05, "EXACT_WORD_TIMING": .02}


def competence(profile):
    if profile not in PROFILE_TASK:
        raise AVError("No task-specific competency is defined for this profile")
    return PROFILE_TASK[profile]


def _valid_response(obs):
    from .auditory_observer import validate_response
    from .observer_requests import validate_comparison_response
    if not isinstance(obs, dict) or obs.get("validation_status") != "VALID":
        return False
    try:
        if obs.get("raw_comparison_clips"):
            parsed = validate_comparison_response(obs.get("raw_response"), obs["raw_comparison_clips"])
            label = obs.get("comparison_projection", {}).get("label")
            return [r["observation"] for r in parsed["clips"] if r["label"] == label] == [obs.get("parsed")]
        return validate_response(obs.get("raw_response"), obs["duration_seconds"]) == obs.get("parsed")
    except (AVError, KeyError, TypeError):
        return False


def validate_dataset(value):
    keys = {"schema", "dataset_id", "revision", "task", "media_category", "source_kind",
            "scope_description", "reference_method", "limitations", "examples"}
    object_fields(value, keys, keys, "auditory benchmark dataset")
    if value["schema"] != "ave.auditory-benchmark-dataset.v1":
        raise AVError("Unsupported auditory benchmark dataset")
    identifier(value["dataset_id"], "dataset ID")
    choice(value["task"], TASK_PROPOSITIONS, "auditory competency")
    choice(value["source_kind"], {"NATURAL_AUDIO", "GENERATED", "MOCK"}, "benchmark source kind")
    for key in ("revision", "media_category", "scope_description", "reference_method"):
        text_value(value[key], key)
    strings(value["limitations"], "benchmark limitations", 100, 1)
    ids, locators = set(), set()
    for row in array(value["examples"], "benchmark examples", 1000, 1):
        keys = {"id", "source_sha256", "stream_index", "interval_seconds", "split", "control",
                "expected", "reference_votes", "reference_provenance"}
        object_fields(row, keys, keys, "reference example")
        key = identifier(row["id"], "example ID")
        digest_value(row["source_sha256"])
        if type(row["stream_index"]) is not int or row["stream_index"] < 0:
            raise AVError("Benchmark reference requires the absolute audio stream")
        interval = span(row["interval_seconds"])
        locator = (row["source_sha256"], row["stream_index"], tuple(interval))
        if key in ids or locator in locators:
            raise AVError("Repeated IDs or source intervals cannot inflate benchmark sample size")
        ids.add(key); locators.add(locator)
        choice(row["split"], {"development", "held_out"}, "benchmark split")
        choice(row["control"], {"POSITIVE", "NEGATIVE", "AMBIGUOUS"}, "reference control")
        choice(row["expected"], LABELS, "reference expectation")
        expected = {"POSITIVE": "PRESENT", "NEGATIVE": "ABSENT", "AMBIGUOUS": "ABSTAIN"}[row["control"]]
        if row["expected"] != expected:
            raise AVError("Control and reference expectation differ")
        text_value(row["reference_provenance"], "independently prepared source/reference provenance")
        reviewers = set()
        for vote in array(row["reference_votes"], "reference votes", 100, 1):
            object_fields(vote, {"reviewer", "label", "basis"}, {"reviewer", "label", "basis"}, "reference vote")
            text_value(vote["reviewer"], "reference reviewer"); text_value(vote["basis"], "reference basis")
            choice(vote["label"], LABELS, "reference vote label")
            if vote["reviewer"] in reviewers:
                raise AVError("A reviewer cannot cast multiple reference votes")
            reviewers.add(vote["reviewer"])
        if len({v["label"] for v in row["reference_votes"]}) > 1 and row["control"] != "AMBIGUOUS":
            raise AVError("Human disagreement requires an ambiguous/abstention reference case")
        if row["control"] != "AMBIGUOUS" and any(v["label"] != expected for v in row["reference_votes"]):
            raise AVError("Reference judgment and expected label disagree")
    return value


def _review(value, example, observation_sha256):
    keys = {"schema", "example_id", "observation_sha256", "reviewer", "reference_independent",
            "blinded_input_declared", "predicted_label", "reason", "timing_error_seconds"}
    object_fields(value, keys, keys-{"timing_error_seconds"}, "benchmark output review")
    if (value["schema"] != "ave.auditory-benchmark-review.v1" or value["example_id"] != example["id"]
        or value["observation_sha256"] != observation_sha256):
        raise AVError("Benchmark output review differs from its immutable trial")
    text_value(value["reviewer"], "benchmark evaluator"); text_value(value["reason"], "benchmark score reason")
    choice(value["predicted_label"], LABELS, "evaluated model prediction")
    for key in ("reference_independent", "blinded_input_declared"):
        if type(value[key]) is not bool:
            raise AVError("Benchmark independence and blinding must be declared explicitly")
    if "timing_error_seconds" in value:
        from .common import finite
        finite(value["timing_error_seconds"], "timing error")
        if value["timing_error_seconds"] < 0:
            raise AVError("Timing error must be nonnegative")
    return value


def evaluate_benchmark(dataset, trials, backend_identity, configuration_revision, scope_approval=None):
    """Score validated snapshots. Returned states are derived, not user inputs."""
    validate_dataset(dataset)
    if not isinstance(backend_identity, dict) or not backend_identity.get("model_revision"):
        raise AVError("Qualification requires a complete backend identity")
    required_identity = {"type", "backend_id", "provider", "route_type", "model_revision", "adapter_revision",
                         "api_route", "prompt_revision", "configuration_revision"}
    if required_identity-set(backend_identity) or backend_identity["configuration_revision"] != configuration_revision:
        raise AVError("Qualification requires exact route, adapter, prompt and configuration revisions")
    for key in required_identity:
        text_value(backend_identity[key], "backend "+key)
    text_value(configuration_revision, "prompt/configuration revision")
    rows = {row["id"]: row for row in dataset["examples"]}
    array(trials, "benchmark trials", 1000)
    seen, judged = set(), []
    for trial in trials:
        keys = {"example_id", "observation", "observation_sha256", "review", "valid_output", "validation_error"}
        object_fields(trial, keys, keys, "benchmark trial snapshot")
        eid = trial["example_id"]
        if eid not in rows or eid in seen:
            raise AVError("Every benchmark example needs at most one primary trial")
        seen.add(eid)
        obs, example = trial["observation"], rows[eid]
        digest_value(trial["observation_sha256"])
        if (not isinstance(obs, dict) or obs.get("backend_identity") != backend_identity
            or obs.get("source_sha256") != example["source_sha256"]
            or obs.get("stream_index") != example["stream_index"]
            or obs.get("source_interval_seconds") != example["interval_seconds"]
            or obs.get("media_category") != dataset["media_category"]
            or competence(obs.get("task_profile", "SPEECH_PERFORMANCE")) != dataset["task"]):
            raise AVError("Benchmark trial is not bound to this backend/task/reference interval")
        review = _review(trial["review"], example, trial["observation_sha256"])
        if type(trial["valid_output"]) is not bool:
            raise AVError("Trial validity must be recomputed before scoring")
        # Revalidate the local response contract rather than trusting VALID.
        valid = _valid_response(obs)
        if valid != trial["valid_output"]:
            raise AVError("Stored trial validity differs from its raw response contract")
        real = (obs.get("observer_type") == "model_audio" and
                backend_identity.get("execution_mode") != "TEST_DOUBLE" and backend_identity.get("type") != "mock")
        collection_verified = False
        if real:
            from .auditory_claims import validate_observation_data
            try:
                validate_observation_data(obs, allow_experimental=True, require_valid_output=valid)
                collection_verified = True
            except AVError:
                if valid:
                    raise
        refs = {v["reviewer"] for v in example["reference_votes"]}
        independent = review["reference_independent"] and review["reviewer"] not in refs
        timing_limit = TIMING_TOLERANCES.get(dataset["task"])
        timing_correct = (timing_limit is None or example["control"] != "POSITIVE" or
                          review.get("timing_error_seconds") is not None and
                          review["timing_error_seconds"] <= timing_limit)
        correct = valid and review["predicted_label"] == example["expected"] and timing_correct
        judged.append({"example_id": eid, "split": example["split"], "control": example["control"],
            "correct": correct, "valid_output": valid, "prediction": review["predicted_label"],
            "independent_reference": independent, "blinded_input_declared": review["blinded_input_declared"],
            "actual_observer": real and collection_verified, "collection_verified": collection_verified,
            "timing_correct": timing_correct, "stage": obs.get("stage", 1),
            "context_minimized": not obs.get("supplied_context"),
            "reference_votes": copy.deepcopy(example["reference_votes"]), "evaluator": review["reviewer"],
            "timing_error_seconds": review.get("timing_error_seconds")})
    held = [r for r in judged if r["split"] == "held_out"]
    totals = {name: sum(r["control"] == name for r in held) for name in ("POSITIVE", "NEGATIVE", "AMBIGUOUS")}
    n = len(held)
    errors = sum(not r["correct"] for r in held)
    fp = sum(r["valid_output"] and r["control"] == "NEGATIVE" and r["prediction"] == "PRESENT" for r in held)
    # A malformed or mistimed positive is still a failed detection, even when
    # its attributed label says PRESENT. Other classes cannot dilute that error.
    fn = sum(r["control"] == "POSITIVE" and not r["correct"] for r in held)
    control_failures = {name: sum(r["control"] == name and not r["correct"] for r in held) for name in totals}
    control_rates = {name: control_failures[name]/totals[name] if totals[name] else None for name in totals}
    metrics = {"trials": n, "correct": n-errors, "invalid_outputs": sum(not r["valid_output"] for r in held),
        "overall_failure_rate": errors/n if n else None, "false_positives": fp, "false_negatives": fn,
        "false_positive_rate": fp/totals["NEGATIVE"] if totals["NEGATIVE"] else None,
        "false_negative_rate": fn/totals["POSITIVE"] if totals["POSITIVE"] else None,
        "controls": totals, "control_failures": control_failures, "control_failure_rates": control_rates,
        "timing_failures": sum(not r["timing_correct"] for r in held),
        "missing_examples": sorted(set(rows)-seen)}
    enough = (n >= CRITERIA["minimum_held_out_examples"] and totals["POSITIVE"] >= 6
              and totals["NEGATIVE"] >= 6 and totals["AMBIGUOUS"] >= 2 and not metrics["missing_examples"])
    passed = bool(enough and metrics["overall_failure_rate"] <= CRITERIA["maximum_overall_failure_rate"]
                  and metrics["false_positive_rate"] <= CRITERIA["maximum_false_positive_rate"]
                  and metrics["false_negative_rate"] <= CRITERIA["maximum_false_negative_rate"]
                  and all(rate <= CRITERIA["maximum_control_failure_rate"] for rate in control_rates.values()))
    returned_models = {(t["observation"].get("provider_receipt") or {}).get("returned_model", backend_identity.get("model_revision")) for t in trials}
    natural = (len(returned_models) == 1 and None not in returned_models and dataset["source_kind"] == "NATURAL_AUDIO" and all(r["actual_observer"] and
               r["independent_reference"] and r["blinded_input_declared"] and r["stage"] == 1 and r["context_minimized"] for r in held))
    status = "NOT_TESTED" if not n else "PROVISIONAL" if not enough or not natural else "VALIDATED_ON_BENCHMARK" if passed else "FAILED"
    if scope_approval is not None:
        keys = {"reviewer", "basis", "dataset_sha256", "task", "media_category", "configuration_revision"}
        object_fields(scope_approval, keys, keys, "explicit qualification scope review")
        for key in ("reviewer", "basis"):
            text_value(scope_approval[key], key)
        if (scope_approval["dataset_sha256"] != canonical_digest(dataset) or scope_approval["task"] != dataset["task"]
            or scope_approval["media_category"] != dataset["media_category"]
            or scope_approval["configuration_revision"] != configuration_revision):
            raise AVError("Scope approval does not bind this exact benchmark/task/configuration")
        if passed and natural:
            status = "QUALIFIED_FOR_SCOPE"
    return {"schema": "ave.auditory-task-qualification.v1", "revision": REVISION,
        "backend_identity": copy.deepcopy(backend_identity), "configuration_revision": configuration_revision,
        "returned_model": next(iter(returned_models)) if len(returned_models) == 1 else None,
        "task": dataset["task"], "media_category": dataset["media_category"],
        "dataset_id": dataset["dataset_id"], "dataset_revision": dataset["revision"],
        "dataset_sha256": canonical_digest(dataset), "scope_description": dataset["scope_description"],
        "scoring_method": "Independent reviewed PRESENT/ABSENT/ABSTAIN; all failed positives count as false negatives; invalids and timing failures retained; each control family separately bounded",
        "criteria": {**copy.deepcopy(CRITERIA), "maximum_positive_timing_error_seconds": TIMING_TOLERANCES.get(dataset["task"])},
        "results": judged, "metrics": metrics, "status": status,
        "scope_approval": copy.deepcopy(scope_approval), "limitations": copy.deepcopy(dataset["limitations"]),
        "global_qualification": False, "authorizes_submission": False}


def _benchmark_input_runs(records):
    """Protect every enclosing immutable AV run, including nested outer runs."""
    checked, roots, manifests = set(), [], []
    for record in records:
        for folder in Path(record["path"]).parents:
            if folder in checked:
                continue
            checked.add(folder)
            marker = folder/"run.json"
            if not marker.is_file():
                continue
            manifest = read_event_json(marker)
            if not isinstance(manifest, dict) or manifest.get("schema") != "ave.run.v1":
                continue
            verify_run(folder)
            roots.append(folder)
            manifests.append(file_record(marker))
    return roots, manifests


def benchmark(config, output):
    """Snapshot reference and observation/review artifacts, then recompute scores."""
    from .scene_packets import resolve_artifact
    path = Path(config).resolve()
    value = read_event_json(path)
    keys = {"dataset", "backend_identity", "configuration_revision", "trials", "scope_approval"}
    object_fields(value, keys, keys-{"scope_approval"}, "benchmark configuration")
    dataset = validate_dataset(value["dataset"])
    examples = {r["id"]: r for r in dataset["examples"]}
    deps, snapshots = [file_record(path)], []
    for trial in array(value["trials"], "trial artifacts", 1000):
        object_fields(trial, {"example_id", "observation", "review"}, {"example_id", "observation", "review"}, "trial artifacts")
        if trial["example_id"] not in examples:
            raise AVError("Trial references a missing benchmark example")
        obs_path = resolve_artifact(trial["observation"], path.parent)
        review_path = resolve_artifact(trial["review"], path.parent)
        obs = read_event_json(obs_path)
        review = read_event_json(review_path)
        valid = _valid_response(obs)
        error = None if valid else "Invalid strict response contract or validation status"
        snapshots.append({"example_id": trial["example_id"], "observation": obs,
            "observation_sha256": sha256(obs_path), "review": review, "valid_output": valid,
            "validation_error": error})
        deps.extend((file_record(obs_path), file_record(review_path)))
    record = evaluate_benchmark(dataset, snapshots, value["backend_identity"], value["configuration_revision"], value.get("scope_approval"))
    input_runs, manifests = _benchmark_input_runs(deps)
    deps.extend(manifests)
    with output_transaction(output, [r["path"] for r in deps]+input_runs) as stage:
        write_json(stage/"benchmark-snapshot.json", {"dataset": dataset, "trials": snapshots,
            "backend_identity": value["backend_identity"], "configuration_revision": value["configuration_revision"],
            "scope_approval": value.get("scope_approval")})
        # Preserve original JSON bytes as well as the scored projection.
        for i, trial in enumerate(value["trials"]):
            for name in ("observation", "review"):
                source = resolve_artifact(trial[name], path.parent)
                destination = stage/"originals"/str(i)/(name+".json")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
        write_json(stage/"qualification.json", record)
        for folder in input_runs:
            verify_run(folder)
        return finish_run(stage, "auditory-benchmark", deps, metadata={"result_file": "qualification.json", "status": record["status"]})


def load_qualification(path, *, identity=None, task=None, media_category=None, configuration_revision=None, required=True):
    path = Path(path).resolve()
    folder = path if path.is_dir() else path.parent
    verify_run(folder)
    snapshot = read_event_json(folder/"benchmark-snapshot.json")
    record = read_event_json(folder/"qualification.json")
    if record.get("revision") != REVISION:
        raise AVError("Qualification uses an earlier scoring revision or unsupported revision; recompute original inputs into a NEW run without rewriting the archive")
    for i, trial in enumerate(snapshot["trials"]):
        original = folder/"originals"/str(i)/"observation.json"
        if (sha256(original) != trial["observation_sha256"] or read_event_json(original) != trial["observation"]
            or read_event_json(folder/"originals"/str(i)/"review.json") != trial["review"]):
            raise AVError("Benchmark snapshot differs from its retained original trial/review bytes")
    expected = evaluate_benchmark(snapshot["dataset"], snapshot["trials"], snapshot["backend_identity"],
                                  snapshot["configuration_revision"], snapshot["scope_approval"])
    if record != expected:
        raise AVError("Qualification must be recomputed from benchmark evidence, not self-declared")
    for key, value in (("backend_identity", identity), ("task", task), ("media_category", media_category),
                       ("configuration_revision", configuration_revision)):
        if value is not None and record[key] != value:
            raise AVError("Qualification differs from the exact backend/task/media/configuration scope")
    if required and record["status"] != "QUALIFIED_FOR_SCOPE":
        raise AVError("This exact auditory task has not earned qualification for the requested scope")
    return record
