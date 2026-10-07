"""File-backed frame selections survive legacy and current FFmpeg interfaces.

The modern option is documented at https://ffmpeg.org/ffmpeg.html#Options:
the leading slash loads an option value from a file. Fixtures model capability
help, rather than inferring support from a release-version number.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from avevidence.common import AVError, run
from avevidence.visual import _filter_file_option


class FilterOptionCapabilityTests(unittest.TestCase):
    def select(self, stdout='', stderr=''):
        response = subprocess.CompletedProcess(['ffmpeg'], 0, stdout, stderr)
        with patch('avevidence.visual.run', return_value=response) as probe:
            selected = _filter_file_option()
        probe.assert_called_once_with(['ffmpeg', '-hide_banner', '-h', 'full'])
        return selected

    def test_legacy_option_listing_uses_file_option(self):
        help_text = ('Advanced options:\n'
                     '-filter_script[:<stream_spec>] <filename>  read filter graph from file\n')
        self.assertEqual(self.select(stdout=help_text), '-filter_script:v')

    def test_legacy_deprecated_listing_on_stderr_remains_usable(self):
        help_text = ('-filter[:<stream_spec>] <filter_graph>  video filter\n'
                     '-filter_script[:<stream_spec>] <filename>  deprecated, use -/filter\n')
        self.assertEqual(self.select(stderr=help_text), '-filter_script:v')

    def test_current_listing_without_legacy_option_uses_documented_file_loading(self):
        help_text = ('Advanced options:\n'
                     '-filter[:<stream_spec>] <filter_graph>  apply a stream filter\n'
                     '-filter_complex <graph>  configure a graph\n')
        self.assertEqual(self.select(stdout=help_text), '-/filter:v')

    def test_removed_option_mentioned_in_prose_is_not_an_advertised_option(self):
        self.assertEqual(self.select(stderr='Removed flag -filter_script is mentioned in this diagnostic.\n'),
                         '-/filter:v')

    def test_help_failure_does_not_turn_into_a_guessed_supported_option(self):
        with patch('avevidence.visual.run', side_effect=AVError('FFmpeg unavailable')):
            with self.assertRaises(AVError):
                _filter_file_option()


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for generated-filter execution')
class FilterFileExecutionTests(unittest.TestCase):
    def execute_filter(self, option):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'selection with spaces.filter'
            script.write_text('scale=8:8,format=rgb24', encoding='utf-8')
            result = run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                          '-f', 'lavfi', '-i', 'color=c=red:size=16x16:rate=10:duration=0.1',
                          option, script, '-frames:v', '1', '-threads', '1', '-f', 'null', '-'])
            self.assertEqual(result.returncode, 0)

    def test_selected_file_option_executes_a_generated_frame(self):
        self.execute_filter(_filter_file_option())

    def test_documented_modern_file_loading_executes_when_host_advertises_it(self):
        response = run(['ffmpeg', '-hide_banner', '-h', 'full'])
        help_text = response.stdout + response.stderr
        if '-/filter' not in help_text and '-filter_script' in help_text:
            self.skipTest('Legacy host does not advertise slash-prefixed filter-file loading')
        self.execute_filter('-/filter:v')


if __name__ == '__main__':
    unittest.main()
