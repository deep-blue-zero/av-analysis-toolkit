# RC2 release validation workflow

One `select_members` implementation in `scripts/build_release.py` defines source
membership. The builder checks exact stored names, refuses stale reports and
casefold collisions, parses Python against the supported syntax baseline, verifies
version consistency and validates report bindings.

`reports/BUILD_REPRODUCIBILITY.json` carries the entire sorted non-report
path/SHA-256 member set, its count and its aggregate digest. Runtime/test bindings
in both regression reports must match the actual selected files. Wheel and real
media reports must match the current runtime bytes. Source bindings exclude
reports and generated `SHA256SUMS.txt`, avoiding self-reference; this exclusion is
not a claim that reports are unimportant. The final ZIP checksum inventory covers
all included reports as well.

## Rebuild a validated source release

```
python scripts/build_release.py /outside/new-release.zip
```

Missing/stale/mismatched current reports cause refusal. `--candidate` creates an
explicitly unvalidated staging ZIP, useful for running clean-tree tests before
reports exist. A candidate build is NOT a passing release validation. Do not
rename a candidate as a verified delivery without generating and checking reports.

Final archive hashes and final report-frozen rebuild observations are external
sidecars: embedding an archive's own hash or the report of its exact final rebuild
would change that archive. Internal reproducibility reports bind the exact
non-report members and describe candidate-stage observations. After reports are
frozen, rebuild twice and once from the extracted archive and record those actual
hashes externally. This separates actual observations from future gate promises.

## Native host verification

```
python scripts/verify_release.py /outside/new-native-validation
```

This performs two verified builds, verifies archive integrity, extracts into a new
directory, runs the suite there, and rebuilds from it. The three archive digests
must match; normal runtime checks, not Python assertions, enforce these gates.

On native Windows PowerShell:

```
./scripts/verify_release_windows.ps1 -Output C:\Temp\ave-rc2-native-check
```

The wrapper requires a Windows Python interpreter and refuses a Linux host.
Both scripts require a NEW output directory outside the tree. Use an interpreter
with dependencies installed and FFmpeg/ffprobe on PATH. Record the generated
`native_release_validation.json` and test reports without rewriting historical
validation reports from a different release.

Native Windows validation and a genuine user-produced LosslessCut clip remain
external release-candidate gates unless the release report explicitly records
them as executed. An FFmpeg stream-copy synthetic fixture is a useful analogue,
not evidence of exercising the LosslessCut graphical application.
