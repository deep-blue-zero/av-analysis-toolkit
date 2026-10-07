"""Generated event-contract fixtures; no episode, paid call or perceptual verdict.

The auditory JSON below exercises attribution contracts, not a real observer.
Every byte digest comes from a distinct generated artifact.
"""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from array import array
import wave

from avevidence.common import AVError, read_json, run, sha256, write_json
from avevidence.cross_modal import reconcile_data, reconcile_scene
from avevidence.event_contracts import CLOCK
from avevidence.inventory import verify_run
from avevidence.observation_dependencies import dependence_groups, propagate_changes, validate_graph
from avevidence.scene_delta import scene_claim_delta
from avevidence.scene_packets import load_scene_packet, packet_identity, prepare_scene_packet, validate_packet
from avevidence.scene_planning import plan_scene_queries
from avevidence.visual import extract_frames


class SceneEvents(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ave-scene-contract-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "generated-source.wav"
        self._wav(self.source, 8.)
        self.source_hash = sha256(self.source)
        self.clip = self.root / "generated-clip.wav"
        with wave.open(str(self.source), "rb") as source:
            source.setpos(1600)
            payload = source.readframes(4800)
        with wave.open(str(self.clip), "wb") as clip:
            clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(8000)
            clip.writeframes(payload)
        self.text = self.root / "textual-witness.json"
        write_json(self.text, {"line": "The generated character says stay.", "language": "synthetic-English"})
        self.still = self.root / "generated-still.ppm"
        self.still.write_text("P3\n1 1\n255\n20 30 40\n", encoding="ascii")
        self.measurement = self.root / "generic-measurement.json"
        write_json(self.measurement, {"schema": "ave.synthetic-measurement.v1", "source_sha256": self.source_hash,
                                      "rms": .2, "description": "A generated unsupported measurement record."})
        self.a1 = self.root / "stage1.json"
        self.a2 = self.root / "stage2.json"
        base = {"schema": "ave.auditory-observation.v1", "stage": 1,
                "source_sha256": self.source_hash, "stream_index": 0,
                "source_interval_seconds": [.2, .8], "clip_sha256": sha256(self.clip),
                "task_profile": "SPEECH_PERFORMANCE", "validation_status": "VALID",
                "observer_type": "model_audio", "model_revision": "generated-contract-fixture",
                "observation": "Synthetic attributed description of a soft delivery."}
        write_json(self.a1, base)
        contextual = dict(base, stage=2, observation="Synthetic contextual attributed description.",
                          stage1_parent={"observation_sha256": sha256(self.a1)},
                          supplied_context={"context_evidence_ids": ["txt"]})
        write_json(self.a2, contextual)

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve(), self.root)
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.temp.cleanup()

    @staticmethod
    def _wav(path, seconds):
        samples = array("h", (int(1000 * math.sin(i * .19) + 120 * math.sin(i * .013))
                              for i in range(round(seconds * 8000))))
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(8000)
            target.writeframes(samples.tobytes())

    def ref(self, eid, artifact, *, authority=None, channel=None, interval=None, **extras):
        locator = {"source_sha256": self.source_hash, "clock": CLOCK,
                   "intervals_seconds": [interval or [.2, .8]]}
        if channel in {"AM", "AO_STAGE1", "AO_STAGE2", "HL", "TVIS", "VO", "AVO"}:
            locator["stream_index"] = 0
        result = {"id": eid, "artifact": {"path": artifact.name, "sha256": sha256(artifact)}, "locator": locator}
        if authority is not None:
            result["authority"] = authority
        result.update(extras)
        return result

    @staticmethod
    def obs(oid, proposition, eid, *, status="PROVISIONAL", interval=None, **extras):
        result = {"id": oid, "proposition": proposition, "statement": "Synthetic observation for " + oid,
                  "status": status, "evidence_ids": [eid], "interval_seconds": interval or [.2, .8]}
        result.update(extras)
        return result

    @staticmethod
    def claim(cid, proposition, oid, *, status="PROVISIONAL", interval=None, **extras):
        result = {"id": cid, "proposition": proposition, "statement": "Synthetic claim for " + cid,
                  "status": status, "observation_ids": [oid], "interval_seconds": interval or [.2, .8]}
        result.update(extras)
        return result

    @staticmethod
    def edge(start, end, relation="SUPPORTS"):
        return {"from": start, "to": end, "relation": relation, "reason": "Explicit generated premise dependency."}

    def packet(self):
        return {"schema": "ave.scene-packet.v1", "scene_id": "synthetic-event-01",
                "source": {"sha256": self.source_hash, "clock": CLOCK, "path": self.source.name,
                           "duration_seconds": 8., "audio_stream": 0},
                "interval_seconds": [0., 6.],
                "narrative_boundary": {"basis": "generated test boundary", "description": "A bounded synthetic exchange."},
                "question": "Which exact propositions survive the synthetic evidence comparison?",
                "evidence": {"TXT": [self.ref("txt", self.text, authority="canonical_text", record_pointer="/line")]},
                "observations": [self.obs("o-text", "wording", "txt")],
                "claims": [self.claim("c-text", "wording", "o-text")],
                "dependencies": [self.edge("o-text", "c-text")], "conflicts": [],
                "adjudication": {"decisions": {}}, "open_questions": ["The fixture makes no real performance judgment."]}

    def save(self, packet, name="packet.json"):
        path = self.root / name
        write_json(path, packet)
        return path

    def reconciled(self, packet, name="packet.json"):
        return reconcile_data(*load_scene_packet(self.save(packet, name))[:4])

    @staticmethod
    def decide(packet, states):
        packet["adjudication"] = {"reviewer": "Synthetic contract reviewer",
                                   "decisions": {key: {"state": value, "reason": "Explicit local fixture adjudication."}
                                                 for key, value in states.items()}}

    def test_hashes_are_actual_distinct_artifact_digests(self):
        values = [sha256(path) for path in (self.source, self.clip, self.text, self.still, self.measurement, self.a1, self.a2)]
        self.assertEqual(len(set(values)), len(values))
        self.assertTrue(all(len(value) == 64 for value in values))

    def test_packet_identity_roundtrip_and_exact_original_freeze(self):
        packet = self.packet(); path = self.save(packet)
        original = path.read_bytes(); identity = packet_identity(packet)
        output = self.root / "prepared"
        prepare_scene_packet(path, output)
        frozen = read_json(output / "scene-packet.json")
        self.assertEqual(frozen["packet_id"], identity)
        self.assertEqual(packet_identity(frozen), identity)
        self.assertEqual((output / "input-packet.json").read_bytes(), original)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(load_scene_packet(path)[5], "SOURCE_BYTES_VERIFIED_NOT_PERCEPTUALLY_INSPECTED")
        verify_run(output)
        # Generated prepared refs are verified absolute paths; original bytes stay separate.
        prepared = load_scene_packet(output / "scene-packet.json")
        self.assertEqual(packet_identity(prepared[0]), identity)
        self.assertEqual(prepared[5], "SOURCE_BYTES_VERIFIED_NOT_PERCEPTUALLY_INSPECTED")

    def test_explicit_relocation_preserves_logical_identity(self):
        packet = self.packet(); path = self.save(packet)
        old_id = packet_identity(packet)
        destination = self.root / "relocated"; destination.mkdir()
        relocated = destination / self.text.name
        shutil.move(str(self.text), str(relocated))
        with self.assertRaisesRegex(AVError, "missing"):
            load_scene_packet(path)
        loaded = load_scene_packet(path, relocations={sha256(relocated): str(relocated)})
        self.assertEqual(packet_identity(loaded[0]), old_id)
        path_changed = copy.deepcopy(packet)
        path_changed["source"]["path"] = "another-source-location.wav"
        path_changed["evidence"]["TXT"][0]["artifact"]["path"] = "relocated/textual-witness.json"
        self.assertEqual(packet_identity(path_changed), old_id)

    def test_changed_existing_artifact_cannot_fall_back_to_good_relocation(self):
        path = self.save(self.packet()); good = self.root / "good-copy.json"
        shutil.copyfile(self.text, good); original_digest = sha256(good)
        self.text.write_text('{"line":"changed"}', encoding="utf-8")
        with self.assertRaisesRegex(AVError, "changed"):
            load_scene_packet(path, relocations={original_digest: str(good)})

    def test_changed_pointer_source_stream_and_identity_are_refused(self):
        for field in ("pointer", "source", "stream", "identity"):
            packet = self.packet()
            if field == "pointer":
                packet["evidence"]["TXT"][0]["record_pointer"] = "/absent"
            elif field == "source":
                packet["evidence"]["TXT"][0]["locator"]["source_sha256"] = sha256(self.clip)
            elif field == "stream":
                packet["evidence"]["AM"] = [self.ref("am", self.measurement, channel="AM")]
                packet["evidence"]["AM"][0]["locator"]["stream_index"] = 1
            else:
                packet["packet_id"] = packet_identity(packet)
                packet["question"] = "A changed question must have a different logical identity."
            with self.subTest(field=field), self.assertRaises(AVError):
                load_scene_packet(self.save(packet, field + ".json"))

    def test_strict_json_duplicates_nonfinite_and_unknown_fields_are_refused(self):
        path = self.root / "strict.json"
        for payload in ('{"schema":1,"schema":2}', '{"x":NaN}'):
            path.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(AVError, "strict UTF-8 JSON"):
                load_scene_packet(path)
        packet = self.packet(); packet["unknown"] = "not accepted"
        with self.assertRaisesRegex(AVError, "unknown"):
            validate_packet(packet)

    def test_rfc_pointer_rejects_negative_leading_zero_and_bad_escape(self):
        artifact = self.root / "array-witness.json"; write_json(artifact, {"rows": ["zero", "one"], "slash/key": "escaped"})
        for index, pointer in enumerate(("/rows/-1", "/rows/01", "/rows/1~2", "/rows/+1")):
            packet = self.packet()
            packet["evidence"]["TXT"] = [self.ref("txt", artifact, authority="canonical_text", record_pointer=pointer)]
            with self.subTest(pointer=pointer), self.assertRaises(AVError):
                load_scene_packet(self.save(packet, "pointer-%s.json" % index))
        packet = self.packet()
        packet["evidence"]["TXT"] = [self.ref("txt", artifact, authority="canonical_text", record_pointer="/slash~1key")]
        load_scene_packet(self.save(packet, "escaped-pointer.json"))

    def test_evidence_mutating_during_read_is_refused(self):
        import avevidence.scene_packets as packets
        packet = self.packet(); packet["evidence"]["AO_STAGE1"] = [self.ref("ao1", self.a1, channel="AO_STAGE1")]
        path = self.save(packet); original_read = packets.read_event_json
        def mutate_after_read(candidate, *args, **kwargs):
            value = original_read(candidate, *args, **kwargs)
            if Path(candidate).resolve() == self.a1:
                self.a1.write_text(json.dumps(dict(value, observation="Changed after the admitted read.")), encoding="utf-8")
            return value
        with patch.object(packets, "read_event_json", side_effect=mutate_after_read), self.assertRaisesRegex(AVError, "changed after admission"):
            load_scene_packet(path)

    def test_evidence_mutating_before_publication_is_refused(self):
        import avevidence.cross_modal as cross_modal
        path = self.save(self.packet()); original_assessment = cross_modal.evidence_assessment
        def mutate_after_assessment(*args, **kwargs):
            result = original_assessment(*args, **kwargs)
            self.text.write_text('{"line":"Changed after assessment."}', encoding="utf-8")
            return result
        output = self.root / "mutated-reconciliation"
        with patch.object(cross_modal, "evidence_assessment", side_effect=mutate_after_assessment), self.assertRaisesRegex(AVError, "changed after admission"):
            reconcile_scene(path, output)
        self.assertFalse(output.exists())

    def test_local_and_transitive_contradiction_preserves_unrelated_nodes(self):
        observations = [self.obs("a", "wording", "txt", status="CONFIRMED"),
                        self.obs("b", "visual_fact", "vis", status="CONFIRMED")]
        claims = [self.claim("c", "wording", "a", status="SUPPORTED"),
                  self.claim("d", "interpretation", "a", status="SUPPORTED"),
                  self.claim("unrelated", "visual_fact", "b", status="SUPPORTED")]
        edges = [self.edge("a", "c"), self.edge("c", "d", "REQUIRES_RECHECK_IF_CHANGED"), self.edge("b", "unrelated")]
        original = copy.deepcopy((observations, claims, edges))
        result = propagate_changes(observations, claims, edges, {"a": {"state": "CONTRADICTED", "reason": "Lexical correction."}})
        self.assertEqual(result["state_overlay"]["a"]["after"], "CONTRADICTED")
        self.assertEqual(result["recheck_ids"], ["c", "d"])
        self.assertEqual(result["unaffected_ids"], ["b", "unrelated"])
        self.assertEqual(result["state_overlay"]["unrelated"]["after"], "SUPPORTED")
        self.assertEqual((observations, claims, edges), original)

    def test_dependency_cycle_and_claim_to_observation_are_refused(self):
        packet = self.packet()
        packet["claims"].append(self.claim("other", "interpretation", "o-text"))
        packet["dependencies"].append(self.edge("o-text", "other"))
        edges = packet["dependencies"] + [self.edge("c-text", "other"), self.edge("other", "c-text")]
        with self.assertRaisesRegex(AVError, "Circular"):
            validate_graph(packet["observations"], packet["claims"], edges)
        with self.assertRaisesRegex(AVError, "retroactively"):
            validate_graph(packet["observations"], packet["claims"], [self.edge("c-text", "o-text")])

    def test_no_op_state_decision_does_not_trigger_downstream_recheck(self):
        packet = self.packet()
        result = propagate_changes(packet["observations"], packet["claims"], packet["dependencies"],
                                   {"o-text": {"state": "PROVISIONAL", "reason": "The state is explicitly unchanged."}})
        self.assertEqual(result["recheck_ids"], [])
        self.assertEqual(result["state_overlay"]["c-text"]["changed_premises"], [])

    def test_lexical_text_authority_and_auditory_delivery_remain_separate(self):
        packet = self.packet()
        packet["evidence"]["AO_STAGE1"] = [self.ref("ao1", self.a1, channel="AO_STAGE1", authority="observer_report")]
        packet["observations"] += [self.obs("o-guess", "wording", "ao1"), self.obs("o-delivery", "delivery", "ao1")]
        packet["claims"].append(self.claim("c-delivery", "delivery", "o-delivery", status="SUPPORTED"))
        packet["dependencies"].append(self.edge("o-delivery", "c-delivery"))
        packet["conflicts"] = [{"id": "lexical-conflict", "proposition": "wording", "observation_ids": ["o-text", "o-guess"],
                                "question": "Which lexical witness is authoritative?"}]
        self.decide(packet, {"o-text": "SUPPORTED", "c-text": "SUPPORTED", "o-delivery": "SUPPORTED", "c-delivery": "SUPPORTED"})
        result = self.reconciled(packet)
        text = result["observations"]["o-text"]["evidence_assessments"][0]
        guess = result["observations"]["o-guess"]["evidence_assessments"][0]
        delivery = result["observations"]["o-delivery"]["evidence_assessments"][0]
        self.assertEqual(text["authority"], "canonical_text"); self.assertTrue(text["adequate_for_support"])
        self.assertEqual(guess["authority"], "NON_AUTHORITATIVE_LEXICAL_GUESS")
        self.assertEqual(delivery["authority"], "PROVISIONAL_MODEL_REPORT")
        self.assertFalse(delivery["adequate_for_support"]); self.assertFalse(delivery["global_qualification"])
        self.assertEqual(result["conflicts"][0]["preferred_text_observations"], ["o-text"])
        self.assertEqual(result["dependency_assessment"]["state_overlay"]["o-delivery"]["after"], "OPEN")
        self.assertNotIn(next(c for c in result["claims"] if c["id"] == "c-delivery")["state"], {"SUPPORTED", "CONFIRMED"})

    def test_affirmative_raw_text_status_needs_explicit_attributed_adjudication(self):
        packet = self.packet(); packet["observations"][0]["status"] = "CONFIRMED"
        packet["claims"][0]["status"] = "SUPPORTED"
        result = self.reconciled(packet)
        self.assertEqual(result["dependency_assessment"]["state_overlay"]["o-text"]["after"], "OPEN")
        self.assertNotIn(result["claims"][0]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_direct_fact_requires_same_proposition_and_claim_interval_coverage(self):
        for kind in ("interval", "proposition"):
            packet = self.packet()
            packet["observations"][0]["status"] = "SUPPORTED"; packet["claims"][0]["status"] = "SUPPORTED"
            if kind == "interval": packet["claims"][0]["interval_seconds"] = [1., 2.]
            if kind == "proposition": packet["claims"][0]["proposition"] = "delivery"
            self.decide(packet, {"o-text": "SUPPORTED", "c-text": "SUPPORTED"})
            result = self.reconciled(packet, kind + "-scope.json")
            with self.subTest(kind=kind):
                self.assertTrue(result["observations"]["o-text"]["adequate_for_support"])
                self.assertEqual(result["claims"][0]["state"], "OPEN")

    def test_still_motion_text_delivery_and_rms_emotion_are_not_competent(self):
        packet = self.packet()
        packet["evidence"].update(VIS=[self.ref("vis", self.still)], AM=[self.ref("am", self.measurement, channel="AM")])
        for oid, proposition, eid in (("o-motion", "motion", "vis"), ("o-emotion", "emotion", "am"), ("o-breath", "delivery", "txt")):
            packet["observations"].append(self.obs(oid, proposition, eid, status="CONFIRMED"))
        self.decide(packet, {oid: "SUPPORTED" for oid in ("o-motion", "o-emotion", "o-breath")})
        result = self.reconciled(packet)
        for oid in ("o-motion", "o-emotion", "o-breath"):
            with self.subTest(oid=oid):
                assessment = result["observations"][oid]["evidence_assessments"][0]
                self.assertFalse(assessment["competent"]); self.assertFalse(assessment["adequate_for_support"])
                self.assertEqual(result["dependency_assessment"]["state_overlay"][oid]["after"], "OPEN")

    def test_claimed_complete_temporal_status_without_review_remains_open(self):
        packet = self.packet()
        report = self.root / "generated-temporal-report.json"
        write_json(report, {"schema": "ave.temporal-inspection.v1", "source_sha256": self.source_hash,
                            "stream_index": 1, "status": "GENERATED_NOT_REVIEWED"})
        temporal = self.ref("tvis", report, channel="TVIS")
        temporal["locator"]["stream_index"] = 1
        packet["source"]["video_stream"] = 1
        packet["evidence"]["TVIS"] = [temporal]
        packet["observations"].append(self.obs("o-contact", "contact", "tvis", status="CONFIRMED",
                                              temporal_status="FRAME_COMPLETE_CONFIRMED"))
        self.decide(packet, {"o-contact": "CONFIRMED"})
        result = self.reconciled(packet)
        row = result["observations"]["o-contact"]["evidence_assessments"][0]
        self.assertEqual(row["authority"], "GENERATED_NOT_REVIEWED")
        self.assertFalse(row["adequate_for_support"])
        self.assertEqual(result["dependency_assessment"]["state_overlay"]["o-contact"]["after"], "OPEN")

    def test_human_declaration_is_exactly_scoped_and_broad_accuracy_review_cannot_close(self):
        for kind in ("exact", "broad", "wrong_interval", "wrong_stream"):
            raw = {"schema": "ave.human-listening-observation.v1", "actual_audio_inspection_declared": True,
                   "reviewer": "Synthetic scoped declaration", "source_sha256": self.source_hash,
                   "stream_index": 0, "proposition": "delivery", "observation": "An exact generated contract observation.",
                   "inspected_intervals_seconds": [[.2, .8]]}
            if kind == "broad": raw["schema"] = "ave.scoped-human-auditory-review.v1"
            if kind == "wrong_interval": raw["inspected_intervals_seconds"] = [[.2, .5]]
            if kind == "wrong_stream": raw["stream_index"] = 1
            artifact = self.root / (kind + "-human.json"); write_json(artifact, raw)
            packet = self.packet(); packet["evidence"]["HL"] = [self.ref("hl", artifact, channel="HL")]
            packet["observations"].append(self.obs("o-human", "delivery", "hl"))
            self.decide(packet, {"o-human": "SUPPORTED"})
            result = self.reconciled(packet, kind + "-human-packet.json")
            with self.subTest(kind=kind):
                self.assertEqual(result["observations"]["o-human"]["adequate_for_support"], kind == "exact")
                self.assertEqual(result["dependency_assessment"]["state_overlay"]["o-human"]["after"], "SUPPORTED" if kind == "exact" else "OPEN")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Generated static fixture requires FFmpeg/ffprobe")
    def test_source_bound_static_inventory_and_local_review_can_support_static_fact(self):
        raw = self.root / "static-source.gray"
        raw.write_bytes(b"".join(bytes([20 + i * 20]) * 32 * 24 for i in range(8)))
        source = self.root / "static-source.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "rawvideo", "-pixel_format", "gray",
             "-video_size", "32x24", "-framerate", "8", "-i", raw, "-c:v", "ffv1", source])
        frames = self.root / "static-frames"
        extract_frames(source, frames, mode="exact", timestamps=[.25], width=0, stream_index=0)
        inventory = frames / "frames.json"; data = read_json(inventory)
        selected = data["rows"][0]
        review = self.root / "static-review.json"
        write_json(review, {"schema": "ave.static-review.v1", "actual_visual_inspection_declared": True,
                            "reviewer": "Synthetic static-review contract fixture", "source_sha256": sha256(source),
                            "stream_index": 0, "frame_inventory_sha256": sha256(inventory),
                            "inspected_frame_ids": [selected["frame_id"]],
                            "observation": "A generated frame has a single uniform gray field; test declaration only."})
        packet = self.packet()
        packet["source"] = {"path": source.name, "sha256": sha256(source), "clock": CLOCK, "video_stream": 0, "duration_seconds": 1.}
        packet["interval_seconds"] = [0., 1.]
        visual = {"id": "vis", "artifact": {"path": "static-frames/frames.json", "sha256": sha256(inventory)},
                  "review_artifact": {"path": review.name, "sha256": sha256(review)},
                  "locator": {"source_sha256": sha256(source), "clock": CLOCK, "stream_index": 0,
                              "points_seconds": [selected["source_seconds"]]}}
        packet["evidence"] = {"VIS": [visual]}
        packet["observations"] = [self.obs("o-static", "visual_fact", "vis", interval=[.25, .26], status="SUPPORTED")]
        packet["claims"] = [self.claim("c-static", "visual_fact", "o-static", interval=[.25, .26], status="SUPPORTED")]
        packet["dependencies"] = [self.edge("o-static", "c-static")]
        self.decide(packet, {"o-static": "SUPPORTED", "c-static": "SUPPORTED"})
        result = self.reconciled(packet)
        self.assertTrue(result["observations"]["o-static"]["adequate_for_support"])
        self.assertEqual(result["claims"][0]["state"], "SUPPORTED")
        # A verified inventory never makes the same static evidence competent for motion.
        packet["observations"][0]["proposition"] = "motion"
        packet["claims"][0]["proposition"] = "motion"
        result = self.reconciled(packet, "static-motion.json")
        self.assertFalse(result["observations"]["o-static"]["adequate_for_support"])
        self.assertNotIn(result["claims"][0]["state"], {"SUPPORTED", "CONFIRMED"})

    def test_stage2_shared_context_and_parent_are_dependent_not_votes(self):
        packet = self.packet()
        packet["evidence"].update(AO_STAGE1=[self.ref("ao1", self.a1, channel="AO_STAGE1")],
                                  AO_STAGE2=[self.ref("ao2", self.a2, channel="AO_STAGE2", stage1_evidence_id="ao1", context_evidence_ids=["txt"])])
        packet["observations"] += [self.obs("o-stage1", "delivery", "ao1"), self.obs("o-stage2", "delivery", "ao2")]
        loaded = load_scene_packet(self.save(packet))
        groups = dependence_groups(["o-text", "o-stage1", "o-stage2"], loaded[2], packet["dependencies"], loaded[1])
        self.assertEqual(groups["groups"], [["o-stage1", "o-stage2", "o-text"]])
        self.assertTrue(groups["not_a_majority_vote"]); self.assertTrue(groups["independence_not_proven"])
        result = reconcile_data(*loaded[:4])
        self.assertTrue(result["observations"]["o-stage2"]["evidence_assessments"][0]["context_contaminated"])
        self.assertFalse(result["observations"]["o-stage2"]["adequate_for_support"])

    def test_stage2_changed_parent_clip_task_and_context_are_refused(self):
        for field in ("parent", "clip", "task", "context"):
            packet = self.packet(); changed = read_json(self.a2)
            if field == "parent": changed["stage1_parent"]["observation_sha256"] = sha256(self.text)
            if field == "clip": changed["clip_sha256"] = sha256(self.source)
            if field == "task": changed["task_profile"] = "SOUNDSCAPE"
            if field == "context": changed["supplied_context"]["context_evidence_ids"] = []
            artifact = self.root / (field + "-stage2.json"); write_json(artifact, changed)
            packet["evidence"].update(AO_STAGE1=[self.ref("ao1", self.a1, channel="AO_STAGE1")],
                                      AO_STAGE2=[self.ref("ao2", artifact, channel="AO_STAGE2", stage1_evidence_id="ao1", context_evidence_ids=["txt"])])
            with self.subTest(field=field), self.assertRaises(AVError):
                load_scene_packet(self.save(packet, field + "-packet.json"))

    def test_malformed_auditory_structure_is_a_contract_refusal(self):
        for field, value in (("clips", "not an ordered clip list"), ("task_profile", ["SPEECH_PERFORMANCE"])):
            raw = read_json(self.a1); raw[field] = value
            artifact = self.root / (field + "-malformed.json"); write_json(artifact, raw)
            packet = self.packet()
            packet["evidence"]["AO_STAGE1"] = [self.ref("ao1", artifact, channel="AO_STAGE1")]
            packet["observations"].append(self.obs("o-delivery", "delivery", "ao1"))
            with self.subTest(field=field), self.assertRaises(AVError):
                self.reconciled(packet, field + "-malformed-packet.json")

    def test_planner_respects_all_ceilings_and_never_executes_or_authorizes(self):
        packet = self.packet()
        packet["claims"].append(self.claim("c-emotion", "emotion", "o-text", interval=[1., 4.]))
        packet["dependencies"].append(self.edge("o-text", "c-emotion"))
        path = self.save(packet); output = self.root / "plan"
        plan_scene_queries(path, output, max_requests=1, max_frames=0, max_audio_seconds=0.)
        result = read_json(output / "query-plan.json")
        self.assertLessEqual(len(result["requests"]), 1)
        self.assertEqual(result["planned_resources"], {"frames": 0, "audio_seconds": 0.})
        self.assertEqual(result["api_spend_usd"], 0); self.assertEqual(result["execution"], "NONE")
        self.assertTrue(any(r["status"] == "OPEN_RESOURCE_CEILING" for r in result["open_requests"]))
        self.assertTrue(all(r["api_submission_authorized"] is False for r in result["requests"]))
        verify_run(output)
        for name, kwargs in (("requests", {"max_requests": 201}), ("frames", {"max_frames": 10001}),
                             ("seconds", {"max_audio_seconds": 1201}), ("boolean", {"max_frames": True})):
            with self.subTest(name=name), self.assertRaises(AVError):
                plan_scene_queries(path, self.root / name, **kwargs)

    def test_planner_keeps_contact_and_order_requirements_when_intervals_match(self):
        packet = self.packet()
        packet["claims"] = [self.claim("c-motion", "motion", "o-text"), self.claim("c-contact", "contact", "o-text"),
                            self.claim("c-order", "event_order", "o-text")]
        packet["dependencies"] = [self.edge("o-text", row["id"]) for row in packet["claims"]]
        output = self.root / "specific-plan"; plan_scene_queries(self.save(packet), output)
        requests = read_json(output / "query-plan.json")["requests"]
        for cid, required_type in (("c-contact", "EXACT_CONTACT"), ("c-order", "CONTACT_ORDER")):
            with self.subTest(claim=cid):
                self.assertTrue(any(cid in row["claim_ids"] and row.get("question_type") == required_type for row in requests))

    def test_auditory_plan_uses_neutral_task_prompt_and_retains_claim_separately(self):
        packet = self.packet()
        packet["claims"][0]["proposition"] = "delivery"
        packet["claims"][0]["statement"] = "A generated narrative inference about a reluctant character."
        output = self.root / "neutral-plan"; plan_scene_queries(self.save(packet), output)
        request = read_json(output / "query-plan.json")["requests"][0]
        self.assertEqual(request["analytical_claims"], [packet["claims"][0]["statement"]])
        self.assertNotEqual(request["question"], packet["claims"][0]["statement"])
        self.assertNotIn("reluctant character", request["question"])
        self.assertEqual(request["stage"], 1); self.assertFalse(request["api_submission_authorized"])

    def test_music_planner_retains_coherent_interval_and_long_speech_stays_open(self):
        source = self.root / "generated-long-source.wav"; self._wav(source, 100.)
        packet = self.packet(); packet["source"].update(path=source.name, sha256=sha256(source), duration_seconds=100.)
        packet["interval_seconds"] = [0., 90.]
        packet["evidence"]["TXT"][0]["locator"]["source_sha256"] = sha256(source)
        packet["claims"] = [self.claim("c-song", "performance_music", "o-text", interval=[0., 90.]),
                            self.claim("c-speech", "delivery", "o-text", interval=[0., 90.])]
        packet["dependencies"] = [self.edge("o-text", row["id"]) for row in packet["claims"]]
        output = self.root / "music-plan"; plan_scene_queries(self.save(packet), output, max_frames=1080, max_audio_seconds=90.)
        result = read_json(output / "query-plan.json")
        music = next(r for r in result["requests"] if r["route"] == "AUDITORY")
        self.assertEqual(music["interval_seconds"], [0., 90.]); self.assertEqual(music["audio_mode"], "coherent_section")
        self.assertEqual(music["task_profile"], "PERFORMANCE_MUSIC")
        self.assertTrue(any(r["claim_id"] == "c-speech" and r["status"] == "OPEN_REFINE_INTERVAL" for r in result["open_requests"]))

    def test_frozen_delta_retains_exact_before_and_after_without_promotion(self):
        before = self.packet(); after = copy.deepcopy(before)
        after["observations"][0]["statement"] = "A changed synthetic lexical observation remains pending review."
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        frozen_a, frozen_b = a.read_bytes(), b.read_bytes(); output = self.root / "delta"
        scene_claim_delta(a, b, output)
        result = read_json(output / "claim-delta.json")
        self.assertEqual(result["claims"][0]["disposition"], "OPEN")
        self.assertFalse(result["automatic_confidence_promotion"])
        self.assertEqual((output / "before-packet.json").read_bytes(), frozen_a)
        self.assertEqual((output / "after-packet.json").read_bytes(), frozen_b)
        self.assertEqual(a.read_bytes(), frozen_a); self.assertEqual(b.read_bytes(), frozen_b)
        self.assertEqual(result["baseline_input_sha256"], sha256(a)); self.assertEqual(result["completed_input_sha256"], sha256(b))
        verify_run(output)

    def test_unchanged_claim_is_preserved_without_new_confidence(self):
        packet = self.packet(); a = self.save(packet, "before.json"); b = self.save(packet, "after.json")
        output = self.root / "unchanged"; scene_claim_delta(a, b, output)
        row = read_json(output / "claim-delta.json")["claims"][0]
        self.assertEqual(row["disposition"], "PRESERVE"); self.assertEqual(row["relevant_new_evidence"], [])
        self.assertEqual(row["confidence_change"], {"before": "OPEN", "after": "OPEN"})

    def delta_decision(self, disposition):
        return {"reviewer": "Synthetic delta reviewer", "decisions": {"c-text": {
            "disposition": disposition, "reason": "Explicit synthetic disposition.",
            "what_survived": "The frozen initial formulation.", "what_changed": "The declared changed premise.",
            "remaining_open_questions": ["The source is a generated contract fixture."]}}}

    def test_strengthening_without_relevant_new_evidence_is_refused(self):
        before = self.packet(); after = copy.deepcopy(before)
        self.decide(after, {"o-text": "SUPPORTED", "c-text": "SUPPORTED"})
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        decision = self.save(self.delta_decision("STRENGTHEN"), "delta-decisions.json")
        with self.assertRaisesRegex(AVError, "STRENGTHEN requires"):
            scene_claim_delta(a, b, self.root / "bad-strengthen", decisions=decision)
        self.assertFalse((self.root / "bad-strengthen").exists())

    def test_new_provisional_auditory_material_cannot_strengthen_delivery(self):
        before = self.packet(); before["claims"][0]["proposition"] = "delivery"
        after = copy.deepcopy(before)
        after["evidence"]["AO_STAGE1"] = [self.ref("ao1", self.a1, channel="AO_STAGE1")]
        after["observations"][0] = self.obs("o-text", "delivery", "ao1")
        self.decide(after, {"o-text": "SUPPORTED", "c-text": "SUPPORTED"})
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        decision = self.save(self.delta_decision("STRENGTHEN"), "delta-decisions.json")
        with self.assertRaisesRegex(AVError, "STRENGTHEN requires"):
            scene_claim_delta(a, b, self.root / "bad-auditory-strengthen", decisions=decision)

    def test_delta_rejects_changed_scene_and_disguised_reformulation(self):
        before = self.packet(); a = self.save(before, "before.json")
        changed = copy.deepcopy(before); changed["scene_id"] = "a-different-event"
        b = self.save(changed, "different.json")
        with self.assertRaisesRegex(AVError, "same scene"):
            scene_claim_delta(a, b, self.root / "different-delta")
        changed = copy.deepcopy(before); changed["claims"][0]["statement"] = "A new formulation of the synthetic claim."
        b = self.save(changed, "reformulated.json")
        decision = self.save(self.delta_decision("PRESERVE"), "delta-decisions.json")
        with self.assertRaisesRegex(AVError, "PRESERVE cannot"):
            scene_claim_delta(a, b, self.root / "hidden-revision", decisions=decision)

    def test_removed_claim_cannot_be_marked_preserved(self):
        before = self.packet(); after = copy.deepcopy(before)
        after["claims"] = []; after["dependencies"] = []
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        decision = self.save(self.delta_decision("PRESERVE"), "delta-decisions.json")
        with self.assertRaisesRegex(AVError, "PRESERVE cannot"):
            scene_claim_delta(a, b, self.root / "removed-preserved", decisions=decision)

    def test_reconciled_state_change_is_a_delta_even_with_same_claim_record(self):
        before = self.packet()
        before["observations"][0]["status"] = "SUPPORTED"; before["claims"][0]["status"] = "SUPPORTED"
        self.decide(before, {"o-text": "SUPPORTED", "c-text": "SUPPORTED"})
        after = copy.deepcopy(before)
        after["adjudication"]["decisions"]["o-text"] = {"state": "CONTRADICTED", "reason": "An explicit local lexical correction."}
        after["adjudication"]["decisions"].pop("c-text")
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        output = self.root / "state-delta"; scene_claim_delta(a, b, output)
        row = read_json(output / "claim-delta.json")["claims"][0]
        self.assertEqual(row["before"], row["after"])
        self.assertEqual(row["before_state"], "SUPPORTED"); self.assertEqual(row["after_state"], "RECHECK")
        self.assertEqual(row["disposition"], "OPEN")

    def test_delta_detects_changed_transitive_premise_without_new_adjudication(self):
        before = self.packet()
        text2 = self.root / "second-textual-witness.json"
        write_json(text2, {"line": "A different generated statement."})
        before["evidence"]["TXT"].append(self.ref("txt2", text2, authority="best_available_text"))
        before["observations"].append(self.obs("o-second", "wording", "txt2"))
        before["claims"].append(self.claim("c-second", "interpretation", "o-second", inference="A declared synthetic inference."))
        before["dependencies"] += [self.edge("o-second", "c-second"), self.edge("c-text", "c-second", "CONSTRAINS")]
        after = copy.deepcopy(before)
        after["observations"][0]["statement"] = "Changed wording in the transitive ancestor of the second claim."
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        output = self.root / "ancestor-delta"; scene_claim_delta(a, b, output)
        rows = {r["claim_id"]: r for r in read_json(output / "claim-delta.json")["claims"]}
        self.assertEqual(rows["c-text"]["disposition"], "OPEN")
        self.assertEqual(rows["c-second"]["disposition"], "OPEN")

    def test_delta_detects_changed_transitive_artifact_hash(self):
        before = self.packet()
        second = self.root / "transitive-second.json"; write_json(second, {"line": "A separate generated direct premise."})
        before["evidence"]["TXT"].append(self.ref("txt2", second, authority="best_available_text"))
        before["observations"].append(self.obs("o-second", "wording", "txt2"))
        before["claims"].append(self.claim("c-second", "interpretation", "o-second", inference="A declared generated inference."))
        before["dependencies"] += [self.edge("o-second", "c-second"), self.edge("c-text", "c-second", "CONSTRAINS")]
        changed_text = self.root / "transitive-changed.json"; write_json(changed_text, {"line": "Changed generated ancestral wording."})
        after = copy.deepcopy(before)
        after["evidence"]["TXT"][0] = self.ref("txt", changed_text, authority="canonical_text", record_pointer="/line")
        a = self.save(before, "before.json"); b = self.save(after, "after.json")
        output = self.root / "ancestor-hash-delta"; scene_claim_delta(a, b, output)
        rows = {r["claim_id"]: r for r in read_json(output / "claim-delta.json")["claims"]}
        self.assertIn("txt", rows["c-second"]["relevant_new_evidence"])
        self.assertEqual(rows["c-second"]["disposition"], "OPEN")

    def test_public_schemas_validate_real_contract_outputs(self):
        import jsonschema
        schemas = Path(__file__).resolve().parents[1] / "schemas"
        packet = self.packet(); path = self.save(packet)
        reconcile_scene(path, self.root / "schema-reconcile")
        plan_scene_queries(path, self.root / "schema-plan")
        scene_claim_delta(path, path, self.root / "schema-delta")
        outputs = {"scene-packet": packet,
                   "scene-reconciliation": read_json(self.root / "schema-reconcile" / "reconciliation.json"),
                   "scene-query-plan": read_json(self.root / "schema-plan" / "query-plan.json"),
                   "scene-claim-delta": read_json(self.root / "schema-delta" / "claim-delta.json")}
        for name, value in outputs.items():
            with self.subTest(schema=name):
                schema = read_json(schemas / (name + ".schema.json"))
                jsonschema.Draft202012Validator.check_schema(schema)
                jsonschema.validate(value, schema)
        # A richer packet includes both stages, a scoped human observation,
        # an incompatible still-motion proposal, and a lexical disagreement.
        rich = self.packet()
        human = self.root / "human-listening.json"
        write_json(human, {"schema": "ave.human-listening-observation.v1", "actual_audio_inspection_declared": True,
                          "reviewer": "Synthetic declared-review fixture", "source_sha256": self.source_hash,
                          "stream_index": 0, "proposition": "delivery", "observation": "A scoped synthetic contract declaration.",
                          "inspected_intervals_seconds": [[.2, .8]]})
        rich["evidence"].update(AO_STAGE1=[self.ref("ao1", self.a1, channel="AO_STAGE1")],
                                AO_STAGE2=[self.ref("ao2", self.a2, channel="AO_STAGE2", stage1_evidence_id="ao1", context_evidence_ids=["txt"])],
                                HL=[self.ref("human", human, channel="HL")], VIS=[self.ref("vis", self.still)])
        rich["observations"] += [self.obs("o-guess", "wording", "ao1"), self.obs("o-context", "wording", "ao2"),
                                 self.obs("o-human", "delivery", "human"), self.obs("o-motion", "motion", "vis")]
        rich["conflicts"] = [{"id": "lexical-conflict", "proposition": "wording", "observation_ids": ["o-text", "o-guess", "o-context"],
                              "question": "Which exact lexical witness is authoritative?"}]
        rich["claims"].append(self.claim("c-delivery", "delivery", "o-human"))
        rich["dependencies"].append(self.edge("o-human", "c-delivery"))
        rich_path = self.save(rich, "rich-schema.json")
        result = reconcile_data(*load_scene_packet(rich_path)[:4])
        jsonschema.validate(rich, read_json(schemas / "scene-packet.schema.json"))
        jsonschema.validate(result, read_json(schemas / "scene-reconciliation.schema.json"))


if __name__ == "__main__":
    unittest.main()
