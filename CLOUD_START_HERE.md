# Run in a ChatGPT-style execution environment

The cloud edition includes Linux x64 native wheels for CPython 3.11 and 3.12,
glibc 2.17+. Code execution and file access must actually be enabled in the
receiving environment. A ZIP cannot grant those capabilities or auditory
perception. The core path does not call a paid API or upload media.

Locate the actual uploaded archive and choose a writable NEW work directory.
Paths below are examples, not assumptions about the host. Use this template
from a notebook/code tool:

```python
from pathlib import Path
import subprocess, sys, zipfile

upload = Path('/mnt/data/AV-Evidence-Toolkit-1.5.0a2-cloud.zip')
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
toolkit = destination / 'av-evidence-toolkit-1.5.0a2'
subprocess.run([sys.executable, str(toolkit/'scripts/verify_transfer.py')], check=True)
# Use bundled wheels on Linux x64 Python 3.11/3.12. On another ABI, explicitly
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

Use `ave(...)` in place of `python -m avevidence` in the workflow. The wrapper
launches a fresh process using the private toolkit so already imported notebook
packages cannot silently substitute another toolkit version. The setup receipt
states whether dependencies came from matching bundled wheels or the host.

FFmpeg and ffprobe are required for media decoding. If missing, the environment
must supply them or allow its usual free installation route. Decoder availability
is checked separately from Python imports. Do not copy Windows launcher shims
into Linux or invoke a VM to make this package work.

Upload media separately or use a mounted authorized source. Verify external
Japanese subtitle correspondence. Local paths in old receipts do not transfer
the original media. Model-free contours, queries, frames, reuse and review work
without alignment weights. Optional Qwen alignment requires its separately
installed runtime and model; canonical text remains authoritative.

Use actual host image, audio or video interfaces where available. Persist their
real outcomes in capability declarations. A decoded or playable WAV does not
prove that the model used auditory content. No real auditory observer adapter
is configured in this release; the CLI mock is a contract test, not listening.

A localhost review server may not be visible in the user's browser. Return a
verified review ZIP with HTML, bounded clips and durable JSON notes fields.
Temporary widgets or browser storage are not durable feedback. Save compact
evidence, source receipts and reports before an ephemeral execution session ends.
Keep large PCM caches out of the handoff unless specifically required.

Read [START_HERE.md](START_HERE.md) for setup variants and
[reports/PACKAGE_VALIDATION.md](reports/PACKAGE_VALIDATION.md) for executed scope.
Bundled Linux dependencies are not themselves proof of native Linux testing.


Explicit `--method` always takes precedence, including in isolated/blind mode. Episode isolation constrains semantic evidence independently. See docs/METHOD_PRECEDENCE_PATCH.md and the current workflow; the embedded method is a fallback only.
