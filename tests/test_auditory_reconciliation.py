"""Generated graph/receipt conformance; no actual perceptual qualification."""
import copy
from pathlib import Path
import tempfile
import unittest

from avevidence.auditory_qualification import benchmark
from avevidence.common import AVError, finish_run, read_json, sha256, write_json
from avevidence.cross_modal import reconcile_data
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
