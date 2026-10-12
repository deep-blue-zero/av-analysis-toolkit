# Repository layout and historical paths

Start at the root [README](../README.md). Commands and code paths in the guides
are relative to the repository or extracted portable toolkit root; Markdown links
are relative to the document containing them.

| Location | Contents |
| --- | --- |
| Root | README, AGENTS, changelog, licensing/notices, packaging metadata, `avtool.py` |
| `docs/` | Onboarding, workflows, limitations, validation and component guides |
| `docs/specs/` | The two versioned implementation contracts |
| `docs/prompts/` | Reusable agent handoff prompt |
| `requirements/` | Exact platform-specific offline dependency locks |
| `avevidence/`, `schemas/`, `examples/`, `tests/`, `scripts/`, `.github/` | Runtime, contracts, examples, validation, build tools and CI |

Historical manifests, source inventories, review receipts and files under
`docs/history/` retain their original bytes, paths and digests. A historical path
describes the source layout at its recorded commit; it is not a claim about the
current checkout. Use that exact commit to reproduce an old source binding.
This relocation does not renew or inherit an earlier validation result.

| Historical root path | Current path |
| --- | --- |
| `AGENT_START_HERE.md` | `docs/AGENT_START_HERE.md` |
| `START_HERE.md` | `docs/START_HERE.md` |
| `CLOUD_START_HERE.md` | `docs/CLOUD_START_HERE.md` |
| `ANALYSIS_WORKFLOW.md` | `docs/ANALYSIS_WORKFLOW.md` |
| `KNOWN_LIMITATIONS.md` | `docs/KNOWN_LIMITATIONS.md` |
| `NEXT_VALIDATION_PLAN.md` | `docs/NEXT_VALIDATION_PLAN.md` |
| `AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md` | `docs/specs/AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md` |
| `HARDENING_SPEC_1.1.0_RC2.md` | `docs/specs/HARDENING_SPEC_1.1.0_RC2.md` |
| `PROMPT_FOR_OTHER_AGENTS.md` | `docs/prompts/PROMPT_FOR_OTHER_AGENTS.md` |
| `requirements-cloud-cp311.txt` | `requirements/requirements-cloud-cp311.txt` |
| `requirements-cloud-cp312.txt` | `requirements/requirements-cloud-cp312.txt` |
| `requirements-windows-offline-cp312.txt` | `requirements/requirements-windows-offline-cp312.txt` |

The requirement-lock bytes are unchanged. Active source/portable builders and
the cloud setup message use the current paths. Generic guides and build scripts
are carried by the source distribution and portable ZIP; the wheel intentionally
installs runtime modules and licensing metadata rather than a documentation tree.
For a wheel-only installation, read the guides in the
[canonical repository](https://github.com/deep-blue-zero/av-analysis-toolkit).
