"""Review joins preserve audio/identity and cannot manufacture listening coverage."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import wave

from avevidence.common import AVError, read_json, sha256, write_json
from avevidence.performance import performance
from avevidence.performance_review import build_review, import_notes, empty_note, validate_notes, _write_audition
from avevidence.inventory import verify_run


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.audio = cls.root/'source.wav'
        cls.samples = (np.sin(np.arange(32000)*2*np.pi*220/16000)*14000).astype('<i2')
        with wave.open(str(cls.audio), 'wb') as file:
            file.setnchannels(1); file.setsampwidth(2); file.setframerate(16000)
            file.writeframes(cls.samples.tobytes())
        transcript = cls.root/'text.json'
        write_json(transcript, {'source_sha256': sha256(cls.audio), 'utterances': [
            {'utterance_id': 'cue-1', 'start_s': .2, 'end_s': 1.5, 'text': 'こんにちは'}]})
        cls.performance_run = cls.root/'performance'
        performance(cls.audio, cls.performance_run, cache_dir=cls.root/'cache', transcript=transcript, progress=False)
        cls.row = {'review_id': 'TEST-01', 'run': 'performance', 'source_sha256': sha256(cls.audio),
            'source_id': 'SOURCE-01', 'start_s': .25, 'end_s': 1.75,
            'questions': ['What is audible?'], 'title': 'Test', 'debt_ids': ['D1']}
        cls.config = cls.root/'review-config.json'
        write_json(cls.config, {'schema': 'ave.performance-review.config.v1',
            'title': 'Test </script> & source', 'reviews': [cls.row]})
        cls.review = cls.root/'review'
        build_review(cls.config, cls.review)
        cls.package = read_json(cls.review/'review.json')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def notes(self):
        return {'schema': 'ave.performance-review.notes.v1', 'bundle_key': self.package['bundle_key'],
                'records': [empty_note(self.package['reviews'][0])]}

    def test_playback_is_source_sample_equal_and_pending(self):
        clip = self.package['reviews'][0]['audio']
        with wave.open(str(self.review/clip['path']), 'rb') as file:
            actual = file.readframes(file.getnframes())
        self.assertEqual(actual, self.samples[4000:28000].tobytes())
        self.assertTrue(clip['native_samples_equal'])
        self.assertEqual(self.package['listening_status'], 'NOT_PERFORMED')
        self.assertEqual(self.package['reviews'][0]['observations'], [])
        verify_run(self.review, verify_sources=True)

    def test_placeholder_notes_import_without_inventing_observations(self):
        output = self.root/'import-empty'
        result = import_notes(self.review, self.review/'notes-template.json', output)
        self.assertEqual(result['metadata']['declared_observations'], 0)
        self.assertEqual(result['metadata']['debt_closure'], 'NOT_PERFORMED')

    def test_partial_declared_observation_retains_scope_and_pending_adoption(self):
        notes = self.notes()
        notes['records'][0].update(status='OBSERVED', reviewer='Synthetic test reviewer',
            observation='Synthetic assertion for validator test, not a real listening record.',
            uncertainty='Test data', inspected_intervals=[[.3, .5]])
        row = validate_notes(self.package, notes)[0]
        self.assertEqual(row['inspected_intervals'], [[.3, .5]])
        self.assertEqual(row['canonical_adoption'], 'PENDING_SEPARATE_ANALYTICAL_REVIEW')
        self.assertEqual(row['independent_verification'], 'NOT_PERFORMED')

    def test_wrong_bundle_missing_observation_and_outside_times_refused(self):
        notes = self.notes()
        notes['bundle_key'] = 'wrong'
        with self.assertRaises(AVError): validate_notes(self.package, notes)
        notes = self.notes(); notes['records'][0]['status'] = 'OBSERVED'
        with self.assertRaises(AVError): validate_notes(self.package, notes)
        notes = self.notes(); notes['records'][0]['inspected_intervals'] = [[0, 2]]
        with self.assertRaises(AVError): validate_notes(self.package, notes)

    def test_duplicate_and_overlapping_notes_refused(self):
        notes = self.notes(); notes['records'].append(deepcopy(notes['records'][0]))
        with self.assertRaises(AVError): validate_notes(self.package, notes)
        notes = self.notes(); notes['records'][0]['inspected_intervals'] = [[.3, 1], [.8, 1.3]]
        with self.assertRaises(AVError): validate_notes(self.package, notes)

    def test_interpretation_requires_counterreading(self):
        notes = self.notes()
        notes['records'][0].update(status='OBSERVED', reviewer='Test', observation='Synthetic',
            uncertainty='Test only', inspected_intervals=[[.3, .5]], interpretation='An inference')
        with self.assertRaises(AVError): validate_notes(self.package, notes)

    def test_av_declaration_requires_bound_video(self):
        notes = self.notes()
        notes['records'][0]['inspection_mode'] = 'synchronized_video'
        with self.assertRaises(AVError): validate_notes(self.package, notes)

    def test_audition_padding_preserves_requested_scope(self):
        path = self.root/'padded-config.json'
        write_json(path, {'schema': 'ave.performance-review.config.v1', 'reviews': [
            self.row | {'audition_start_s': 0, 'audition_end_s': 2}]})
        output = self.root/'padded-review'
        build_review(path, output)
        row = read_json(output/'review.json')['reviews'][0]
        self.assertEqual((row['start_s'], row['end_s']), (.25, 1.75))
        self.assertEqual((row['audio']['source_start_s'], row['audio']['source_end_s']), (0, 2))
        with wave.open(str(output/row['audio']['path']), 'rb') as file:
            self.assertEqual(file.readframes(file.getnframes()), self.samples.tobytes())

    def test_wrong_source_unsafe_id_and_outside_interval_refused(self):
        for suffix, changes in [('source', {'source_sha256': 'a'*64}), ('id', {'review_id': '../escape'}),
                                ('time', {'end_s': 3})]:
            path = self.root/(suffix+'.json')
            write_json(path, {'schema': 'ave.performance-review.config.v1', 'reviews': [self.row | changes]})
            with self.assertRaises(AVError): build_review(path, self.root/('bad-'+suffix))
            self.assertFalse((self.root/('bad-'+suffix)).exists())

    def test_frame_cannot_be_silently_joined_to_wrong_interval(self):
        path = self.root/'bad-frame.json'
        # Hash is genuine, but the frame time is out of range. It must fail before decoding.
        write_json(path, {'schema': 'ave.performance-review.config.v1', 'reviews': [self.row | {'frames': [
            {'path': str(self.audio), 'sha256': sha256(self.audio), 'source_id': 'SOURCE-01', 'source_time_s': 9}]}]})
        with self.assertRaises(AVError): build_review(path, self.root/'bad-frame')

    def test_timestamp_gap_is_never_collapsed_into_continuous_playback(self):
        decoded = {'sample_rate_hz': 16000, 'segments': [
            {'sample_start': 0, 'sample_end': 16000, 'source_start_seconds': 0., 'source_end_seconds': 1.},
            {'sample_start': 16000, 'sample_end': 32000, 'source_start_seconds': 2., 'source_end_seconds': 3.}]}
        with self.assertRaises(AVError): _write_audition(decoded, .5, 2.5, self.root, self.root/'bad.wav')

    def test_generated_html_escapes_script_text(self):
        document = (self.review/'review.html').read_text(encoding='utf-8')
        self.assertIn('Test &lt;/script&gt; &amp; source', document)
        self.assertIn('Test \\u003c/script>', document)
        self.assertNotIn('"title": "Test </script>', document)


if __name__ == '__main__':
    unittest.main()
