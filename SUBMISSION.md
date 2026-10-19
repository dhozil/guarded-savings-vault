# Submission: Guarded Savings Vault

## Category
Intelligent Contracts

## Contract Name
Guarded Savings Vault

## Summary
A reusable GenLayer primitive for guarded savings: the owner deposits and requests withdrawals with contract-fetched evidence that must pass LLM consensus against the vault's immutable rules; a guardian can veto any request — but every veto is time-bounded (force claim after 30 days), so funds can be delayed, never locked forever.

## What Makes This Unique
- **Dual control with a mathematically bounded veto**: the guardian buys detection time against a compromised owner, while the owner retains a 30-day guardian-bypass exit — the design guarantees no state where funds wait indefinitely on a specific actor
- **Evidence is fetched, not just linked**: the contract fetches the withdrawal evidence URL on-chain (`gl.nondet.web.get`) and embeds the exact bytes into the rules-check prompt (`--- BEGIN FETCHED CONTENT ---`, fenced as untrusted data); unreachable evidence is a deterministic deny with no LLM call
- **Deny is feedback, not a lock**: a denied request moves no funds — only the denial reasoning and timestamp are stored, so the owner retries with better justification; if the rules check never approves (unmeetable rules, dead evidence site), `override_denial` provides a 30-day-bounded escape hatch, and each override consumes its denial
- **Immutable once funded**: guardian and rules are fixed at creation and cannot be changed while money is inside; one in-flight request per vault; balance checked at request and claim time
- **Exactly one payout per request**: every payout path (`claim_withdraw` after cooldown, `force_claim` after veto, `override_denial` after a denial) flips status to `claimed` before transferring, and the paths are mutually exclusive by status — replays revert; each override also consumes its denial

## How Consensus Is Used
The contract uses `gl.vm.run_nondet_unsafe()` with a custom validator function. Each validator independently fetches the evidence URL on-chain and re-evaluates it against the vault's immutable rules and the owner's justification, producing its own `approve`/`deny` decision. Consensus requires every validator to reach the same decision as the leader. `approve` moves the request to `pending` (cooldown 7 days); `deny` moves no funds — it only stores the denial reasoning and timestamp (retry allowed immediately, bounded override after 30 days). An unreachable evidence URL deterministically denies for every fetch-failing validator without calling the LLM.

## Technical Details
- Python-based GenLayer Intelligent Contract
- Uses `gl.nondet.exec_prompt()` for rules-check decisions
- Uses `gl.nondet.web.get()` for contract-side evidence acquisition (non-200 → deny)
- Custom validators must match the leader's exact decision (`approve` / `deny`)
- TreeMap storage: vaults (owner/guardian/rules/balance/request lifecycle) and an owner→vaults index
- Integer-only domain (wei amounts; deadlines as ISO strings); time gates via `datetime` against consensus time
- Constants: `COOLDOWN_DAYS = 7`, `VETO_REREQUEST_DAYS = 7`, `VETO_OVERRIDE_DAYS = 30`, `DENIAL_OVERRIDE_DAYS = 30`

## Use Case
Personal/treasury savings vaults, escrowed emergency funds, allowance-style wallets for teams or DAOs, and any custody arrangement where withdrawals should require justification against written rules plus an independent veto power — without trusting either party with permanent control.

## Verification
- 15/15 direct-mode unit tests (creation guards, deposits, request guards, approve + fetched-content/justification regression, deny-leaves-funds-unchanged, unreachable-evidence deny without LLM, cooldown claim single payout, owner-only claim, veto bounds + 30-day force claim, vetoed re-request cycle, denial override time-gate/owner-only/consumes-denial/in-flight-precedence, **101 consecutive zero-value creations beyond the old 100-vault cap**, **blank/non-string/missing deny-reasoning falls back deterministically**, views)
- `genvm-lint check` + validation pass (10 methods: 3 view, 7 write)
- On-chain verified on studionet: payable creation + deposit, role/value guards, deterministic deny on unreachable evidence (reasoning + timestamp stored, funds unchanged), denial-override 30-day time gate, **real LLM consensus approval** with contract-fetched example.com evidence → `pending` + cooldown, claim time-gate before cooldown, guardian veto (non-guardian reverts), and force-claim / re-request / claim all time-gated while vetoed (balance untouched)

## Live Deployment
Deployed on GenLayer studionet (chain `61999`):
[`0xfCFE1da1c83684E0abE304dAFF7F100903553699`](https://explorer-studio.genlayer.com/address/0xfCFE1da1c83684E0abE304dAFF7F100903553699)

## Source Code
See `contracts/guarded_savings_vault.py`

## Steward Request Response
**Request**: (1) prevent one untrusted caller from exhausting the contract-wide vault capacity through free zero-value creations (remove the global cap or enforce non-exhaustible/per-owner allocation); (2) make every validator-accepted denial result safe for the committed deny branch by validating the required reasoning field or using a deterministic fallback. Provide matching submitted and deployed source.

**Fix 1 — no exhaustible capacity**: the global `MAX_VAULTS = 100` cap and its `Vault limit reached` check were removed entirely; vault ids come from an unbounded monotonic counter (`v0`, `v1`, …), so there is no shared capacity for a single caller to squat with free zero-value creations. Regression test: 101 consecutive zero-value `create_vault` calls all succeed (`v0`–`v100`) and creation still works afterwards.

**Fix 2 — deny branch is consensus-safe**: for `decision == "deny"` validators now additionally require the leader's `reasoning` to be a non-empty string no longer than `MAX_REASONING_CHARS` before accepting; `eval_fn` normalizes LLM output (non-string → empty → fallback) with the deterministic constant `DENY_FALLBACK_REASONING = "Denied by rules check."`; and the store path independently applies the same fallback + length cap, so even a leader result that slipped through with missing/blank/non-string reasoning can never store one. Tests cover blank, numeric, and missing reasoning fields.

**Verified**: 15/15 direct-mode tests, `genvm-lint check` + validation pass (10 methods: 3 view, 7 write), re-deployed and re-verified on-chain (creation/deposit, role/value guards, unreachable-evidence deny, denial-override time gate, real LLM approval → pending + cooldown, claim time gate, guardian veto, bounded force claim). Submitted source matches the deployed source at the address above.
