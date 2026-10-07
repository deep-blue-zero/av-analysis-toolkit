"""Seeking and packet checks protect the source clock used by the review UI."""
from copy import deepcopy
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from avevidence.common import AVError
from avevidence.performance_video import compare_packets, check_audio_frames
from avevidence.review_server import ReviewHandler


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.parent = [
            {'stream_index': 0, 'data_hash': 'SHA256:video', 'pts_time': '0.000', 'dts_time': '0.000'},
            {'stream_index': 1, 'data_hash': 'SHA256:audio', 'pts_time': '0.000', 'dts_time': '0.000'}]
        self.indices = {'video': 0, 'audio': 1}

    def compare(self, child):
        return compare_packets(self.parent, child, self.indices, self.indices)

    def test_payload_identity_allows_only_recorded_timebase_rounding(self):
        child = deepcopy(self.parent)
        child[1]['pts_time'] = '0.000522'
        receipt = self.compare(child)
        self.assertTrue(receipt['audio']['encoded_payloads_equal'])
        self.assertEqual(receipt['audio']['maximum_pts_or_dts_difference_seconds'], .000522)

    def test_missing_or_changed_packet_refused(self):
        with self.assertRaises(AVError): self.compare(self.parent[:1])
        child = deepcopy(self.parent)
        child[0]['data_hash'] = 'SHA256:changed'
        with self.assertRaises(AVError): self.compare(child)

    def test_retiming_or_unbound_clock_refused(self):
        child = deepcopy(self.parent)
        child[1]['pts_time'] = '0.1'
        with self.assertRaises(AVError): self.compare(child)
        child = deepcopy(self.parent)
        del child[0]['dts_time']
        with self.assertRaises(AVError): self.compare(child)

    def test_nonfinite_timestamps_cannot_pass_a_tolerance_check(self):
        for value in ['nan', 'inf', '-inf', 'not a number']:
            child = deepcopy(self.parent)
            child[0]['pts_time'] = value
            with self.assertRaises(AVError): self.compare(child)


class QuietHandler(ReviewHandler):
    def log_message(self, *args):
        pass


class AudioClockTests(unittest.TestCase):
    def test_container_subtitle_tail_does_not_define_audio_duration(self):
        frames = [{'pts_time': '0', 'nb_samples': 1024}, {'pts_time': '.023', 'nb_samples': 1024}]
        receipt = check_audio_frames(frames, 44100, 2048)
        self.assertTrue(receipt['verified'])
        self.assertEqual(receipt['sample_frames'], 2048)

    def test_gap_shift_or_wrong_sample_count_refused(self):
        for second in ['.04', '.01', 'nan']:
            frames = [{'pts_time': '0', 'nb_samples': 1024}, {'pts_time': second, 'nb_samples': 1024}]
            with self.assertRaises(AVError): check_audio_frames(frames, 44100, 2048)
        with self.assertRaises(AVError):
            check_audio_frames([{'pts_time': '0', 'nb_samples': 1024}], 44100, 2048)

    def test_larger_matching_tolerance_is_explicit_and_recorded(self):
        frames = [{'pts_time': '-0.002', 'nb_samples': 1024}]
        with self.assertRaises(AVError): check_audio_frames(frames, 44100, 1024)
        receipt = check_audio_frames(frames, 44100, 1024, .003)
        self.assertEqual(receipt['declared_alignment_tolerance_seconds'], .003)
        self.assertEqual(receipt['maximum_frame_pts_difference_from_sample_clock_s'], .002)
        with self.assertRaises(AVError): check_audio_frames(frames, 44100, 1024, .021)


class RangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.folder = cls.root/'public'
        cls.folder.mkdir()
        (cls.folder/'audio.wav').write_bytes(b'0123456789')
        (cls.root/'outside.txt').write_text('not served', encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(cls.folder)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def fetch(self, path='/audio.wav', range=None, method='GET'):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers={'Range': range} if range else {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_bounded_suffix_and_open_ranges(self):
        for value, expected, label in [('bytes=2-4', b'234', 'bytes 2-4/10'),
                ('bytes=-3', b'789', 'bytes 7-9/10'), ('bytes=8-', b'89', 'bytes 8-9/10')]:
            status, headers, data = self.fetch(range=value)
            self.assertEqual((status, data), (206, expected))
            self.assertEqual(headers['Content-Range'], label)
            self.assertEqual(int(headers['Content-Length']), len(data))

    def test_head_and_invalid_ranges(self):
        status, headers, data = self.fetch(range='bytes=3-5', method='HEAD')
        self.assertEqual((status, headers['Content-Length'], data), (206, '3', b''))
        for value in ['bytes=10-', 'bytes=5-2', 'bytes=-0', 'bytes=1-2,4-6']:
            status, headers, data = self.fetch(range=value)
            self.assertEqual((status, headers['Content-Range'], data), (416, 'bytes */10', b''))

    def test_directory_and_parent_files_not_served(self):
        self.assertEqual(self.fetch('/')[0], 403)
        status, _, data = self.fetch('/%2e%2e/outside.txt')
        self.assertIn(status, [403, 404])
        self.assertNotEqual(data, b'not served')


if __name__ == '__main__':
    unittest.main()
