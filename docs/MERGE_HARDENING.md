# Merge hardening

The music-identity pull request received two unresolved review findings after
its original validation. A later real-media experiment also exposed an inherited
budget comparison defect. These corrections preserve the previous receipts and
failed attempts; they do not grant a new perceptual qualification.

## Corrections

- AcoustID validates the configured 1-100 candidate ceiling before transport.
  It validates the full bounded response, deduplicates candidates, and rejects
  an over-ceiling result as unresolved while preserving the response digest.
  No rejected candidate ID reaches MusicBrainz normalization. This avoids
  silently discarding alternatives or exceeding the requested bound.
- All five music-identity scene routes accept intervals of at most 120 seconds.
  Larger objects remain open for explicit interval refinement; their complete
  original interval is retained as context and is never arbitrarily split.
- The scene-plan schema admits selected music audio streams and retains the
  missing-selection, no-default-track and legacy-readability contracts.
- The hosted audio adapter uses the existing validated decimal money conversion
  for both operands when comparing the request limit with the section ceiling.
  Equal $0.30 limits are accepted, while higher request limits, unauthorized
  uploads, changed witnesses, exhausted budgets and unresolved usage remain
  blocked.

## Validation

Generated/local fixtures and mock transports exercise candidate boundaries,
deduplication and malformed responses; all identity routes at and over the
120-second boundary; valid and invalid music stream declarations; and equal,
lower and higher monetary limits with the existing accounting guards. No real
media upload, catalogue request or paid observer call is part of this validation.

The original music-identity implementation and regression records describe
commit 7b457a42878d104a1b6d4cb1a2c19fe38afc1987. A separate current hardening
receipt records the corrected implementation rather than rewriting those records.
