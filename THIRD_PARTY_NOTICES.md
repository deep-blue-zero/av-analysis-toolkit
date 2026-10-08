# Dependency notices

Optional music identification calls a separately installed
[Chromaprint/fpcalc](https://github.com/acoustid/chromaprint) executable. No binary
or source is bundled. Chromaprint uses the LGPL; an installed binary's FFT/
FFmpeg dependencies can add GPL obligations. Preserve its distribution's notices
if redistributing it. AcoustID and MusicBrainz are external services; their service
and metadata terms apply. The Python `music` extra reuses NumPy/SciPy declarations.

The following notice describes the supplied portable release and optional
offline artifacts. The canonical Git source externalizes downloaded wheels and
the dependency source archive; they are not tracked here. `build_portable.py`
requires those external inputs when constructing an offline artifact. Exact
artifact inventory and hashes are generated at build time. Native upstream
license metadata remains inside each unmodified wheel.

Toolkit source is MIT; inherited contributor notice is in `LICENSES/GBC_TOOLKIT_MIT.txt`.
Unmodified native wheels target Linux x64 CPython 3.11/3.12; glibc 2.17+. Their own metadata and license files
remain intact; readable copies are in `LICENSES/dependencies/`.

| Package | Version | Principal license |
|---|---|---|
| Pillow | 12.2.0 | MIT-CMU |
| NumPy | 2.1.2 | BSD plus bundled library notices |
| SciPy | 1.15.3 | BSD plus bundled library notices |
| praat-parselmouth | 0.4.7 | GPLv3 |

The corresponding unmodified Parselmouth source archive, including its Praat
source, is in `dependency-sources/`. All archive hashes are in the transfer
manifest. The exact wheel inventory and upstream links are in `DEPENDENCIES.json`.
Python, FFmpeg, PyTorch, alignment weights, optional librosa/SoundFile/PyArrow and
perceptual model backends are not bundled. Their own terms apply if installed.
