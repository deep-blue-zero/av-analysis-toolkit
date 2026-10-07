# Explicit method precedence — 1.5.0a2

The previous deep-read expression selected the embedded method whenever episode
isolation was active. Blind export independently always wrote that same embedded
method. This incorrectly joined the analytical framework and evidence boundary.

Selection now follows exactly:

```python
if method is not None:
    # Verify/read the explicit UTF-8 file, retaining its original bytes.
    selected_method = explicit_file_bytes
    method_selection = "explicit"
else:
    selected_method = FALLBACK_GENERIC_METHOD.encode("utf-8")
    method_selection = "fallback"
```

Episode isolation is not an argument to the selector. It separately records
`semantic_evidence_boundary: supplied_source_only` and adds analyst instructions
excluding later episodes and external-series evidence. The embedded text was
renamed to `FALLBACK_GENERIC_METHOD` without rewriting its content.

Explicit bytes, including a UTF-8 BOM and CRLF line endings, remain unchanged in
`method.txt` or `analyst-context/method.txt`. Each run includes
`method-receipt.json`, matching source/copied hashes, explicit/fallback selection,
source filename and independent episode-isolation state. The original method is
also a hash-verified run dependency. A method changed during preparation cannot
publish successfully. Invalid paths, unreadable files and invalid UTF-8 fail
early; they do not downgrade to the fallback. An explicit empty file remains
explicit rather than being mistaken for an omitted option.

`blind-export CONFIG OUTPUT --method PATH` is the only newly added public flag.
Existing command names, positional arguments, run schemas and method copy
locations remain. Provenance fields and the receipt extend the existing layout.
Full source-path provenance stays outside the blind analyst context. An explicit
method may itself identify a series; exact preservation does not sanitize it.

Runtime files changed: `avevidence/analysis_benchmark.py`,
`avevidence/deep_read.py`, `avevidence/cli.py`, plus version bookkeeping in
`avevidence/__init__.py` and `pyproject.toml`. Documentation updates are in
README, AGENT_START_HERE, PROMPT_FOR_OTHER_AGENTS, the current-extension notice of
AUDIO_ANALYSIS_SPEC, COMMANDS and the claim-directed workflow/scope guides.

`tests/test_method_selection.py` adds 16 focused cases covering the four
explicit/fallback × isolated/ordinary combinations, both blind-export choices,
missing paths, a directory path, unreadability, invalid UTF-8, an empty path, an
explicit empty file, mutation during preparation and both public CLI routes.
The full source suite passed 277 tests, with no failures, errors or skips.

The example receipt in
`reports/method-precedence-1.5.0a2-example/deep-read/method-receipt.json` comes from
actual CLI preparation on a generated tone and an explicitly supplied custom
method. The source and copied SHA-256 both equal
`348f5cb821140a71d218a89cfdb89403736922d60cda8c6d785a61de6996f402`.
It is a fixture demonstration, not an anime reading or a receipt for the named
repository generic method. No substantive episode analysis was performed.

The user's episode acceptance run is pending the forthcoming media. The named
repository method was not found in the currently searched local repository,
Downloads or project roots; its actual available path/file is also needed for
that final repository-method check. A missing explicit method will fail rather
than silently substituting this toolkit's fallback.
