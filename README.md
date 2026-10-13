<div align="center">

# Guarded Savings Vault

**Owner-controlled savings with an LLM rules guardian: withdrawals need consensus approval, vetoes are bounded — funds can be delayed, never locked forever.**

![GenLayer](https://img.shields.io/badge/GenLayer-Intelligent%20Contract-6a4cff)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Status](https://img.shields.io/badge/status-live%20on%20studionet-2ea44f)
![Tests](https://img.shields.io/badge/tests-13%20passed-2ea44f)

[Live Contract](https://explorer-studio.genlayer.com/address/0xB99d6f2a82EC2E2535C5298AC5B93a1711E91bA8) ·
[GenLayer Docs](https://docs.genlayer.com)

</div>

---

## Why

A savings account where someone else (a guardian) can block withdrawals is only safe if that blocker can't hold the money hostage. GuardedSavingsVault combines both: every withdrawal request must pass an LLM consensus check against the vault's immutable rules with contract-fetched evidence — and the guardian can veto any request — but **every veto is time-bounded**: the owner can always reclaim after 30 days without guardian cooperation. Guardian gone? The owner simply claims after the cooldown. Owner compromised? The guardian + 7-day cooldown buy detection time.

## Live Deployment

| | |
|---|---|
| **Contract** | [GuardedSavingsVault](https://explorer-studio.genlayer.com/address/0xB99d6f2a82EC2E2535C5298AC5B93a1711E91bA8) |
| **Address** | `0xB99d6f2a82EC2E2535C5298AC5B93a1711E91bA8` |
| **Network** | GenLayer studionet (chain `61999`) |
| **Status** | ✅ deployed + verified on-chain |

## Highlights

- 🧾 **Evidence-gated withdrawals** — the contract fetches the evidence URL on-chain and embeds the exact bytes into the rules-check prompt (`--- BEGIN FETCHED CONTENT ---`); unreachable evidence is a deterministic deny without any LLM call
- 🛡️ **Dual control with bounded veto** — guardian vetoes delay, never block: after `VETO_OVERRIDE_DAYS = 30` the owner `force_claim`s with zero guardian cooperation
- ⏱️ **Cooldown, not custody** — an approved request pays out after a 7-day cooldown; if the guardian never acts, the owner claims anyway — no party can stall forever
- 🔁 **Deny is feedback, not a lock** — a denied request changes no funds (only stores the denial reasoning + timestamp); the owner retries with better justification, re-requests 7 days after a veto, or — if the rules check never approves (unmeetable rules, dead evidence site) — `override_denial` 30 days after the last denial
- 🔒 **Immutable once funded** — guardian and rules can never be changed; one in-flight request per vault; balance checked at request time and again at claim time; claims are status-flipped before transfer (exactly one payout per request)

## How It Works

```
create_vault (owner: guardian address + rules, payable)   ── guardian & rules immutable
      │
deposit (anyone, anytime)
      │
      ▼
request_withdrawal (owner: amount + justification + evidence URL)
      │                    contract fetches evidence → LLM consensus vs vault rules
      ├── deny ──► no funds moved; denial_reasoning + last_denied_at stored
      │              (retry allowed; after 30d: override_denial escape hatch)
      └── approve ──► status=pending, cooldown = now + 7d
                          │
              ┌───────────┼──────────────────────────────┐
              ▼           ▼                              ▼
   guardian veto_request   owner claim_withdraw     (owner) force_claim
   status=vetoed (non-     after cooldown:          30 days after veto:
   money; reason stored)   status=claimed,          status=claimed, payout
              │            payout, replay-safe      (no guardian needed)
              ▼
   after 7d: owner may re-request (fresh LLM + cooldown, vetoable again)
   after 30d: owner force_claim (guardian bypassed — delay, never lock)
```

## Methods

| Role | Method | Guard |
|---|---|---|
| Owner | `request_withdrawal`, `claim_withdraw`, `force_claim`, `override_denial` | owner-only; status/deadline/balance checks; single payout per request |
| Guardian | `veto_request` | guardian-only; pending-only; non-money transition |
| Anyone | `deposit` | payable, > 0 |
| Any | `get_vault`, `get_vaults_by_owner`, `get_all_vaults` | read-only |

## Security Model

- **No permanent lock, by construction**: every state has a time-bounded exit — `pending → claim` (7d cooldown, owner), `vetoed → force_claim` (30d, owner, no guardian needed), `vetoed → re-request` (7d, fresh consensus), `denied → override_denial` (30d after the last denial, and each override consumes that denial); a veto or denial can only delay ≤ 30 days
- **Guardian cannot rug**: veto is a non-money state transition (no funds move); guardian and rules are immutable and set at creation; the guardian cannot initiate, redirect, or claim funds
- **Compromised owner is bounded**: requests require contract-fetched evidence passing rules consensus, then a 7-day cooldown + guardian veto window = detection/recovery time
- **Exactly one payout per request**: status flip (`claimed`) before the transfer; balance re-checked at every payout; `claim_withdraw`, `force_claim`, and `override_denial` are mutually exclusive by status and each consumes its own trigger; replays hit `No pending/No vetoed/No denial`
- **Prompt-injection hardening**: fetched evidence is fenced and marked untrusted; validators must reach the same `approve`/`deny` decision after their own independent fetch + rules check
- **Denial never moves funds**: LLM `deny` (or unreachable evidence) stores reasoning + timestamp only; the withdrawal request itself is never consumed by a denial, and the owner's bypass path after a denial costs a full 30-day wait (same bound as a guardian veto)
- Full input validation (address, justification, URL and length caps, amount ≤ balance at request, claim, and override)

## Test

```bash
gltest tests/ -q   # 13 passed
```

## Lint

```bash
genvm-lint check contracts/guarded_savings_vault.py
```
