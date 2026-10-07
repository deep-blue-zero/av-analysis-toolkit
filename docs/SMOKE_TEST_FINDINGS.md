# Findings from the corrected toolkit's real-video trial

The supplied 54.506-second screen recording was useful for both interpretation and robustness testing. The original video yielded source-PTS stills successfully. Audio timestamps accumulated 41 one-sample overlaps (0.854167 ms total), causing the canonical decoder to refuse an unqualified source-timed audio run.

An explicitly documented consecutive-sample WAV, with ordered decoded sample equality and known stereo layout verified, allowed acoustic exploration. Its clock remains distinct from the original MP4. This workaround was implemented in the accompanying example workflow, not by relaxing the toolkit's decoder or mapping policy. Full decoder-frame timing records accompany the results.

The combined caption, sampled-expression and measured-mix evidence supports local scene interpretations. It does not make computational analysis equivalent to direct listening, make sampled frames equivalent to continuous motion, or turn source mixing measurements into speaker/emotion estimates. This distinction permits useful inferences while preserving the basis of each claim.

Possible future work is a separately specified recorder-clock policy that reports accumulated disagreement and uncertain source relations without promoting it into rate-one AV equivalence. It needs its own acceptance criteria and retiming regressions. This patch does not implement or silently enable that feature.

See the delivery's `real-video-smoke/ANALYSIS.md`, `MEASUREMENT_SUMMARY.json`, `REVIEW_ACCOUNTING.json`, and `clock-analysis/clock-diagnostic.json` for this particular test. Source-package validation is in [PATCH_VALIDATION](../reports/PATCH_VALIDATION.md).
