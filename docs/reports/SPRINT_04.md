# Sprint 4 report - evidence dossier and resilient delivery

## Outcome

Sprint 4 completes the shadow-mode path from a governed fusion decision to an explainable officer action. An evaluator can open a live triage queue, inspect six evidence panels, test whether the decision survives removal of weak evidence or either stream, record an officer decision in an append-only ledger, and send one controlled message through push, SMS, IVR and CAP sandbox boundaries.

No external recipient, provider or government system is contacted. CAP output is explicitly marked `Test`, every delivery status is `sandbox_recorded`, and the language templates are explicitly marked as unreviewed sandbox drafts.

## Built

- Responsive `/operator` command surface with a severity-sorted cell queue, confidence meters, trust-weighted report map, dossier panels and decision rail.
- Six-panel evidence dossier:
  1. Verdict, population, first-evidence age and hysteresis state.
  2. Sensor heads, model version and top signed attributions.
  3. Report locations, trust, classifier evidence, independence and quarantine audit.
  4. Visible `S`, `R`, beta, gamma, `F`, threshold margin, governance and divergence state.
  5. Weakest-report, no-crowd and no-sensor counterfactuals.
  6. Historical-analogue availability and controlled officer actions.
- Pure fusion preview path so counterfactual analysis cannot mutate hysteresis state.
- Immutable officer decision repository with a global SHA-256 hash chain and SQLite triggers that reject updates and deletes.
- Decision actions for confirm, request ground verification, reject as false and sign/sandbox CAP.
- Versioned `flood.warning.avoid_route.v1` templates for `en-IN`, `hi-IN` and `mr-IN` with a fixed slot contract.
- Identical-message sandbox adapters for app push, SMS and IVR.
- CAP v1.2 XML composer with `status=Test`, `scope=Restricted`, H3-R9 geocoding and the same controlled message body.
- Offline `DHR|FL|...` SMS codec with ordinal depth, geohash-8, elapsed minutes and a per-device HMAC.
- Triage, dossier, officer-action and audit APIs.
- Deterministic Sprint 4 acceptance replay and machine-readable evidence artifact.

## Acceptance replay

The committed result is `docs/reports/sprint4_acceptance_results.json`.

- First evaluation cycle: Monitor, with a pending Warning candidate.
- Second evaluation cycle: committed Warning draft.
- `S = 0.950000`, `R = 0.824835`, `F = 0.884759`.
- Six dossier panels produced.
- Removing the three weakest reports: `F = 0.566859`, Watch.
- Removing the crowd stream: `F = 0.495652`, Advisory.
- Removing the sensor stream: `F = 0.286899`, Monitor.
- Push, SMS and IVR recorded the same SHA-256 message hash.
- CAP status: `Test`.
- Offline report: 34 characters and successfully authenticated/decoded.
- Audit events: one; hash chain valid.
- All nine acceptance flags passed.

## Safety controls

- Counterfactuals are read-only and cannot advance alert state.
- A non-Warning case cannot use the CAP-escalation action.
- An officer ID and reason are mandatory for every recorded action.
- Audit rows cannot be updated or deleted through SQLite.
- Slot values reject blank, oversized, multiline and brace-bearing content.
- Runtime translation is not present.
- Each language record states `human_reviewed=false`, `approval_scope=sandbox_only`, and `dlt_id=null`.
- CAP is restricted to shadow mode and cannot be emitted as an operational alert.
- Delivery adapters have no network code or provider credentials.
- Offline payload modification invalidates its HMAC.

## Verification

- 34 automated tests passed.
- Package coverage: 90%.
- Ruff linting passed.
- JavaScript syntax validation passed.
- The dashboard and its local assets are served successfully by the FastAPI test client.
- The generated CAP document parses as XML and carries the exact delivered message.

## Deliberate prototype limitations

- The triage queue, dossiers and delivery outboxes are in memory and reset with the process.
- The audit ledger is durable locally but lacks external anchoring and privileged-administrator protection.
- Operator identity is supplied by the caller; production requires gateway authentication, RBAC and signed identity claims.
- Templates require native-speaker review, authority approval and DLT registration.
- CAP is composed but not transported to SACHET.
- Push, SMS, IVR, Bhashini TTS and provider delivery receipts are sandbox interfaces only.
- Historical analogues and population exposure require authority-owned event and census layers.
- The operator map is an evidence-coordinate plane, not yet a full ward-boundary/tile GIS integration.

## Sprint 5 handoff

Sprint 5 can consume the immutable decision events as confirmed/refuted labels, update reporter trust and calibration through audited jobs, expose public reliability metrics, and harden this stack with load, chaos, privacy and security tests plus operator runbooks and a complete shadow-mode rehearsal.
