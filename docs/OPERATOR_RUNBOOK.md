# D.H.A.R.A. shadow-pilot operator runbook

## Scope and authority boundary

This runbook operates the Sprint 5 prototype only. D.H.A.R.A. is decision support: it does not
authorise evacuation, road closure or public warning. Only the relevant authority may make
those decisions. The repository is hard-locked to `shadow`; public delivery is disabled, CAP
messages use `Test`, delivery recipients are sandbox identifiers, and runtime translation is
disabled.

Stop immediately if any screen, log or response shows a non-shadow mode, CAP status other than
`Test`, an actual phone/token recipient, an unapproved language template, or a request to bypass
officer review.

## Start-of-shift checks

1. Start the API with `uvicorn dhara.api:app` and open `/operator`.
2. Verify `GET /health` returns `status=ok`, `mode=shadow`, and
   `public_delivery_enabled=false`.
3. Verify `GET /v1/safety` reports sandbox delivery enabled, public delivery disabled, CAP
   `Test`, and runtime translation disabled.
4. Confirm the intended database path and replay root. Do not use a copied production database
   for a demonstration.
5. Review `GET /v1/learning/status`. The learning audit chain must be valid. A missing latest run
   is expected before the first labelled rehearsal.
6. Record the operator, environment, code commit, start time and any degraded dependencies in
   the shift log.

## Triage workflow

1. Select the highest-priority case in `/operator`; urgency never removes the human-review
   requirement.
2. Inspect all six dossier panels. Check raw `S`, `R`, `F`, data freshness, attribution,
   quarantined reports, spatial independence, hysteresis and divergence.
3. Compare the no-crowd, no-sensor and weakest-report counterfactuals. Large tier movement is a
   reason to request verification, not a reason to hide the counterfactual.
4. If the streams diverge, read the direction-specific review question and seek the missing
   ground or instrument evidence.
5. Choose exactly one controlled action and supply the authenticated actor identity and a
   specific reason. In this prototype the actor field is caller-supplied; the pilot gateway must
   authenticate and bind it before use.
6. `confirm` writes a confirmed outcome; `reject_as_false` writes a refuted outcome. Outcomes
   cannot be edited or reversed. If adjudication is uncertain, request ground verification and
   do not create a final label.
7. Sandbox CAP escalation is permitted only for a committed Warning draft requiring sign-off.
   Verify all three channel receipts refer to the same message hash and the CAP document remains
   `Test`. A `sandbox_failed` receipt is a test-path failure, not a public delivery failure.

## Learning job

Run the job only after adjudicated labels have been reviewed for time, cell, evidence linkage
and classification. Submit an explicit timezone-aware `as_of` to `POST /v1/learning/run`.

- A repeated unchanged outcome set must return the original job with `reused=true`; trust must
  not change again.
- Verify `GET /v1/learning/status` reports a valid audit chain.
- Inspect each calibration candidate. `promoted=true` only means the replay Brier score improved
  with at least five labels. It does not authorise production activation.
- A degrading candidate must remain stored with `promoted=false`; the active probability remains
  unchanged.
- Review the cell policy's sample count, miss/false-alarm counts and five-to-one cost ratio.
  Prototype artifacts are advisory and must not silently replace authority-approved policy.
- `GET /v1/public/calibration` must suppress fewer than five labels and must never contain actor,
  report, reporter, notes or private input-digest fields.

## Degraded modes and response

| Condition | Expected behavior | Operator response |
| --- | --- | --- |
| Signature or attestation adapter unavailable | Report quarantined; contribution is zero | Record outage, inspect instrument stream, request ground verification |
| Content classifier unavailable | Report quarantined as `classifier_unavailable` | Do not manually mark it trusted; restore/review adapter |
| One sandbox channel fails | Failed receipt recorded; other channels attempted | Preserve evidence, diagnose adapter, never substitute a real recipient |
| Instrument feed stale or missing | QC flag/missing features visible | Treat `S` as degraded and use divergence workflow |
| Audit chain invalid | Status reports false | Stop decisions and learning; preserve database and logs |
| Learning job rejected | No partial promotion should be assumed | Preserve inputs, investigate, rerun only after correction |
| Non-shadow configuration requested | Process rejects startup | Do not bypass the lock; escalate to project and authority owners |

## Incident preservation and recovery

Do not update or delete outcome, decision-audit, calibration, zone-policy or learning-run rows.
On an integrity or safety incident, stop new operator actions, copy the database and relevant
logs to the approved evidence location, record hashes and timestamps, and notify the pilot
security and authority leads. Recovery uses a reviewed backup in a separate environment; it
does not mutate the original evidence.

After recovery, verify the decision and learning chains, repeat the 48-hour replay, run the full
test suite, and perform a sandbox-only end-to-end action before reopening the shadow pilot.

## Rehearsal and promotion gates

Run `python scripts/run_sprint5_pilot_rehearsal.py`. Every acceptance flag must be true, lint and
tests must pass, and the result must remain classified as synthetic. Promotion beyond shadow
also requires authority-owned historical outcomes, held-out calibration, local language and
template approval, security/privacy review, authenticated RBAC, monitored production storage,
provider/DLT agreements, SACHET onboarding and a signed operational go/no-go decision. This
repository intentionally provides no switch that satisfies those gates.
