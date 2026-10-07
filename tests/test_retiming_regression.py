"""Actual packet-timing regressions; generated media is never perceptual review."""
import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest

from avevidence.cli import main
from avevidence.common import AVError, read_json, run
from avevidence.inventory import verify_run
from avevidence.mapping import timing_basis, validated_segments


class MappingTimingTests(unittest.TestCase):
    def mapping(self, values):
        fields = ('parent_start_seconds', 'parent_end_seconds',
                  'derivative_start_seconds', 'derivative_end_seconds')
        return {'segments': [dict(zip(fields, row)) for row in values],
                'timing_basis': timing_basis({'time_base': '1/1000'},
                                             {'time_base': '1/1000'})}

    def test_existing_rc2_fragmented_retiming_is_rejected(self):
        # Every individual segment is rate one; the continuous parent is not.
        mapping = self.mapping([(i/10, (i+1)/10, i/5, i/5+.1) for i in range(20)])
        with self.assertRaisesRegex(AVError, 'contiguous source onset offsets'):
            validated_segments(mapping)

    def test_small_incremental_drift_cannot_reset_the_budget(self):
        mapping = self.mapping([(0, 1, 0, 1), (1, 2, 1.0015, 2.0015),
                                (2, 3, 2.003, 3.003)])
        with self.assertRaisesRegex(AVError, 'contiguous source onset offsets'):
            validated_segments(mapping)

    def test_real_parent_gap_can_start_an_independent_mapping(self):
        mapping = self.mapping([(0, 1, 0, 1), (2, 3, 10, 11)])
        self.assertEqual(len(validated_segments(mapping)[0]), 2)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class PacketTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ave-retiming-')
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        if not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError('Unexpected test scratch directory')
        self.tmp.cleanup()

    def assert_refused(self, parent, clip, end, reason):
        out = self.root/'admitted'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['admit-external-clip', str(parent), str(clip), str(out),
                         '--parent-start', '0', '--parent-end', str(end)])
        self.assertEqual(code, 3)
        data = read_json(out/'external_clip.json')
        self.assertEqual(data['overall_status'], 'PACKET_IDENTITY_VERIFIED_REVIEW_MAPPING_INDETERMINATE')
        self.assertEqual(data['review_mappings'], [])
        modality = data['modalities'][0]
        self.assertEqual(modality['status'], 'VERIFIED_PACKET_SUBSEQUENCE')
        self.assertFalse(modality['review_ready'])
        self.assertFalse(modality['claimed_interval_covered'])
        self.assertIn(reason, modality['review_limitation'])
        self.assertNotIn('packet_anchored_normalized_segments', modality)
        verify_run(out)
        return modality

    def test_twofold_video_retiming_keeps_identity_but_refuses_review(self):
        parent, clip = self.root/'parent.mkv', self.root/'stretched.mkv'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
             'testsrc2=size=128x96:rate=10:duration=2', '-c:v', 'ffv1', '-g', '1', parent])
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', parent, '-map', '0:v:0',
             '-c', 'copy', '-bsf:v', 'setts=pts=2*PTS:dts=2*DTS:duration=2*DURATION', clip])
        modality = self.assert_refused(parent, clip, 2, 'Retimed packet onsets')
        self.assertAlmostEqual(modality['maximum_mapping_offset_spread_seconds'], 1.9)

    def test_retimed_audio_packets_are_also_refused(self):
        parent, clip = self.root/'parent.mka', self.root/'stretched.mka'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
             'sine=frequency=457:sample_rate=48000:duration=2', '-c:a', 'flac', parent])
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', parent, '-c', 'copy',
             '-bsf:a', 'setts=pts=2*PTS:dts=2*DTS:duration=2*DURATION', clip])
        self.assert_refused(parent, clip, 2, 'Retimed packet onsets')

    def test_single_video_packet_duration_change_is_not_normalized_away(self):
        parent, clip = self.root/'single.mov', self.root/'slower.mov'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
             'testsrc2=size=128x96:rate=10:duration=0.1', '-c:v', 'qtrle', parent])
        run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', parent, '-c', 'copy',
             '-bsf:v', 'setts=duration=2*DURATION', clip])
        self.assert_refused(parent, clip, .1, 'Retimed video packet durations')


if __name__ == '__main__':
    unittest.main()
