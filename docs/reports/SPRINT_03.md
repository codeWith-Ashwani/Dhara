# Sprint 3 report - community trust and governed fusion

## Outcome

Sprint 3 delivers the shadow-mode Loop B community-evidence pipeline and the governed boundary that combines crowd confidence `R` with sensor confidence `S`. The required adversarial exit test passes: fifteen reports from one device cannot create a Warning, while four independent reports distributed across four sub-cells can corroborate a strong sensor event.

No public delivery is enabled. A committed Warning is a draft and explicitly requires authorised officer sign-off.

## Built

- Canonical, timezone-aware report submission with device ID, monotonic counter, location accuracy, capture channel, media reference, attestation token and signature.
- Signature-verifier, device-attestation and content-classifier interfaces, with deterministic HMAC/static prototype adapters.
- Content class and confidence gate, perceptual-hash duplicate detection, capture-time checks and application/node attestation checks.
- Per-device replay protection and configurable report-rate quarantine.
- Retained quarantine records with machine-readable reasons; quarantined evidence cannot influence `R`.
- Reporter Beta posteriors with anonymous, citizen and verified-node priors, conservative trust (`mean - standard deviation`), outcome updates and inactivity decay.
- Trust, visual-confidence and GPS-integrity contribution weights.
- Thirty-minute crowd window, exponential recency decay, one-contribution-per-device and geohash-7 sub-cell caps, noisy-OR aggregation and spatial-spread damping.
- Default independence gate of four devices across three sub-cells, with a verified-node sparse-zone rule of two devices across two sub-cells.
- Governed fusion with explicit thresholds, stream-divergence diagnostics, two-cycle escalation and three-cycle de-escalation.
- Dual-stream Warning invariant: both `S >= 0.5` and `R >= 0.5` are required even when the fused score crosses the Warning threshold.
- Community intake, crowd assessment and fusion evaluation API endpoints.
- Deterministic adversarial replay script and JSON evidence artifact.

## Adversarial verification

The committed machine-readable result is `docs/reports/sprint3_adversarial_results.json`.

### Same-device attack

- Submitted: 15
- Accepted before aggregation caps: 5
- Quarantined by the rate gate: 10
- Independent devices/sub-cells contributing: 1/1
- Contributing reports after independence caps: 1
- Crowd confidence `R`: `0.012520`
- Sensor confidence `S`: `0.95`
- Fused confidence `F`: `0.501558`
- Committed tier after two cycles: Advisory
- Outcome: attack blocked; divergence sent for human review; no Warning or officer-signoff request created.

### Independent corroboration

- Submitted/accepted: 4/4
- Independent devices/sub-cells: 4/4
- Crowd confidence `R`: `0.824835`
- Sensor confidence `S`: `0.95`
- Fused confidence `F`: `0.884759`
- First cycle: candidate Warning, committed Monitor
- Second cycle: committed Warning draft
- Outcome: corroboration accepted and authorised officer sign-off required.

## Verification

- 28 automated tests passed.
- Package coverage: 89%.
- Ruff linting passed.
- The replay returned success only after all three acceptance flags were true: same-device Sybil blocked, independent-cluster Warning drafted, and officer sign-off required.

## Safety and governance controls

- Community and sensor inference remain separate until the fusion boundary.
- Quarantine preserves suspicious evidence for audit without silently deleting it.
- High `S` alone and high `R` alone cannot produce a Warning.
- Divergence is visible and direction-specific rather than averaged away.
- Hysteresis prevents a single evaluation cycle from changing the operational tier.
- The API has no push, SMS, IVR, CAP or other public-warning side effect.

## Deliberate prototype substitutions

- HMAC signatures stand in for hardware-backed device keys.
- Static tokens stand in for Play Integrity/App Attest and verified-node device attestation.
- Deterministic content results stand in for a reviewed flood-image classifier and production perceptual-hash service.
- Report, trust and fusion state are in memory; production requires transactional persistence, distributed rate limits and auditable policy-version storage.
- Locations and reports in the replay are synthetic software fixtures, not operational observations.

## Sprint 4 handoff

Sprint 4 can consume each fusion decision, its contributing report IDs, independence counts, divergence state, pending hysteresis cycles and sign-off requirement. The next work is the evidence dossier, operator map and decision rail, immutable decision audit, reviewed alert templates, sandbox delivery adapters, offline report codec and CAP v1.2 composition.
