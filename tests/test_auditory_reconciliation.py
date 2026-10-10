"""Generated graph/receipt conformance; no actual perceptual qualification."""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.auditory_qualification import benchmark, load_qualification
from avevidence.common import AVError, finish_run, read_json, sha256, write_json
from avevidence.cross_modal import reconcile_data, reconcile_scene
from avevidence.inventory import verify_run
from avevidence.observation_dependencies import propagate_changes, validate_graph
from avevidence.scene_packets import load_scene_packet, validate_packet, verify_scene_snapshot
from avevidence.scene_delta import scene_claim_delta
from test_auditory_qualification import qualification_config, observation
import test_scene_events as _scene


class AuditoryReconciliation(unittest.TestCase):
    setUp = _scene.SceneEvents.setUp
    tearDown = _scene.SceneEvents.tearDown
    _wav = staticmethod(_scene.SceneEvents._wav)
    ref = _scene.SceneEvents.ref
    obs = staticmethod(_scene.SceneEvents.obs)
    claim = staticmethod(_scene.SceneEvents.claim)
    edge = staticmethod(_scene.SceneEvents.edge)
    packet = _scene.SceneEvents.packet
    save = _scene.SceneEvents.save
    reconciled = _scene.SceneEvents.reconciled
    decide = staticmethod(_scene.SceneEvents.decide)
    static_packet = _scene.SceneEvents.static_packet

    @classmethod
    def setUpClass(cls):
        cls.qual_temp = tempfile.TemporaryDirectory(prefix="ave-reconciliation-qualification-")
        cls.qual_root = Path(cls.qual_temp.name)
        config = qualification_config(cls.qual_root/"inputs")
        benchmark(config, cls.qual_root/"qualification")

    @classmethod
    def tearDownClass(cls):
        cls.qual_temp.cleanup()

    def auditory(self, *, source_sha256=None, test_double=False):
        raw = observation(source_sha256=source_sha256 or self.source_hash)
        # Keep the manufactured report, review and packet wording identical.
        raw["parsed"]["observations"][0]["description"] = "The pace slows."
        raw["source_observations"][0]["description"] = "The pace slows."
        raw["raw_response"] = __import__('json').dumps(raw["parsed"])
        if test_double:
            raw["backend_identity"]["execution_mode"] = "TEST_DOUBLE"
        folder = self.root/"new-auditory"; folder.mkdir()
        path = folder/"observation.json"
        write_json(path, raw); finish_run(folder, "manufactured-observer-record", [])
        review = self.root/"scoped-review.json"
        write_json(review, {"schema": "ave.auditory-observation-assessment.v1", "observation_sha256": sha256(path),
            "observation_index": 0, "proposition": "delivery", "interval_seconds": [.2, .7],
            "reviewer": "Manufactured contract reviewer", "scope": "This slowing observation only",
            "statement": "The pace slows.", "adequacy": "ELIGIBLE_FOR_SUPPORT", "unresolved_contradictions": [],
            "reason": "Generated eligibility contract, not a real listening declaration",
            "qualification_artifact": {"path": str(self.qual_root/"qualification"/"qualification.json"),
                "sha256": sha256(self.qual_root/"qualification"/"qualification.json")}})
        return {"id": "audio-new", "artifact": {"path": str(path), "sha256": sha256(path)},
            "locator": {"source_sha256": raw["source_sha256"], "stream_index": 0,
                "clock": "original_pts_minus_source_origin", "intervals_seconds": [[0., 1.]]},
            "record_pointer": "/source_observations/0", "review_artifact": {"path": review.name, "sha256": sha256(review)}}

    def delivery_packet(self, **options):
        packet = self.packet()
        packet["evidence"] = {"AO_STAGE1": [self.auditory(**options)]}
        packet["observations"] = [self.obs("o-delivery", "delivery", "audio-new", status="SUPPORTED",
            interval=[.2, .7], statement="The pace slows.")]
        packet["claims"] = [self.claim("c-delivery", "delivery", "o-delivery", status="SUPPORTED", interval=[.2, .7])]
        packet["dependencies"] = [self.edge("o-delivery", "c-delivery")]
        self.decide(packet, {"o-delivery": "SUPPORTED", "c-delivery": "SUPPORTED"})
        return packet

    @staticmethod
    def route(essential, *, supplementary=(), interchangeable=()):
        return {"id": "declared-proof", "scope": "Only the stated evidence route supports this formulation",
            "essential": list(essential), "interchangeable": list(interchangeable),
            "supplementary": list(supplementary), "corroborating": [], "insufficient_alone": []}

    def test_scoped_delivery_can_support_without_source_fact_promotion(self):
        result = self.reconciled(self.delivery_packet())
        assessment = result["observations"]["o-delivery"]["evidence_assessments"][0]
        self.assertEqual(assessment["admission_status"], "ELIGIBLE_FOR_SUPPORT")
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
        self.assertFalse(assessment["global_qualification"])
        self.assertEqual(result["source_fact_promotion"], "NONE_TOOLKIT_DOES_NOT_ASSIGN_PROJECT_F-AUD")

    def test_eligible_observation_still_needs_explicit_adjudication(self):
        packet = self.delivery_packet(); packet["adjudication"] = {"decisions": {}}
        result = self.reconciled(packet)
        self.assertTrue(result["observations"]["o-delivery"]["adequate_for_support"])
        self.assertEqual(result["claims"][0]["state"], "RECHECK")

    def test_test_double_never_becomes_perceptual_support(self):
        result = self.reconciled(self.delivery_packet(test_double=True))
        self.assertFalse(result["observations"]["o-delivery"]["adequate_for_support"])
        self.assertEqual(result["claims"][0]["state"], "RECHECK")

    def test_missing_individual_review_leaves_qualified_task_provisional(self):
        packet = self.delivery_packet(); del packet["evidence"]["AO_STAGE1"][0]["review_artifact"]
        result = self.reconciled(packet)
        self.assertFalse(result["observations"]["o-delivery"]["adequate_for_support"])

    def test_qualification_cannot_establish_emotion_directly(self):
        packet = self.delivery_packet()
        packet["observations"][0]["proposition"] = "emotion"
        packet["claims"][0]["proposition"] = "emotion"
        packet["claims"][0]["interpretation"] = "Manufactured emotional claim"
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["state"], "RECHECK")

    def test_unresolved_individual_contradiction_blocks_eligibility(self):
        packet = self.delivery_packet()
        path = self.root/"scoped-review.json"
        value = read_json(path); value["unresolved_contradictions"] = ["The reported slowing is disputed"]
        path.write_text(__import__('json').dumps(value), encoding="utf-8")
        packet["evidence"]["AO_STAGE1"][0]["review_artifact"]["sha256"] = sha256(path)
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["state"], "RECHECK")

    def test_unqualified_optional_audio_preserves_text_image_route(self):
        packet = self.static_packet()
        source_hash = packet["source"]["sha256"]
        textual = self.ref("txt", self.text, authority="canonical_text", interval=[.2, .8])
        textual["locator"]["source_sha256"] = source_hash
        packet["evidence"]["TXT"] = [textual]
        audio = self.auditory(source_sha256=source_hash)
        del audio["review_artifact"]
        packet["evidence"]["AO_STAGE1"] = [audio]
        packet["observations"].extend([self.obs("o-text", "wording", "txt", status="SUPPORTED"),
            self.obs("o-audio", "delivery", "audio-new", status="PROVISIONAL", interval=[.2, .7])])
        packet["claims"] = [{"id": "interpretation", "proposition": "interpretation", "statement": "The image and text exaggerate the reaction.",
            "status": "SUPPORTED", "observation_ids": ["o-static", "o-text", "o-audio"],
            "interpretation": "Generated text-image inference; optional audio is supplementary",
            "support_routes": [self.route(["o-static", "o-text"], supplementary=["o-audio"])]}]
        packet["dependencies"] = [self.edge(key, "interpretation") for key in ("o-static", "o-text")]
        packet["dependencies"].append({**self.edge("o-audio", "interpretation"), "role": "SUPPORTING"})
        self.decide(packet, {"o-static": "SUPPORTED", "o-text": "SUPPORTED", "interpretation": "SUPPORTED"})
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
        self.assertEqual(result["claims"][0]["support_routes"][0]["status"], "SURVIVES")

    def test_visual_only_cannot_close_av_synchronization(self):
        packet = self.static_packet()
        claim = packet["claims"][0]
        claim.update(proposition="av_sync", support_routes=[self.route(["o-static"])])
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["state"], "OPEN")
        self.assertEqual(result["claims"][0]["support_routes"][0]["status"], "OPEN")

    def test_interchangeable_direct_route_uses_full_coverage_in_either_order(self):
        packet = self.packet()
        packet["evidence"] = {"TXT": [self.ref("txt", self.text, authority="canonical_text", interval=[.2, .8])]}
        packet["observations"] = [self.obs("short", "wording", "txt", status="SUPPORTED", interval=[.2, .5]),
            self.obs("full", "wording", "txt", status="SUPPORTED", interval=[.2, .8])]
        packet["claims"] = [self.claim("wording", "wording", "short", status="SUPPORTED", interval=[.2, .8],
            support_routes=[self.route([], interchangeable=[["short", "full"]])])]
        packet["claims"][0]["observation_ids"] = ["short", "full"]
        packet["dependencies"] = [{**self.edge(key, "wording"), "role": "SUPPORTING"} for key in ("short", "full")]
        self.decide(packet, {key: "SUPPORTED" for key in ("short", "full", "wording")})
        for ordering in (["short", "full"], ["full", "short"]):
            with self.subTest(order=ordering):
                packet["claims"][0]["support_routes"][0]["interchangeable"] = [ordering]
                result = self.reconciled(packet, name="interchangeable-"+"-".join(ordering)+".json")
                self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
                self.assertEqual(result["claims"][0]["support_routes"][0]["selected_essential"], ["full"])
        packet["adjudication"]["decisions"]["full"]["state"] = "OPEN"
        self.assertEqual(self.reconciled(packet, name="interchangeable-unresolved.json")["claims"][0]["state"], "OPEN")

    def route_selection(self, proposition, groups, kinds, *, temporal=(), intervals=None, insufficient=()):
        # These are already-assessed contract fixtures, not perceptual evidence.
        from avevidence.support_routes import assess_routes
        route = self.route([], interchangeable=groups)
        route["insufficient_alone"] = list(insufficient)
        claim = {"proposition": proposition, "interval_seconds": [.2, .8], "support_routes": [route]}
        observations = {key: {"proposition": kind, "adequate_for_support": True,
            "evidence_assessments": [{"channel": "TVIS" if key in temporal else "TXT", "adequate_for_support": True}]}
            for key, kind in kinds.items()}
        overlay = {key: {"after": "SUPPORTED"} for key in kinds}
        packet = {"interval_seconds": [0., 1.], "observations": [{"id": key,
            "interval_seconds": (intervals or {}).get(key, [.2, .8])} for key in kinds]}
        return assess_routes(claim, observations, overlay, packet, {})[0]

    def test_interchangeable_direct_route_selects_competent_candidate_in_either_order(self):
        for order in (["other", "word"], ["word", "other"]):
            with self.subTest(order=order):
                row = self.route_selection("wording", [order], {"other": "delivery", "word": "wording"})
                self.assertEqual(row["status"], "SURVIVES")
                self.assertEqual(row["selected_essential"], ["word"])
        self.assertEqual(self.route_selection("wording", [["other"]], {"other": "delivery"})["status"], "OPEN")

    def test_interchangeable_av_route_combines_required_modalities_across_groups(self):
        kinds = {"v1": "timing", "a1": "auditory_event_timing", "v2": "timing", "a2": "auditory_event_timing"}
        for groups in ([["v1", "a1"], ["v2", "a2"]], [["a2", "v2"], ["a1", "v1"]]):
            with self.subTest(groups=groups):
                row = self.route_selection("av_sync", groups, kinds, temporal=["v1", "v2"])
                self.assertEqual(row["status"], "SURVIVES")
                self.assertTrue(set(row["selected_essential"]) & {"v1", "v2"})
                self.assertTrue(set(row["selected_essential"]) & {"a1", "a2"})
        row = self.route_selection("av_sync", [["v1"], ["v2"]], kinds, temporal=["v1", "v2"])
        self.assertEqual(row["status"], "OPEN")

    def test_interchangeable_insufficient_alone_candidate_does_not_hide_valid_alternative(self):
        for order in (["alone", "complete"], ["complete", "alone"]):
            with self.subTest(order=order):
                row = self.route_selection("wording", [order], {"alone": "wording", "complete": "wording"}, insufficient=["alone"])
                self.assertEqual(row["status"], "SURVIVES")
                self.assertEqual(row["selected_essential"], ["complete"])
        self.assertEqual(self.route_selection("wording", [["alone"]], {"alone": "wording"}, insufficient=["alone"])["status"], "OPEN")

    def test_interchangeable_route_with_no_complete_candidate_stays_open(self):
        row = self.route_selection("wording", [["first", "second"]], {"first": "wording", "second": "wording"},
            intervals={"first": [.2, .5], "second": [.5, .8]})
        self.assertEqual(row["status"], "OPEN")

    def test_many_interchangeable_groups_find_valid_support_without_cartesian_enumeration(self):
        groups = [[f"other-{i}", f"word-{i}"] for i in range(36)]
        kinds = {key: "wording" if key.startswith("word-") else "delivery" for group in groups for key in group}
        row = self.route_selection("wording", groups, kinds)
        self.assertEqual(row["status"], "SURVIVES")
        self.assertEqual(len(row["selected_essential"]), 36)
        self.assertTrue(any(key.startswith("word-") for key in row["selected_essential"]))

    def test_valid_unqualified_optional_proof_preserves_independent_route(self):
        import json
        base = self.static_packet()
        source_hash = base["source"]["sha256"]
        textual = self.ref("txt", self.text, authority="canonical_text", interval=[.2, .8])
        textual["locator"]["source_sha256"] = source_hash
        base["evidence"]["TXT"] = [textual]
        base["evidence"]["AO_STAGE1"] = [self.auditory(source_sha256=source_hash)]
        base["observations"].extend([self.obs("o-text", "wording", "txt", status="SUPPORTED"),
            self.obs("o-audio", "delivery", "audio-new", interval=[.2, .7], statement="The pace slows.")])
        base["claims"] = [{"id": "interpretation", "proposition": "interpretation", "statement": "A reviewed text-image formulation.",
            "status": "SUPPORTED", "observation_ids": ["o-static", "o-text", "o-audio"],
            "interpretation": "Generated contract with supplementary auditory evidence",
            "support_routes": [self.route(["o-static", "o-text"], supplementary=["o-audio"])]},
            self.claim("required-audio", "delivery", "o-audio", status="SUPPORTED", interval=[.2, .7])]
        base["dependencies"] = [self.edge(key, "interpretation") for key in ("o-static", "o-text")]
        base["dependencies"].extend([{**self.edge("o-audio", "interpretation"), "role": "SUPPORTING"},
                                     self.edge("o-audio", "required-audio")])
        self.decide(base, {"o-static": "SUPPORTED", "o-text": "SUPPORTED", "interpretation": "SUPPORTED", "required-audio": "SUPPORTED"})
        for state in ("PROVISIONAL", "VALIDATED_ON_BENCHMARK", "FAILED", "NOT_TESTED"):
            config = qualification_config(self.root/(state+"-inputs"),
                source_kind="GENERATED" if state == "PROVISIONAL" else "NATURAL_AUDIO",
                approve=state != "VALIDATED_ON_BENCHMARK")
            value = read_json(config)
            if state == "FAILED":
                for trial in value["trials"][8:16]:
                    review_path = config.parent/trial["review"]["path"]
                    review = read_json(review_path); review["predicted_label"] = "PRESENT"
                    review_path.write_text(json.dumps(review), encoding="utf-8")
                    trial["review"]["sha256"] = sha256(review_path)
            elif state == "NOT_TESTED":
                value["trials"] = []
            config.write_text(json.dumps(value), encoding="utf-8")
            proof = self.root/(state+"-proof")
            benchmark(config, proof)
            self.assertEqual(load_qualification(proof, required=False)["status"], state)
            review = read_json(self.root/"scoped-review.json")
            review["qualification_artifact"] = {"path": str(proof/"qualification.json"), "sha256": sha256(proof/"qualification.json")}
            review_path = self.root/(state+"-scoped-review.json")
            write_json(review_path, review)
            packet = copy.deepcopy(base)
            packet["evidence"]["AO_STAGE1"][0]["review_artifact"] = {"path": review_path.name, "sha256": sha256(review_path)}
            result = self.reconciled(packet, state+"-optional-proof.json")
            with self.subTest(state=state):
                assessment = result["observations"]["o-audio"]["evidence_assessments"][0]
                self.assertEqual(assessment["task_capability_status"], state)
                self.assertEqual(assessment["admission_status"], "INADEQUATE")
                self.assertEqual(assessment["authority"], "ATTRIBUTED_UNQUALIFIED_AUDITORY_WITNESS")
                self.assertFalse(result["observations"]["o-audio"]["adequate_for_support"])
                self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
                self.assertNotIn(result["claims"][1]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_required_audio_cannot_be_optionalized_away(self):
        packet = self.delivery_packet()
        packet["claims"][0]["support_routes"] = [self.route([], supplementary=["o-delivery"])]
        with self.assertRaises(AVError):
            validate_packet(packet)

    def test_lexical_correction_rechecks_only_declared_dependents(self):
        packet = self.packet()
        packet["observations"].append(self.obs("o-delivery", "delivery", "txt", status="SUPPORTED"))
        packet["observations"].append(self.obs("o-command", "interpretation", "txt", status="SUPPORTED"))
        packet["claims"].append(self.claim("c-command", "interpretation", "o-command", status="SUPPORTED"))
        packet["dependencies"].extend([self.edge("o-text", "o-command", "DEPENDS_ON"), self.edge("o-command", "c-command")])
        result = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"],
            {"o-text": {"state": "CONTRADICTED", "reason": "A name, not Stop"}})
        self.assertEqual(result["state_overlay"]["o-command"]["after"], "RECHECK")
        self.assertEqual(result["state_overlay"]["c-command"]["after"], "RECHECK")
        self.assertEqual(result["state_overlay"]["o-delivery"]["after"], "SUPPORTED")

    def test_same_model_clip_context_calls_are_dependent(self):
        packet = self.delivery_packet()
        original = packet["evidence"]["AO_STAGE1"][0]
        second = copy.deepcopy(original); second["id"] = "audio-again"
        packet["evidence"]["AO_STAGE1"].append(second)
        packet["observations"].append(self.obs("o-again", "delivery", "audio-again", interval=[.2, .7], status="SUPPORTED", statement="The pace slows."))
        packet["claims"][0]["observation_ids"].append("o-again")
        packet["dependencies"].append(self.edge("o-again", "c-delivery"))
        self.decide(packet, {"o-delivery": "SUPPORTED", "o-again": "SUPPORTED", "c-delivery": "SUPPORTED"})
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["independence"]["agreement_class"], "DEPENDENT_CONSISTENCY")
        self.assertEqual(len(result["claims"][0]["independence"]["groups"]), 1)

    def test_scene_delta_reports_atomic_audio_and_integrated_event(self):
        before = self.delivery_packet()
        # Freeze the earlier experimental observation without rewriting it.
        del before["evidence"]["AO_STAGE1"][0]["review_artifact"]
        before["adjudication"] = {"decisions": {}}
        before_path = self.save(before, "before.json")
        after = copy.deepcopy(before)
        review = self.root/"scoped-review.json"
        after["evidence"]["AO_STAGE1"][0]["review_artifact"] = {"path": review.name, "sha256": sha256(review)}
        after["holistic_analysis"] = "The slowing qualifies the pacing premise; the emotional interpretation remains open."
        self.decide(after, {"o-delivery": "SUPPORTED", "c-delivery": "SUPPORTED"})
        after_path = self.save(after, "after.json")
        scene_claim_delta(before_path, after_path, self.root/"delta")
        result = read_json(self.root/"delta"/"claim-delta.json")
        self.assertEqual(result["claims"][0]["auditory_evidence_delta"][0]["qualification_states"], ["QUALIFIED_FOR_SCOPE"])
        self.assertEqual(result["event_synthesis"]["formulation"], after["holistic_analysis"])
        self.assertTrue(result["raw_records_preserved"])

    def test_supplementary_dependency_cycle_is_still_refused(self):
        packet = self.packet()
        packet["observations"].append(self.obs("o-second", "wording", "txt"))
        packet["dependencies"] = [{**self.edge("o-text", "o-second"), "role": "SUPPORTING"},
                                  {**self.edge("o-second", "o-text"), "role": "SUPPORTING"}]
        with self.assertRaisesRegex(AVError, "Circular"):
            validate_graph(packet["observations"], packet["claims"], packet["dependencies"])

    def test_overlapping_route_classifications_are_refused(self):
        packet = self.delivery_packet()
        packet["claims"][0]["support_routes"] = [self.route(["o-delivery"], supplementary=["o-delivery"])]
        with self.assertRaises(AVError):
            validate_packet(packet)

    def test_nested_qualification_proof_cannot_change_after_assessment(self):
        packet = self.delivery_packet(); loaded = load_scene_packet(self.save(packet))
        reconcile_data(*loaded[:4])
        bindings = loaded[3]
        self.assertIn("qualification_proof", bindings["audio-new"])
        bindings["audio-new"]["qualification_proof"]["manifest_sha256"] = "0"*64
        with self.assertRaisesRegex(AVError, "qualification proof changed"):
            verify_scene_snapshot(loaded[4], bindings)

    def test_unresolved_packet_conflict_blocks_scoped_audio(self):
        packet = self.delivery_packet()
        packet["observations"].append(self.obs("o-dispute", "delivery", "audio-new", status="OPEN",
                                              interval=[.2, .7], statement="The pace slows."))
        packet["conflicts"] = [{"id": "delivery-conflict", "proposition": "delivery",
                                "observation_ids": ["o-delivery", "o-dispute"], "question": "Is the slowing audible?"}]
        result = self.reconciled(packet)
        self.assertFalse(result["observations"]["o-delivery"]["adequate_for_support"])

    def human_delivery(self):
        path = self.root/"human-delivery.json"
        write_json(path, {"schema": "ave.human-listening-observation.v1",
            "actual_audio_inspection_declared": True, "reviewer": "Manufactured human-listening contract",
            "source_sha256": self.source_hash, "stream_index": 0, "proposition": "delivery",
            "observation": "The pace remains constant.", "inspected_intervals_seconds": [[.2, .7]]})
        return self.ref("human", path, channel="HL", interval=[.2, .7])

    def conflict_packet(self, packet):
        original = packet["observations"][0]
        rival = copy.deepcopy(original); rival["id"] = "o-rival"
        rival["statement"] = "An explicitly conflicting alternative to the first observation."
        packet["observations"].append(rival)
        first_claim = packet["claims"][0]
        second_claim = copy.deepcopy(first_claim); second_claim["id"] = "c-rival"
        second_claim["observation_ids"] = ["o-rival"]
        packet["claims"].append(second_claim)
        packet["dependencies"].append(self.edge("o-rival", "c-rival"))
        packet["conflicts"] = [{"id": "unresolved-rivals", "proposition": original["proposition"],
            "observation_ids": [original["id"], "o-rival"], "question": "Which conflicting observation survives?"}]
        for row in packet["observations"]+packet["claims"]:
            row["status"] = "SUPPORTED"
        self.decide(packet, {r["id"]: "SUPPORTED" for r in packet["observations"]+packet["claims"]})
        return packet

    def assert_conflict_gated_and_unique_survivor_supported(self, packet, name):
        original_id = packet["observations"][0]["id"]
        for reverse in (False, True):
            candidate = copy.deepcopy(packet)
            if reverse:
                candidate["observations"].reverse()
            result = self.reconciled(candidate, name+str(reverse)+".json")
            with self.subTest(channel=name, reverse=reverse):
                for key in (original_id, "o-rival"):
                    self.assertFalse(result["observations"][key]["adequate_for_support"])
                    self.assertNotIn(result["dependency_assessment"]["state_overlay"][key]["after"], {"SUPPORTED", "CONFIRMED"})
                for claim in result["claims"]:
                    self.assertNotIn(claim["state"], {"SUPPORTED", "CONFIRMED"})
        resolved = copy.deepcopy(packet)
        resolved["adjudication"]["decisions"]["o-rival"]["state"] = "CONTRADICTED"
        result = self.reconciled(resolved, name+"-resolved.json")
        self.assertTrue(result["observations"][original_id]["adequate_for_support"])
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
        self.assertNotIn(result["claims"][1]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_mixed_human_and_qualified_audio_conflict_gates_both_rivals(self):
        packet = self.conflict_packet(self.delivery_packet())
        packet["evidence"]["HL"] = [self.human_delivery()]
        packet["observations"][1].update(evidence_ids=["human"], statement="The pace remains constant.")
        # The first claim deliberately relies on the human rival: model-only
        # downgrading must not leave a human-backed claim supported.
        packet["claims"][0]["observation_ids"] = ["o-rival"]
        packet["claims"][1]["observation_ids"] = ["o-delivery"]
        packet["dependencies"] = [self.edge("o-rival", "c-delivery"), self.edge("o-delivery", "c-rival")]
        for reverse in (False, True):
            candidate = copy.deepcopy(packet)
            if reverse:
                candidate["observations"].reverse()
            result = self.reconciled(candidate, "mixed-"+str(reverse)+".json")
            with self.subTest(reverse=reverse):
                for key in ("o-delivery", "o-rival"):
                    self.assertFalse(result["observations"][key]["adequate_for_support"])
                self.assertTrue(all(r["state"] not in {"SUPPORTED", "CONFIRMED"} for r in result["claims"]))
        packet["adjudication"]["decisions"]["o-delivery"]["state"] = "CONTRADICTED"
        result = self.reconciled(packet, "mixed-resolved.json")
        self.assertTrue(result["observations"]["o-rival"]["adequate_for_support"])
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")

    def test_unresolved_text_conflict_gates_claims_until_unique_survivor(self):
        packet = self.conflict_packet(self.packet())
        self.assert_conflict_gated_and_unique_survivor_supported(packet, "text-conflict")
        packet["observations"].append(self.obs("o-unrelated", "wording", "txt", status="SUPPORTED"))
        packet["claims"].append(self.claim("c-unrelated", "wording", "o-unrelated", status="SUPPORTED"))
        packet["dependencies"].append(self.edge("o-unrelated", "c-unrelated"))
        self.decide(packet, {r["id"]: "SUPPORTED" for r in packet["observations"]+packet["claims"]})
        result = self.reconciled(packet, "text-conflict-unrelated.json")
        self.assertTrue(result["observations"]["o-unrelated"]["adequate_for_support"])
        self.assertEqual(next(r for r in result["claims"] if r["id"] == "c-unrelated")["state"], "SUPPORTED")

    def test_unresolved_human_conflict_gates_claims_until_unique_survivor(self):
        packet = self.packet()
        packet["evidence"] = {"HL": [self.human_delivery()]}
        packet["observations"] = [self.obs("o-human", "delivery", "human", status="SUPPORTED", interval=[.2, .7])]
        packet["claims"] = [self.claim("c-human", "delivery", "o-human", status="SUPPORTED", interval=[.2, .7])]
        packet["dependencies"] = [self.edge("o-human", "c-human")]
        self.assert_conflict_gated_and_unique_survivor_supported(self.conflict_packet(packet), "human-conflict")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Generated static fixture requires FFmpeg/ffprobe")
    def test_unresolved_visual_conflict_gates_claims_until_unique_survivor(self):
        self.assert_conflict_gated_and_unique_survivor_supported(self.conflict_packet(self.static_packet()), "visual-conflict")

    def test_scoped_assessment_cannot_relabel_atomic_model_statement(self):
        base = self.delivery_packet()
        review_path = self.root/"scoped-review.json"
        review = read_json(review_path)
        review["statement"] = "The delivery accelerates into an angry shout."
        new_review = self.root/"relabelled-review.json"
        write_json(new_review, review)
        for pointer in ("/source_observations/0", "/parsed/observations/0"):
            packet = copy.deepcopy(base)
            ref = packet["evidence"]["AO_STAGE1"][0]
            ref.update(record_pointer=pointer, review_artifact={"path": new_review.name, "sha256": sha256(new_review)})
            packet["observations"][0]["statement"] = review["statement"]
            with self.subTest(pointer=pointer), self.assertRaisesRegex(AVError, "relabel"):
                self.reconciled(packet, "relabel-"+str(pointer.startswith('/parsed'))+".json")
        # An analyst's narrower formulation remains separate from the raw
        # atomic model statement and does not require rewriting that record.
        base["claims"][0]["statement"] = "Within this reviewed interval, the pace decreases."
        self.assertEqual(self.reconciled(base, "matching-statement.json")["claims"][0]["state"], "SUPPORTED")

    def test_multiway_conflict_rejecting_one_rival_does_not_resolve_the_rest(self):
        base = self.delivery_packet()
        unreviewed = copy.deepcopy(base["evidence"]["AO_STAGE1"][0])
        unreviewed["id"] = "unreviewed-rival"
        del unreviewed["review_artifact"]
        base["evidence"]["AO_STAGE1"].append(unreviewed)
        for state in ("OPEN", "RECHECK", "PROVISIONAL", "REPORTED", "SUPPORTED", "CONTRADICTED"):
            for reverse in (False, True):
                packet = copy.deepcopy(base)
                packet["observations"].extend([
                    self.obs("o-rejected", "delivery", "audio-new", status="CONTRADICTED",
                             interval=[.2, .7], statement="The pace slows."),
                    self.obs("o-rival", "delivery", "unreviewed-rival", status=state,
                             interval=[.2, .7], statement="The pace remains constant.")])
                if reverse:
                    packet["observations"].reverse()
                packet["conflicts"] = [{"id": "three-way", "proposition": "delivery",
                    "observation_ids": ["o-delivery", "o-rejected", "o-rival"],
                    "question": "Have all competing delivery readings been adjudicated?"}]
                self.decide(packet, {"o-delivery": "SUPPORTED", "o-rejected": "CONTRADICTED",
                                     "o-rival": state, "c-delivery": "SUPPORTED"})
                result = self.reconciled(packet, state+str(reverse)+".json")
                with self.subTest(state=state, reverse=reverse):
                    if state == "CONTRADICTED":
                        self.assertTrue(result["observations"]["o-delivery"]["adequate_for_support"])
                        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
                    else:
                        self.assertFalse(result["observations"]["o-delivery"]["adequate_for_support"])
                        self.assertNotIn(result["claims"][0]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_same_model_different_clips_and_prompts_remain_dependent(self):
        base = self.delivery_packet()
        declaration = {"reviewer": "Separate fixture reviewer", "basis": "Independently declared preparation",
                       "upstream_evidence_ids": []}
        base["evidence"]["AO_STAGE1"][0]["independence_declaration"] = declaration
        for same_model in (True, False):
            packet = copy.deepcopy(base)
            raw = observation(1, source_sha256=self.source_hash)
            raw["backend_identity"]["prompt_revision"] = "different-neutral-prompt"
            raw["backend_identity"]["configuration_revision"] = "different-config"
            if not same_model:
                raw["backend_identity"]["model_revision"] = "another-fixture-model"
            folder = self.root/("different-clip-"+str(same_model)); folder.mkdir()
            path = folder/"observation.json"
            write_json(path, raw); finish_run(folder, "manufactured-observer-record", [])
            packet["evidence"]["AO_STAGE1"].append({"id": "different-clip",
                "artifact": {"path": str(path), "sha256": sha256(path)},
                "locator": {"source_sha256": self.source_hash, "stream_index": 0,
                    "clock": "original_pts_minus_source_origin", "intervals_seconds": [[1., 2.]]},
                "independence_declaration": copy.deepcopy(declaration)})
            packet["observations"].append(self.obs("o-other-clip", "delivery", "different-clip", interval=[1.1, 1.8]))
            packet["claims"][0]["observation_ids"].append("o-other-clip")
            packet["dependencies"].append(self.edge("o-other-clip", "c-delivery"))
            result = self.reconciled(packet, "clips-"+str(same_model)+".json")
            assessment = result["claims"][0]["independence"]
            with self.subTest(same_model=same_model):
                self.assertEqual(assessment["agreement_class"],
                                 "DEPENDENT_CONSISTENCY" if same_model else "INDEPENDENT_CORROBORATION")
                self.assertEqual(len(assessment["groups"]), 1 if same_model else 2)

    def test_conflict_rival_with_unresolved_material_prerequisite_stays_unresolved(self):
        packet = self.delivery_packet()
        packet["evidence"]["TXT"] = [self.ref("pending-text", self.text)]
        packet["observations"].extend([
            self.obs("o-rejected", "delivery", "audio-new", status="CONTRADICTED",
                     interval=[.2, .7], statement="The pace slows."),
            self.obs("o-rival", "delivery", "audio-new", status="SUPPORTED",
                     interval=[.2, .7], statement="The pace slows."),
            self.obs("o-premise", "wording", "pending-text", status="OPEN")])
        packet["dependencies"].append(self.edge("o-premise", "o-rival", "DEPENDS_ON"))
        packet["conflicts"] = [{"id": "dependent-rival", "proposition": "delivery",
            "observation_ids": ["o-delivery", "o-rejected", "o-rival"],
            "question": "Does an affirmative rival still depend on unresolved wording?"}]
        self.decide(packet, {"o-delivery": "SUPPORTED", "o-rejected": "CONTRADICTED",
                             "o-rival": "SUPPORTED", "o-premise": "OPEN", "c-delivery": "SUPPORTED"})
        result = self.reconciled(packet)
        self.assertFalse(result["observations"]["o-delivery"]["adequate_for_support"])
        self.assertNotIn(result["claims"][0]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_conflict_with_two_adequate_affirmative_rivals_stays_open(self):
        base = self.delivery_packet()
        raw = observation(source_sha256=self.source_hash)
        raw["parsed"]["observations"][0]["description"] = "The pace remains constant."
        raw["source_observations"][0]["description"] = "The pace remains constant."
        raw["raw_response"] = __import__('json').dumps(raw["parsed"])
        folder = self.root/"adequate-rival"; folder.mkdir()
        path = folder/"observation.json"
        write_json(path, raw); finish_run(folder, "manufactured-observer-record", [])
        rival = copy.deepcopy(base["evidence"]["AO_STAGE1"][0]); rival["id"] = "rival-audio"
        rival["artifact"] = {"path": str(path), "sha256": sha256(path)}
        review_path = self.root/"rival-review.json"
        review = read_json(self.root/"scoped-review.json")
        review.update(observation_sha256=sha256(path), statement="The pace remains constant.")
        write_json(review_path, review)
        rival["review_artifact"] = {"path": str(review_path), "sha256": sha256(review_path)}
        base["evidence"]["AO_STAGE1"].append(rival)
        for left in ("SUPPORTED", "CONFIRMED"):
            for right in ("SUPPORTED", "CONFIRMED"):
                for reverse in (False, True):
                    packet = copy.deepcopy(base)
                    packet["observations"][0]["status"] = left
                    packet["observations"].extend([
                        self.obs("o-rival", "delivery", "rival-audio", status=right,
                                 interval=[.2, .7], statement="The pace remains constant."),
                        self.obs("o-rejected", "delivery", "audio-new", status="CONTRADICTED",
                                 interval=[.2, .7], statement="The pace slows.")])
                    if reverse:
                        packet["observations"].reverse()
                    packet["conflicts"] = [{"id": "two-survivors", "proposition": "delivery",
                        "observation_ids": ["o-delivery", "o-rival", "o-rejected"],
                        "question": "Which conflicting pace reading survives?"}]
                    self.decide(packet, {"o-delivery": left, "o-rival": right,
                                         "o-rejected": "CONTRADICTED", "c-delivery": "SUPPORTED"})
                    result = self.reconciled(packet, left+right+str(reverse)+".json")
                    with self.subTest(left=left, right=right, reverse=reverse):
                        for key in ("o-delivery", "o-rival"):
                            self.assertFalse(result["observations"][key]["adequate_for_support"])
                        self.assertNotIn(result["claims"][0]["state"], {"SUPPORTED", "CONFIRMED"})

    def independent_qualification_packet(self):
        packet = self.delivery_packet()
        proof = self.root/"independent-qualification"
        shutil.copytree(self.qual_root/"qualification", proof)
        path = self.root/"independent-scoped-review.json"
        review = read_json(self.root/"scoped-review.json")
        review["qualification_artifact"]["path"] = str(proof/"qualification.json")
        write_json(path, review)
        packet["evidence"]["AO_STAGE1"][0]["review_artifact"] = {"path": path.name, "sha256": sha256(path)}
        return packet, proof

    def enclosing_qualification_packet(self):
        packet, original = self.independent_qualification_packet()
        outer = self.root/"manufactured-containing-qualification-run"
        nested = outer/"nested-proof"
        shutil.copytree(original, nested)
        finish_run(outer, "manufactured-outer-qualification-container", [])
        review_path = self.root/"independent-scoped-review.json"
        review = read_json(review_path)
        review["qualification_artifact"]["path"] = str(nested/"qualification.json")
        review_path.write_text(__import__('json').dumps(review), encoding="utf-8")
        packet["evidence"]["AO_STAGE1"][0]["review_artifact"]["sha256"] = sha256(review_path)
        return packet, outer, nested

    def publish_enclosing_qualification(self, operation, packet, output):
        before = self.save(packet, operation+"-enclosing-before.json")
        if operation == "scene":
            return reconcile_scene(before, output)
        after = copy.deepcopy(packet)
        after["holistic_analysis"] = "The bounded delivery claim survives without a change in scope."
        return scene_claim_delta(before, self.save(after, operation+"-enclosing-after.json"), output)

    def test_enclosing_qualification_publication_refuses_inside_outer_run(self):
        packet, outer, nested = self.enclosing_qualification_packet()
        before = {p.relative_to(outer).as_posix(): sha256(p) for p in outer.rglob('*') if p.is_file()}
        for operation in ("scene", "delta"):
            with self.subTest(operation=operation):
                output = outer/(operation+"-output")
                with self.assertRaises(AVError):
                    self.publish_enclosing_qualification(operation, packet, output)
                self.assertFalse(output.exists())
                self.assertEqual(before, {p.relative_to(outer).as_posix(): sha256(p) for p in outer.rglob('*') if p.is_file()})
                verify_run(outer); verify_run(nested)

    def test_enclosing_qualification_safe_publication_binds_outer_manifest(self):
        packet, outer, nested = self.enclosing_qualification_packet()
        before = {p.relative_to(outer).as_posix(): sha256(p) for p in outer.rglob('*') if p.is_file()}
        for operation in ("scene", "delta"):
            with self.subTest(operation=operation):
                output = self.root/(operation+"-outside-output")
                manifest = self.publish_enclosing_qualification(operation, packet, output)
                sources = {r["path"]: r["sha256"] for r in manifest["sources"]}
                self.assertEqual(sources.get(str(outer/"run.json")), sha256(outer/"run.json"))
                self.assertEqual(before, {p.relative_to(outer).as_posix(): sha256(p) for p in outer.rglob('*') if p.is_file()})
                verify_run(output); verify_run(outer); verify_run(nested)

    def test_enclosing_qualification_corruption_refused_before_publication(self):
        packet, outer, nested = self.enclosing_qualification_packet()
        (outer/"unlisted-file.txt").write_text("Manufactured membership corruption", encoding="utf-8")
        verify_run(nested)
        for operation in ("scene", "delta"):
            with self.subTest(operation=operation):
                output = self.root/(operation+"-corrupt-outer-output")
                with self.assertRaises(AVError):
                    self.publish_enclosing_qualification(operation, packet, output)
                self.assertFalse(output.exists())
                self.assertFalse(list(self.root.glob('.'+output.name+'.staging-*')))

    def test_enclosing_qualification_membership_rechecked_before_publication(self):
        from unittest.mock import patch
        from avevidence.scene_packets import retain_scene_qualifications
        packet, outer, _ = self.enclosing_qualification_packet()
        marker = outer/"concurrent-unlisted-file.txt"
        def add_after_retention(*args, **kwargs):
            result = retain_scene_qualifications(*args, **kwargs)
            marker.write_text("Manufactured concurrent outer membership change", encoding="utf-8")
            return result
        for operation, module in (("scene", "cross_modal"), ("delta", "scene_delta")):
            try:
                with self.subTest(operation=operation), patch("avevidence."+module+".retain_scene_qualifications", side_effect=add_after_retention):
                    output = self.root/(operation+"-concurrent-outer-output")
                    with self.assertRaises(AVError):
                        self.publish_enclosing_qualification(operation, packet, output)
                    self.assertFalse(output.exists())
            finally:
                if marker.exists():
                    self.assertTrue(marker.resolve().is_relative_to(self.root.resolve()))
                    marker.unlink()
        verify_run(outer)

    def test_reconciliation_retains_recomputable_external_qualification(self):
        packet, original = self.independent_qualification_packet()
        output = self.root/"retained-reconciliation"
        reconcile_scene(self.save(packet, "retained.json"), output)
        retained = read_json(output/"qualification-bindings.json")
        import jsonschema
        jsonschema.validate(retained, read_json(Path(__file__).resolve().parents[1]/"schemas/scene-qualification-bindings.schema.json"))
        binding = retained["bindings"]["scene"]["audio-new"]
        proof = output/binding["path"]
        self.assertEqual(sha256(proof/"run.json"), binding["manifest_sha256"])
        source_paths = {r["path"] for r in read_json(output/"run.json")["sources"]}
        self.assertTrue({str(p.resolve()) for p in original.rglob('*') if p.is_file()} <= source_paths)
        self.assertTrue(original.resolve().is_relative_to(self.root.resolve()))
        original.rename(self.root/"relocated-original-qualification")
        verify_run(output)
        self.assertEqual(load_qualification(proof)["status"], "QUALIFIED_FOR_SCOPE")
        snapshot = proof/"benchmark-snapshot.json"
        snapshot.write_bytes(snapshot.read_bytes()+b" ")
        with self.assertRaises(AVError):
            verify_run(output)

    def test_claim_delta_retains_both_qualification_proofs(self):
        packet, original = self.independent_qualification_packet()
        before = self.save(packet, "qualification-before.json")
        after = copy.deepcopy(packet); after["holistic_analysis"] = "The scoped pacing premise survives; emotional meaning remains open."
        after_path = self.save(after, "qualification-after.json")
        output = self.root/"retained-delta"
        scene_claim_delta(before, after_path, output)
        retained = read_json(output/"qualification-bindings.json")
        self.assertEqual(set(retained["bindings"]), {"before", "after"})
        self.assertEqual(retained["bindings"]["before"]["audio-new"]["path"],
                         retained["bindings"]["after"]["audio-new"]["path"])
        second = self.root/"second-qualification"
        benchmark(qualification_config(self.root/"second-qualification-inputs"), second)
        review = read_json(self.root/"independent-scoped-review.json")
        review["qualification_artifact"] = {"path": str(second/"qualification.json"),
                                            "sha256": sha256(second/"qualification.json")}
        review_path = self.root/"second-scoped-review.json"
        write_json(review_path, review)
        after["evidence"]["AO_STAGE1"][0]["review_artifact"] = {"path": review_path.name, "sha256": sha256(review_path)}
        distinct = self.root/"distinct-retained-delta"
        scene_claim_delta(before, self.save(after, "distinct-qualification-after.json"), distinct)
        distinct_bindings = read_json(distinct/"qualification-bindings.json")["bindings"]
        self.assertNotEqual(distinct_bindings["before"]["audio-new"]["path"],
                            distinct_bindings["after"]["audio-new"]["path"])
        for generated in (original, second):
            self.assertTrue(generated.resolve().is_relative_to(self.root.resolve()))
        original.rename(self.root/"relocated-delta-qualification")
        second.rename(self.root/"relocated-second-qualification")
        verify_run(output)
        verify_run(distinct)
        for label in ("before", "after"):
            proof = output/retained["bindings"][label]["audio-new"]["path"]
            self.assertEqual(load_qualification(proof)["status"], "QUALIFIED_FOR_SCOPE")
            distinct_proof = distinct/distinct_bindings[label]["audio-new"]["path"]
            self.assertEqual(load_qualification(distinct_proof)["status"], "QUALIFIED_FOR_SCOPE")

    def test_reconciliation_cannot_publish_inside_qualification_input(self):
        packet, proof = self.independent_qualification_packet()
        with self.assertRaisesRegex(AVError, "inside an input"):
            reconcile_scene(self.save(packet, "inside-proof.json"), proof/"reconciliation")
        verify_run(proof)

    def test_extended_scene_outputs_match_public_schemas(self):
        import jsonschema
        packet = self.delivery_packet()
        result = self.reconciled(packet)
        schemas = Path(__file__).resolve().parents[1]/"schemas"
        jsonschema.validate(packet, read_json(schemas/"scene-packet.schema.json"))
        jsonschema.validate(result, read_json(schemas/"scene-reconciliation.schema.json"))

    def test_one_surviving_alternative_preserves_its_narrower_scope(self):
        packet = self.static_packet()
        packet["evidence"]["AM"] = [self.ref("unreviewed", self.measurement, channel="AM")]
        packet["evidence"]["AM"][0]["locator"]["source_sha256"] = packet["source"]["sha256"]
        packet["observations"].append(self.obs("o-other", "visual_fact", "unreviewed", status="OPEN"))
        claim = packet["claims"][0]
        claim["observation_ids"].append("o-other")
        good = self.route(["o-static"], supplementary=["o-other"])
        good.update(id="static-proof", scope="Only the reviewed static fact")
        bad = self.route(["o-other"], supplementary=["o-static"])
        bad.update(id="unreviewed-proof", scope="Unreviewed alternative remains open")
        claim["support_routes"] = [good, bad]
        packet["dependencies"] = [{**self.edge(key, claim["id"]), "role": "SUPPORTING"}
                                  for key in claim["observation_ids"]]
        result = self.reconciled(packet)
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
        self.assertEqual(result["claims"][0]["surviving_scopes"], [good["scope"]])
