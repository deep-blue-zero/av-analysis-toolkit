"""Manufactured record contracts, never a natural-media or model benchmark.

The NATURAL_AUDIO declarations below simulate third-party benchmark receipts
to test qualification rules. No actual backend earns qualification in CI.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.auditory_qualification import benchmark, competence, load_qualification, validate_dataset
from avevidence.common import AVError, finish_run, read_json, sha256, write_json
from avevidence.event_contracts import canonical_digest
from avevidence.auditory_claims import validate_model_observation
from avevidence.inventory import verify_run


def rewrite_fixture(path, value):
    """Mutate generated test inputs before admission, never production receipts."""
    path.write_text(json.dumps(value), encoding="utf-8")


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


IDENTITY = {"type": "model_audio", "backend_id": "generated-record-contract", "provider": "local-fixture",
    "route_type": "local", "execution_mode": "LOCAL", "model_revision": "fixture-revision",
    "adapter_revision": "fixture-adapter-v1", "api_route": "local/fixture",
    "prompt_revision": "neutral-fixture-v1", "configuration_revision": "fixture-config-v1"}


def observation(index=0, *, source_sha256=None):
    source_sha256 = source_sha256 or h("source-"+str(index))
    clip_hash = h("clip-"+str(index))
    interval = [float(index), float(index+1)]
    parsed = {"observations": [{"start_s": .1, "end_s": .8, "description": "The delivery slows near the end."}],
              "alternatives": [], "interference": [], "abstentions": [], "confidence": "low"}
    mapping = {"parent_source_sha256": source_sha256, "parent_stream_index": 0, "artifact_sha256": clip_hash,
        "artifact_path": "audio.wav", "modality": "audio", "segments": [{"parent_start_seconds": interval[0],
        "parent_end_seconds": interval[1], "derivative_start_seconds": 0., "derivative_end_seconds": 1.}]}
    binding = {"source_sha256": source_sha256, "stream_index": 0, "clip_sha256": clip_hash,
        "source_interval_seconds": interval, "duration_seconds": 1., "review_mapping": mapping,
        "wav_format": {"sample_rate_hz": 8000, "bits_per_sample": 16, "sample_representation": "PCM"},
        "transformations": [], "witness_kind": "FORENSIC_AUDIO_WITNESS"}
    return {"schema": "ave.auditory-observation.v1", "backend_identity": copy.deepcopy(IDENTITY),
        "observer_type": "model_audio", "stage": 1, "source_sha256": source_sha256, "stream_index": 0,
        "source_interval_seconds": interval, "clip_sha256": clip_hash, "duration_seconds": 1.,
        "source_clock_uncertainty_seconds": 0., "clip_to_source_mapping": mapping, "source_binding_receipt": binding,
        "source_observations": [{**parsed["observations"][0], "source_start_s": index+.1, "source_end_s": index+.8,
            "source_sha256": source_sha256, "stream_index": 0}],
        "raw_response": json.dumps(parsed), "parsed": parsed, "validation_status": "VALID",
        "provider_receipt": None,
        "admission_dimensions": {"lane": "EXPERIMENTAL", "task_competence": "UNQUALIFIED"},
        "observation_lane": "EXPERIMENTAL", "task_capability_status": "UNQUALIFIED",
        "task_profile": "SPEECH_DELIVERY", "media_category": "fictional-dialogue-contract"}


def qualification_config(root, *, count=20, source_kind="NATURAL_AUDIO", approve=True, identity=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity = identity or IDENTITY
    dataset = {"schema": "ave.auditory-benchmark-dataset.v1", "dataset_id": "manufactured-reference-contract",
        "revision": "unit-v1", "task": "SPEECH_DELIVERY", "media_category": "fictional-dialogue-contract",
        "source_kind": source_kind, "scope_description": "Slowing within bounded fictional dialogue; a simulated contract only",
        "reference_method": "Independent source-first reference and separate output evaluator declarations",
        "limitations": ["Manufactured unit-test records do not qualify any actual model"], "examples": []}
    trials = []
    for index in range(count):
        expected = "PRESENT" if index < 8 else "ABSENT" if index < 16 else "ABSTAIN"
        control = {"PRESENT": "POSITIVE", "ABSENT": "NEGATIVE", "ABSTAIN": "AMBIGUOUS"}[expected]
        eid = "case-%02d" % index
        obs = observation(index)
        obs["backend_identity"] = copy.deepcopy(identity)
        opath = root/(eid+"-observation.json")
        write_json(opath, obs)
        example = {"id": eid, "source_sha256": obs["source_sha256"], "stream_index": 0,
            "interval_seconds": obs["source_interval_seconds"], "split": "held_out", "control": control,
            "expected": expected, "reference_votes": [{"reviewer": "Reference preparer", "label": expected,
            "basis": "Manufactured independent reference contract"}], "reference_provenance": "Generated unit-test declaration, not real media"}
        dataset["examples"].append(example)
        rpath = root/(eid+"-review.json")
        write_json(rpath, {"schema": "ave.auditory-benchmark-review.v1", "example_id": eid,
            "observation_sha256": sha256(opath), "reviewer": "Separate output evaluator", "reference_independent": True,
            "blinded_input_declared": True, "predicted_label": expected, "reason": "Manufactured scoring contract"})
        trials.append({"example_id": eid, "observation": {"path": opath.name, "sha256": sha256(opath)},
                       "review": {"path": rpath.name, "sha256": sha256(rpath)}})
    config = {"dataset": dataset, "trials": trials, "backend_identity": copy.deepcopy(identity),
              "configuration_revision": "fixture-config-v1"}
    if approve:
        config["scope_approval"] = {"reviewer": "Scope reviewer", "basis": "Manufactured qualification review contract",
            "dataset_sha256": canonical_digest(dataset), "task": "SPEECH_DELIVERY",
            "media_category": dataset["media_category"], "configuration_revision": "fixture-config-v1"}
    path = root/"benchmark-config.json"
    write_json(path, config)
    return path


class TaskQualification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-qualification-contract-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, **options):
        config = qualification_config(self.root/"input", **options)
        benchmark(config, self.root/"qualification")
        return self.root/"qualification"

    def test_qualification_is_recomputed_from_exact_records(self):
        record = load_qualification(self.build())
        self.assertEqual(record["status"], "QUALIFIED_FOR_SCOPE")
        self.assertEqual(record["metrics"]["trials"], 20)
        self.assertFalse(record["global_qualification"])
        self.assertFalse(record["authorizes_submission"])

    def test_delivery_never_inherits_lexical_or_word_timing(self):
        path = self.build()
        self.assertEqual(competence("SPEECH_PERFORMANCE"), "SPEECH_DELIVERY")
        for task in ("LEXICAL_TRANSCRIPTION", "EXACT_WORD_TIMING", "PERFORMANCE_MUSIC"):
            with self.assertRaises(AVError):
                load_qualification(path, task=task)

    def test_exact_model_route_adapter_prompt_config_binding(self):
        path = self.build()
        for key in ("model_revision", "api_route", "adapter_revision", "prompt_revision", "configuration_revision"):
            identity = copy.deepcopy(IDENTITY); identity[key] += "-changed"
            with self.assertRaises(AVError):
                load_qualification(path, identity=identity)
        with self.assertRaises(AVError):
            load_qualification(path, media_category="unrelated-action-effects")

    def test_untrusted_status_config_cannot_self_qualify(self):
        path = self.root/"self-declared.json"
        write_json(path, {"schema": "ave.auditory-task-qualification.v1", "status": "QUALIFIED_FOR_SCOPE"})
        with self.assertRaises((AVError, FileNotFoundError)):
            load_qualification(path)

    def test_small_reference_sample_is_provisional(self):
        record = load_qualification(self.build(count=4), required=False)
        self.assertEqual(record["status"], "PROVISIONAL")

    def test_generated_reference_set_cannot_qualify_real_tasks(self):
        record = load_qualification(self.build(source_kind="GENERATED"), required=False)
        self.assertEqual(record["status"], "PROVISIONAL")

    def test_mock_observer_cannot_earn_perceptual_qualification(self):
        identity = copy.deepcopy(IDENTITY); identity["execution_mode"] = "TEST_DOUBLE"
        record = load_qualification(self.build(identity=identity), required=False)
        self.assertEqual(record["status"], "PROVISIONAL")

    def test_benchmark_validation_needs_separate_scope_admission(self):
        record = load_qualification(self.build(approve=False), required=False)
        self.assertEqual(record["status"], "VALIDATED_ON_BENCHMARK")

    def test_human_disagreement_preserved_as_ambiguous(self):
        config = qualification_config(self.root/"input")
        value = read_json(config)
        row = value["dataset"]["examples"][-1]
        row["reference_votes"].append({"reviewer": "Disagreeing reference reviewer", "label": "PRESENT", "basis": "Ambiguous cue"})
        value["scope_approval"]["dataset_sha256"] = canonical_digest(value["dataset"])
        second = self.root/"disagreement.json"
        # Rebase immutable original trial paths explicitly.
        for trial in value["trials"]:
            for key in ("observation", "review"):
                trial[key]["path"] = str(config.parent/trial[key]["path"])
        write_json(second, value)
        benchmark(second, self.root/"qualification")
        record = load_qualification(self.root/"qualification")
        self.assertEqual(len(record["results"][-1]["reference_votes"]), 2)

    def test_repeated_source_interval_cannot_inflate_sample(self):
        value = read_json(qualification_config(self.root/"input"))["dataset"]
        value["examples"][1].update(source_sha256=value["examples"][0]["source_sha256"], interval_seconds=[0., 1.])
        with self.assertRaises(AVError):
            validate_dataset(value)

    def test_experimental_output_alone_is_not_adequate(self):
        folder = self.root/"collection"; folder.mkdir()
        write_json(folder/"observation.json", observation())
        finish_run(folder, "generated-record-test", [])
        with self.assertRaisesRegex(AVError, "Experimental"):
            validate_model_observation(folder/"observation.json")
        _, covered, _ = validate_model_observation(folder/"observation.json", allow_experimental=True)
        self.assertEqual(covered, [[.1, .8]])

    def run_bound_config(self, kind, *, nested=False):
        config = qualification_config(self.root/(kind+"-inputs"))
        outer = self.root/(kind+"-immutable-run")
        outer.mkdir()
        run = outer/"inner" if nested else outer
        if nested:
            run.mkdir()
        if kind == "configuration":
            shutil.copytree(config.parent, run/"inputs")
            new_config = run/"inputs"/config.name
        else:
            value = read_json(config)
            trial = value["trials"][0]
            original = config.parent/trial[kind]["path"]
            copied = run/"records"/original.name
            copied.parent.mkdir()
            shutil.copyfile(original, copied)
            for row in value["trials"]:
                for key in ("observation", "review"):
                    row[key]["path"] = str(config.parent/row[key]["path"])
            trial[kind]["path"] = str(copied)
            new_config = self.root/(kind+"-bound-config.json")
            write_json(new_config, value)
        finish_run(run, "manufactured-benchmark-input-run", [])
        if nested:
            finish_run(outer, "manufactured-outer-input-run", [])
        verify_run(outer)
        return new_config, outer, run

    def test_benchmark_cannot_publish_inside_any_immutable_input_run(self):
        for kind in ("observation", "review", "configuration"):
            with self.subTest(kind=kind):
                config, outer, run = self.run_bound_config(kind)
                original = (run/"run.json").read_bytes()
                with self.assertRaisesRegex(AVError, "inside an input"):
                    benchmark(config, run/"qualification")
                self.assertEqual((run/"run.json").read_bytes(), original)
                self.assertFalse((run/"qualification").exists())
                self.assertFalse(list(run.glob('.qualification.staging-*')))
                verify_run(outer)

    def test_benchmark_protects_outer_run_even_when_input_is_in_nested_run(self):
        config, outer, inner = self.run_bound_config("observation", nested=True)
        with self.assertRaisesRegex(AVError, "inside an input"):
            benchmark(config, outer/"qualification")
        verify_run(outer)
        verify_run(inner)

    def test_benchmark_rejects_corrupted_containing_input_run(self):
        config, outer, run = self.run_bound_config("review")
        (run/"unlisted-file.txt").write_text("Changes membership of the generated test run", encoding="utf-8")
        with self.assertRaises(AVError):
            benchmark(config, self.root/"qualification")
        self.assertFalse((self.root/"qualification").exists())

    def test_benchmark_outside_input_run_preserves_and_binds_input_manifest(self):
        config, outer, run = self.run_bound_config("observation", nested=True)
        before = {str(p.relative_to(outer)): sha256(p) for p in outer.rglob('*') if p.is_file()}
        output = self.root/"qualification"
        benchmark(config, output)
        self.assertEqual(load_qualification(output)["status"], "QUALIFIED_FOR_SCOPE")
        self.assertEqual(before, {str(p.relative_to(outer)): sha256(p) for p in outer.rglob('*') if p.is_file()})
        verify_run(outer)
        sources = {r["path"] for r in read_json(output/"run.json")["sources"]}
        self.assertTrue({str((folder/"run.json").resolve()) for folder in (outer, run)} <= sources)

    def timing_config(self, task, error):
        config = qualification_config(self.root/"timing-input")
        value = read_json(config)
        value["dataset"]["task"] = task
        value["scope_approval"].update(task=task, dataset_sha256=canonical_digest(value["dataset"]))
        for trial in value["trials"]:
            opath = config.parent/trial["observation"]["path"]
            obs = read_json(opath); obs["task_profile"] = task; rewrite_fixture(opath, obs)
            trial["observation"]["sha256"] = sha256(opath)
            rpath = config.parent/trial["review"]["path"]
            review = read_json(rpath); review["observation_sha256"] = sha256(opath)
            if error is not None:
                review["timing_error_seconds"] = error
            rewrite_fixture(rpath, review); trial["review"]["sha256"] = sha256(rpath)
        rewrite_fixture(config, value)
        return config

    def test_timing_labels_without_localization_error_cannot_qualify(self):
        benchmark(self.timing_config("AUDITORY_EVENT_TIMING", None), self.root/"qualification")
        record = load_qualification(self.root/"qualification", required=False)
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["metrics"]["timing_failures"], 8)

    def test_word_timing_uses_a_distinct_stricter_tolerance(self):
        benchmark(self.timing_config("EXACT_WORD_TIMING", .03), self.root/"qualification")
        record = load_qualification(self.root/"qualification", required=False)
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["criteria"]["maximum_positive_timing_error_seconds"], .02)

    def test_bounded_event_timing_can_qualify_only_its_task(self):
        benchmark(self.timing_config("AUDITORY_EVENT_TIMING", .04), self.root/"qualification")
        record = load_qualification(self.root/"qualification")
        self.assertEqual(record["task"], "AUDITORY_EVENT_TIMING")
        with self.assertRaises(AVError):
            load_qualification(self.root/"qualification", task="EXACT_WORD_TIMING")

    def malformed_trial(self, *, damage_binding=False):
        config = qualification_config(self.root/"malformed-input")
        value = read_json(config); trial = value["trials"][0]
        opath = config.parent/trial["observation"]["path"]
        obs = read_json(opath); obs.update(raw_response="not JSON", parsed=None, validation_status="INVALID")
        if damage_binding:
            obs["source_binding_receipt"]["clip_sha256"] = "0"*64
        rewrite_fixture(opath, obs); trial["observation"]["sha256"] = sha256(opath)
        rpath = config.parent/trial["review"]["path"]
        review = read_json(rpath); review["observation_sha256"] = sha256(opath)
        rewrite_fixture(rpath, review); trial["review"]["sha256"] = sha256(rpath)
        rewrite_fixture(config, value)
        benchmark(config, self.root/"qualification")
        return load_qualification(self.root/"qualification", required=False)

    def test_malformed_output_counts_as_failure_with_collection_integrity(self):
        record = self.malformed_trial()
        self.assertEqual(record["metrics"]["invalid_outputs"], 1)
        self.assertEqual(record["metrics"]["correct"], 19)
        self.assertTrue(record["results"][0]["collection_verified"])

    def test_invalid_output_cannot_hide_an_unverified_source_trial(self):
        record = self.malformed_trial(damage_binding=True)
        self.assertEqual(record["status"], "PROVISIONAL")
        self.assertFalse(record["results"][0]["actual_observer"])

    def diluted_control_config(self, name, control, *, invalid=True, task="SPEECH_DELIVERY", timing_error=None,
                               failure_count=None, balanced=False):
        config = qualification_config(self.root/name, count=40)
        value = read_json(config)
        value["dataset"]["task"] = task
        selected = []
        for index, (example, trial) in enumerate(zip(value["dataset"]["examples"], value["trials"])):
            if balanced:
                label = "PRESENT" if index < 10 else "ABSENT" if index < 20 else "ABSTAIN"
            elif control == "AMBIGUOUS":
                label = "PRESENT" if index < 8 else "ABSTAIN" if 16 <= index < 18 else "ABSENT"
            else:
                label = example["expected"]
            example["expected"] = label
            example["control"] = {"PRESENT": "POSITIVE", "ABSENT": "NEGATIVE", "ABSTAIN": "AMBIGUOUS"}[label]
            example["reference_votes"][0]["label"] = label
            opath = config.parent/trial["observation"]["path"]
            rpath = config.parent/trial["review"]["path"]
            obs, review = read_json(opath), read_json(rpath)
            obs["task_profile"] = task
            review["predicted_label"] = label
            failing = example["control"] == control and (failure_count is None or len(selected) < failure_count)
            if failing:
                selected.append(index)
                if invalid:
                    obs.update(raw_response="not JSON", parsed=None, validation_status="INVALID")
                elif task in {"AUDITORY_EVENT_TIMING", "EXACT_WORD_TIMING"}:
                    if timing_error is not None:
                        review["timing_error_seconds"] = timing_error
                else:
                    review["predicted_label"] = "PRESENT" if control == "AMBIGUOUS" else "ABSTAIN"
            elif task in {"AUDITORY_EVENT_TIMING", "EXACT_WORD_TIMING"} and example["control"] == "POSITIVE":
                review["timing_error_seconds"] = 0.
            rewrite_fixture(opath, obs); trial["observation"]["sha256"] = sha256(opath)
            review["observation_sha256"] = sha256(opath)
            rewrite_fixture(rpath, review); trial["review"]["sha256"] = sha256(rpath)
        value["scope_approval"].update(dataset_sha256=canonical_digest(value["dataset"]), task=task)
        rewrite_fixture(config, value)
        return config, selected

    def scored_diluted_control(self, name, control, **options):
        config, selected = self.diluted_control_config(name, control, **options)
        output = self.root/(name+"-proof")
        benchmark(config, output)
        record = load_qualification(output, required=False)
        self.assertTrue(all(record["results"][index]["collection_verified"] for index in selected))
        return record, output

    def test_invalid_positive_trials_count_as_false_negatives_without_dilution(self):
        record, output = self.scored_diluted_control("invalid-positives", "POSITIVE")
        self.assertEqual(record["metrics"]["overall_failure_rate"], .2)
        self.assertEqual(record["metrics"]["invalid_outputs"], 8)
        self.assertEqual(record["metrics"]["false_negatives"], 8)
        self.assertEqual(record["metrics"]["false_negative_rate"], 1.)
        self.assertEqual(record["status"], "FAILED")
        with self.assertRaises(AVError):
            load_qualification(output)

    def test_failed_positive_localization_counts_as_false_negative_without_dilution(self):
        for task, error in (("AUDITORY_EVENT_TIMING", None), ("AUDITORY_EVENT_TIMING", .06),
                            ("EXACT_WORD_TIMING", None), ("EXACT_WORD_TIMING", .03)):
            with self.subTest(task=task, error=error):
                record, _ = self.scored_diluted_control(task+str(error), "POSITIVE", invalid=False,
                    task=task, timing_error=error)
                self.assertEqual(record["metrics"]["overall_failure_rate"], .2)
                self.assertEqual(record["metrics"]["timing_failures"], 8)
                self.assertEqual(record["metrics"]["invalid_outputs"], 0)
                self.assertEqual(record["metrics"]["false_negatives"], 8)
                self.assertEqual(record["metrics"]["false_negative_rate"], 1.)
                self.assertEqual(record["status"], "FAILED")

    def test_failed_negative_controls_cannot_be_diluted_by_other_examples(self):
        for invalid in (True, False):
            with self.subTest(invalid=invalid):
                record, _ = self.scored_diluted_control("negative"+str(invalid), "NEGATIVE", invalid=invalid)
                self.assertEqual(record["metrics"]["overall_failure_rate"], .2)
                self.assertEqual(record["metrics"]["false_positives"], 0)
                self.assertEqual(record["status"], "FAILED")
                self.assertEqual(record["metrics"]["control_failure_rates"]["NEGATIVE"], 1.)

    def test_failed_ambiguous_controls_cannot_be_diluted_by_other_examples(self):
        for invalid in (True, False):
            with self.subTest(invalid=invalid):
                record, _ = self.scored_diluted_control("ambiguous"+str(invalid), "AMBIGUOUS", invalid=invalid)
                self.assertEqual(record["metrics"]["overall_failure_rate"], .05)
                self.assertEqual(record["status"], "FAILED")
                self.assertEqual(record["metrics"]["control_failure_rates"]["AMBIGUOUS"], 1.)

    def test_control_failure_threshold_preserves_passing_boundary_and_rejects_excess(self):
        for control, total in (("POSITIVE", 10), ("NEGATIVE", 10), ("AMBIGUOUS", 20)):
            for failures in (total//5+1, total//5):
                with self.subTest(control=control, failures=failures):
                    record, _ = self.scored_diluted_control(control+str(failures), control, balanced=True,
                        failure_count=failures)
                    expected = "QUALIFIED_FOR_SCOPE" if failures/total <= .2 else "FAILED"
                    self.assertEqual(record["status"], expected)
                    self.assertEqual(record["metrics"].get("control_failure_rates", {}).get(control), failures/total)

    def test_earlier_scoring_revision_requires_new_proof_without_rewriting_archive(self):
        original = self.build()
        archive = self.root/"manufactured-v1-archive"
        shutil.copytree(original, archive, ignore=shutil.ignore_patterns("run.json", "commands.json"))
        legacy = read_json(archive/"qualification.json")
        legacy["revision"] = "auditory-qualification-v1"
        legacy["criteria"].pop("maximum_control_failure_rate", None)
        for key in ("control_failures", "control_failure_rates"):
            legacy["metrics"].pop(key, None)
        legacy["scoring_method"] = "Independent reviewed PRESENT/ABSENT/ABSTAIN; invalids and missing/out-of-tolerance positive timing included as failures"
        rewrite_fixture(archive/"qualification.json", legacy)
        finish_run(archive, "manufactured-archived-scoring-contract", [])
        before = {p.relative_to(archive).as_posix(): sha256(p) for p in archive.rglob('*') if p.is_file()}
        for required in (False, True):
            with self.subTest(required=required), self.assertRaisesRegex(AVError, "earlier scoring revision"):
                load_qualification(archive, required=required)
        self.assertEqual(read_json(archive/"qualification.json")["status"], "QUALIFIED_FOR_SCOPE")
        import jsonschema
        jsonschema.validate(legacy, read_json(Path(__file__).resolve().parents[1]/"schemas/auditory-qualification.schema.json"))
        self.assertEqual(before, {p.relative_to(archive).as_posix(): sha256(p) for p in archive.rglob('*') if p.is_file()})
        verify_run(archive)
        benchmark(self.root/"input/benchmark-config.json", self.root/"recomputed-new-proof")
        self.assertEqual(load_qualification(self.root/"recomputed-new-proof")["revision"], "auditory-qualification-v2")

    def test_benchmark_dataset_review_and_qualification_public_schemas(self):
        import jsonschema
        path = self.build()
        snapshot = read_json(path/"benchmark-snapshot.json")
        schemas = Path(__file__).resolve().parents[1]/"schemas"
        for name, value in (("auditory-benchmark-dataset", snapshot["dataset"]),
                            ("auditory-benchmark-review", snapshot["trials"][0]["review"]),
                            ("auditory-qualification", load_qualification(path))):
            jsonschema.validate(value, read_json(schemas/(name+".schema.json")))
