# Canonical source and distribution builds

The canonical software source is `deep-blue-zero/av-analysis-toolkit`. Git stores
first-party Python, tests, generic documentation, schemas/examples, notices and
build scripts. Episode media, analytical conclusions, runtime evidence, model
weights, downloaded wheels and virtual environments remain external.

`pyproject.toml` is the package definition. The existing flat `avevidence/`
layout and `ave` CLI are retained. Install the core with `python -m pip install .`;
select existing optional extras for the features needed. Development installation
is `python -m pip install ".[all,reuse-parquet,dev]"`. Tests use Python's standard
`unittest` runner; `dev` supplies wheel/sdist build tools and JSON-schema
validation. The runtime-only offline profile does not supply the full development
test dependencies. Qwen alignment is a
separate optional installation and model acquisition, never a CI requirement.

FFmpeg/ffprobe are external system dependencies, checked by `ave doctor`. On
Debian/Ubuntu use the system package manager's `ffmpeg` package; on Windows install
FFmpeg through an established package manager and make both executables available
on PATH. No decoder binaries are tracked or copied into these distributions.

## Wheel and source distribution

From a committed source tree with development dependencies installed:

```text
python scripts/build_distributions.py --output ../distribution-build
```

Choose a new output directory outside the repository. The script builds one
wheel and one source distribution without build isolation or network downloads;
missing build tools fail explicitly. A fresh external source snapshot prevents
stale checkout build caches from entering the distributions. The builder checks
exact runtime-module agreement, sdist content policy and source correspondence,
then installs each distribution
in a separate environment and checks imports and CLI launch from outside the
source tree. Installation checks explicitly expose the build interpreter's
already installed dependency directories to each fresh environment and execute a
small Pillow operation;
they prove distribution installation, not independent dependency acquisition.
CI separately installs dependencies from repository metadata.

`BUILD_PROVENANCE.json` records commit identity, source hashes, dirty status,
executed platform and checks. `SHA256SUMS` binds the wheel and sdist. A candidate
may use `--allow-dirty`; its receipt includes changed-source hashes and must not be
described as an unmodified commit build. Generated outputs do not belong in Git.

`MANIFEST.in` includes the first-party source needed to build/test an sdist and
excludes runtime artifacts. The wheel contains the runtime package and licensing
metadata. Generic documentation and test scripts are available in the sdist and
Git rather than installed as runtime package data.

## Optional portable/offline artifacts

The previous standard-library `cloud_setup.py` installer and `cloud_run.py`
wrapper are preserved. They install matching wheels into a private directory
without pip, root access or network access. Git contains their implementation;
wheels are supplied externally only when producing an optional release artifact.

```text
python scripts/build_portable.py --sdist ../distribution-build/av_evidence_toolkit-1.6.0a1.tar.gz --wheel ../distribution-build/av_evidence_toolkit-1.6.0a1-py3-none-any.whl --wheelhouse /external/wheelhouse --target linux-cp312 --parselmouth-source /external/praat_parselmouth-0.4.7.tar.gz --output /external/av-toolkit-linux-cp312.zip
```

Targets retain the existing offline profiles: Linux x64 CPython 3.11 or 3.12
(glibc 2.17+), and Windows x64 CPython 3.12. For Linux put the external four-wheel
set in `wheelhouse/linux/cp311` or `wheelhouse/linux/cp312`; for Windows use the
wheelhouse root. Exact NumPy/Pillow/Parselmouth/SciPy versions come from the
preserved text locks. The builder checks distribution names, versions, ABI/OS
tags, source/wheel module equality and all transfer hashes. It includes the
unmodified corresponding Parselmouth source archive to retain the previous
GPL-dependency distribution materials. Wheel-contained upstream licenses remain
intact. This packaging policy is not a legal determination.

The cloud locks use SciPy 1.15.3; the original Windows offline profile uses SciPy
1.18.0. The Windows lock was recovered without edits from
`AV-Evidence-Toolkit-1.5.0a2-windows-offline.zip` (archive SHA-256
`2f4fad830e82415ec72e2cd7b146f14523445dcdab7a4b2d17d60a420c8c481a`). Its
`requirements-windows-offline-cp312.txt` SHA-256 is
`09b6abfda71e5779e55d58993a2d68a301dd456ad0d36321622486997ce63a10`.
Target-specific lock selection preserves both existing offline routes; the
Windows profile is not silently replaced with the Linux dependency versions.

The resulting ZIP has the existing transfer manifest and checksum format. It
supports private-runtime offline setup and the explicit compatible-host mode.
The builder does not execute the target's native wheels; run `verify_transfer.py`,
`cloud_setup.py` and `portable_smoke.py` on the actual target before declaring that
platform validated. FFmpeg, media and model weights are separate. A code-execution
enabled cloud environment is necessary; an archive cannot grant execution or
auditory perception. Portable artifacts include only one selected native profile.

## Zero-cost CI

The workflow tests Linux CPython 3.11/3.12 and Windows CPython 3.12, matching the
existing portable profile targets. It installs dependencies through package
managers, makes FFmpeg available, checks Python syntax, runs the full generated
synthetic regression suite, builds both distributions and verifies installations.
Windows CI pins the locally tested FFmpeg 8.1.1. Linux uses the distribution's
decoder; a test of modern filter-file syntax is skipped when that syntax is not
available, while the selected legacy route is still executed. See
[decoder scope](BACKWARD_COMPATIBILITY.md) for retained failures and limits.
Test receipts preserve exact failures/errors/skips. Workflow artifacts retain
the distributions, hashes and build receipt; generated binaries are never committed.

No API credentials are supplied and paid-test gating is disabled. Normal CI
performs no hosted observer calls. Successful CI establishes software behavior
on that executed target, never auditory qualification, native continuous-video
perception or global model accuracy. Future paid integration must remain separately
opt-in, authorized, budget limited and usage receipted.
