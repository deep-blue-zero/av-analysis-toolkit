# Dependency notices

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
