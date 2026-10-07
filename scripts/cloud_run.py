#!/usr/bin/env python3
"""Run the toolkit from its private cloud runtime; no shell, GUI or service needed."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.cloud-runtime')
    parser.add_argument('--where', action='store_true')
    args, commands = parser.parse_known_args()
    runtime = args.runtime.resolve()
    if not (runtime / 'avevidence/__init__.py').is_file():
        raise SystemExit('Private runtime is missing; run scripts/cloud_setup.py first')
    sys.path.insert(0, str(runtime))
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    import avevidence
    if not Path(avevidence.__file__).resolve().is_relative_to(runtime):
        raise SystemExit('Unexpected import outside the selected private runtime')
    if args.where:
        import numpy, scipy, parselmouth
        from PIL import Image
        # Imports alone are not an execution check for native extensions.
        import math
        t = numpy.arange(16000)/16000
        pitch = parselmouth.Sound(.2*numpy.sin(2*math.pi*220*t), sampling_frequency=16000).to_pitch_ac()
        frequencies = pitch.selected_array['frequency']
        measured = float(numpy.median(frequencies[frequencies>0]))
        from scipy.signal import resample_poly
        minimums={'Pillow':(10,0),'numpy':(1,26),'scipy':(1,11)}
        for name,minimum in minimums.items():
            version=importlib.metadata.version(name)
            import re
            numbers=tuple(map(int,re.findall(r'\d+',version)[:2]))
            if numbers<minimum or (name=='numpy' and numbers[0]>=3):
                raise SystemExit(f'Incompatible preinstalled dependency: {name} {version}')
        if importlib.metadata.version('praat-parselmouth') != '0.4.7':
            raise SystemExit('This release requires validated praat-parselmouth 0.4.7')
        if not abs(measured-220)<2 or len(resample_poly(t,1,2)) != 8000:
            raise SystemExit('Preinstalled native dependencies failed pitch/resampling execution checks')
        image=Image.new('RGB',(2,2)); image.getpixel((0,0))
        print(json.dumps({'module': avevidence.__file__, 'version': avevidence.__version__,
                          'packages': {n: importlib.metadata.version(n) for n in
                                       ['Pillow', 'numpy', 'scipy', 'praat-parselmouth']},
                          'runtime': str(runtime), 'native_execution_check': 'PASS'}, indent=2))
        return
    if commands and commands[0] == '--':
        commands = commands[1:]
    sys.argv = ['ave', *commands]
    from avevidence.cli import main as cli_main
    raise SystemExit(cli_main())


if __name__ == '__main__':
    main()
