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
- 13/13 direct-mode unit tests (creation guards, deposits, request guards, approve + fetched-content/justification regression, deny-leaves-funds-unchanged, unreachable-evidence deny without LLM, cooldown claim single payout, owner-only claim, veto bounds + 30-day force claim, vetoed re-request cycle, denial override time-gate/owner-only/consumes-denial/in-flight-precedence, views)
- `genvm-lint check` + validation pass (10 methods: 3 view, 7 write)
- On-chain verified on studionet: payable creation + deposit, role/value guards, deterministic deny on unreachable evidence (reasoning + timestamp stored, funds unchanged), denial-override 30-day time gate, **real LLM consensus approval** with contract-fetched example.com evidence → `pending` + cooldown, claim time-gate before cooldown, guardian veto (non-guardian reverts), and force-claim / re-request / claim all time-gated while vetoed (balance untouched)

## Live Deployment
Deployed on GenLayer studionet (chain `61999`):
[`0xB99d6f2a82EC2E2535C5298AC5B93a1711E91bA8`](https://explorer-studio.genlayer.com/address/0xB99d6f2a82EC2E2535C5298AC5B93a1711E91bA8)

## Source Code
See `contracts/guarded_savings_vault.py`
