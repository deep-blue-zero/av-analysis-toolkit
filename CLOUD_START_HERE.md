# Run in a ChatGPT-style execution environment

The current source is 1.6.0a1. The receiving environment needs actual code
execution, file access and a writable working directory. A ZIP cannot grant
these capabilities or auditory/video perception. The standard core workflow
makes no paid API calls and uploads no media.

From the canonical Git checkout, install the core with
`python -m pip install .`, then run `ave doctor`. Select optional extras for
the features needed, such as `.[performance]` for pitch/intensity work.
Development setup is `.[all,reuse-parquet,dev]`. The bare checkout contains no
native dependency wheels, decoder binaries, episode media or model weights.
Use the host's ordinary free package-installation route where available.

The offline instructions below apply to a separately generated portable
artifact. Each artifact contains one selected target: Linux x64 CPython 3.11 or
3.12 (glibc 2.17+), or Windows x64 CPython 3.12. Consult its actual transfer
manifest and delivery profile; neither its filename nor compatible wheel tags
prove that it was executed on the receiving platform.

Locate the actual uploaded archive and choose a writable **new** work directory.
Replace the example upload path with that file. The toolkit root is discovered
from the extracted transfer manifest rather than assumed from a versioned name.

```python
from pathlib import Path
import subprocess, sys, zipfile

upload = Path('/mnt/data/ACTUAL_UPLOADED_ARCHIVE.zip')
destination = Path('/mnt/data/av-toolkit-work')
destination.mkdir(parents=True, exist_ok=False)
with zipfile.ZipFile(upload) as archive:
    for entry in archive.infolist():
        target = (destination / entry.filename).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError('Unsafe archive path')
        if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
            raise ValueError('Archive symlinks are not supported')
    archive.extractall(destination)

roots = [path.parent for path in destination.rglob('TRANSFER_MANIFEST.json')]
if len(roots) != 1:
    raise ValueError('Expected exactly one portable toolkit manifest')
toolkit = roots[0]
subprocess.run([sys.executable, str(toolkit/'scripts/verify_transfer.py')], check=True)
# Use the matching bundled ABI/platform profile. On another ABI, explicitly
# add '--use-host-dependencies' only if compatible native packages already exist.
subprocess.run([sys.executable, str(toolkit/'scripts/cloud_setup.py')], check=True)

def ave(*arguments):
    return subprocess.run(
        [sys.executable, '-I', '-X', 'utf8', str(toolkit/'scripts/cloud_run.py'),
         *map(str, arguments)], check=True, capture_output=True,
        text=True, encoding='utf-8')

print(ave('doctor').stdout)
subprocess.run([sys.executable, str(toolkit/'scripts/portable_smoke.py'),
                str(destination/'setup-smoke'), '--runtime',
                str(toolkit/'.cloud-runtime')], check=True)
```

Use `ave(...)` in place of the installed `ave` command in the workflows.
The wrapper launches a fresh process using this private toolkit, so notebook
imports cannot silently substitute another version. The setup receipt states
whether dependencies came from matching bundled wheels or the host. Offline
setup does not fetch a missing native profile or optional alignment model.

FFmpeg and ffprobe are required for decoding and checked separately from Python
imports. The destination must supply them or permit its usual installation
route. Do not copy Windows launcher shims into Linux or invoke a VM. Decoder
version matters: the current known-limits document records conservative
audio-coverage refusals reproduced with FFmpeg 9.0.2. A successful transfer/import
or setup smoke test does not establish full decoder compatibility.

Upload media separately or use a mounted authorized source. Verify Japanese
subtitle correspondence and timing. Paths in old receipts do not transfer the
original media. Model-free contours, queries, frames, reuse and review work
without alignment weights. Optional Qwen alignment needs its separate runtime
and model; canonical text remains distinct from phonetic timing.

The new [holistic workflow](docs/HOLISTIC_CROSS_MODAL_ANALYSIS.md) combines
separately attributed evidence in a bounded scene packet. `scene validate`,
`prepare`, `reconcile`, `plan` and `delta` run locally without providers.
[Temporal inspection](docs/TEMPORAL_INSPECTION.md) prepares source-clocked
sequences and scoped review records. Generated frames remain unreviewed until
actual inspection is separately declared; extraction cannot establish motion
perception, contact order or acting quality by itself.

Use actual host image, audio or video interfaces where available and record their
real outcomes. A decoded/playable WAV does not prove that the coordinating model
heard it. An optional hosted auditory adapter exists, with strict qualification,
authorization, credential, identity and budget guards. It never activates through
ordinary preparation or tests. All six new auditory task profiles remain
`UNQUALIFIED` / `NOT_TESTED`, and historical failed probes remain failed.
[A separate contextual second pass and scoped human review](docs/AUDITORY_TASK_PROFILES.md)
retain their provenance; neither globally qualifies a backend. No native
continuous-video observer is introduced by this release.

A localhost review server may not be visible in the user's browser. Return a
verified review ZIP with HTML, bounded clips and durable JSON notes fields.
Temporary widgets or browser storage are not durable feedback. Save compact
evidence, source receipts and reports before an ephemeral execution session ends.
Keep large PCM caches out of the handoff unless specifically required.

Read [START_HERE.md](START_HERE.md), [packaging and CI](docs/PACKAGING_AND_CI.md),
and [current limits](KNOWN_LIMITATIONS.md). Validation claims must name the exact
source, platform and receipt. Old portable-release reports do not certify the
current Git checkout or a newly generated artifact.

Explicit `--method` always takes precedence, including in isolated/blind mode.
Episode isolation constrains semantic evidence independently. The embedded method
is a fallback only; see [method precedence](docs/METHOD_PRECEDENCE_PATCH.md).
