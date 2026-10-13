"""Direct-mode tests for the GuardedSavingsVault contract.

Covers: vault creation rules, deposits, LLM-gated withdrawal requests with
contract-fetched evidence, guardian veto, cooldown claim, bounded veto
override (force claim), re-request window, and full replay/role guards.
"""

import json
import re
from datetime import datetime, timedelta

from gltest.direct import create_address

RULES = "Withdrawals require a receipt for an unexpected medical expense."
EVIDENCE_URL = "https://example.com/receipt"
JUSTIFICATION = "Emergency dental surgery, receipt at the evidence URL."
APPROVE_JSON = json.dumps({"decision": "approve", "reasoning": "Receipt matches rules."})
DENY_JSON = json.dumps({"decision": "deny", "reasoning": "Not a medical expense."})


def _create(contract, vm, owner, guardian, pot=1000, rules=RULES):
    vm.sender = owner
    vm.value = pot
    return contract.create_vault(guardian_address=str(guardian), rules=rules)


def _request(contract, vm, owner, vid, amount=100, url=EVIDENCE_URL,
             justification=JUSTIFICATION, llm_json=APPROVE_JSON, body="Receipt body"):
    vm.sender = owner
    vm.clear_mocks()
    vm.mock_web(re.escape(url), {"status": 200, "body": body})
    vm.mock_llm(re.escape("You are a savings-vault rules guardian"), llm_json)
    return contract.request_withdrawal(
        vault_id=vid, amount=amount, justification=justification, evidence_url=url,
    )


def test_create_vault_validates(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    direct_vm.sender = owner
    direct_vm.value = 1000
    with direct_vm.expect_revert("Guardian address is required"):
        contract.create_vault(guardian_address="  ", rules=RULES)
    with direct_vm.expect_revert("Guardian address must be valid"):
        contract.create_vault(guardian_address="not-an-address", rules=RULES)
    with direct_vm.expect_revert("Rules are required"):
        contract.create_vault(guardian_address=str(guardian), rules="  ")
    with direct_vm.expect_revert("Rules too long"):
        contract.create_vault(guardian_address=str(guardian), rules="x" * 2001)
    with direct_vm.expect_revert("Guardian must differ from owner"):
        contract.create_vault(guardian_address=str(owner), rules=RULES)

    vid = _create(contract, direct_vm, owner, guardian)
    v = contract.get_vault(vid)
    assert v["owner"] == str(owner)
    assert v["guardian"] == str(guardian)
    assert v["balance"] == 1000
    assert v["request_status"] == "none"


def test_deposit(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    with direct_vm.expect_revert("Vault not found"):
        direct_vm.sender = owner
        direct_vm.value = 5
        contract.deposit(vault_id="nope")
    with direct_vm.expect_revert("Deposit must be > 0"):
        direct_vm.sender = owner
        direct_vm.value = 0
        contract.deposit(vault_id=vid)

    direct_vm.value = 500
    contract.deposit(vault_id=vid)
    v = contract.get_vault(vid)
    assert v["balance"] == 1500
    assert v["total_deposited"] == 1500


def test_request_guards(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")
    other = create_address("other")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    direct_vm.sender = other
    with direct_vm.expect_revert("Only owner can request a withdrawal"):
        contract.request_withdrawal(vault_id=vid, amount=100,
                                    justification=JUSTIFICATION,
                                    evidence_url=EVIDENCE_URL)
    direct_vm.sender = owner
    with direct_vm.expect_revert("Amount must be > 0"):
        contract.request_withdrawal(vault_id=vid, amount=0,
                                    justification=JUSTIFICATION,
                                    evidence_url=EVIDENCE_URL)
    with direct_vm.expect_revert("Amount exceeds balance"):
        contract.request_withdrawal(vault_id=vid, amount=1001,
                                    justification=JUSTIFICATION,
                                    evidence_url=EVIDENCE_URL)
    with direct_vm.expect_revert("Justification is required"):
        contract.request_withdrawal(vault_id=vid, amount=100,
                                    justification="  ", evidence_url=EVIDENCE_URL)
    with direct_vm.expect_revert("Evidence URL is required"):
        contract.request_withdrawal(vault_id=vid, amount=100,
                                    justification=JUSTIFICATION, evidence_url="")


def test_request_approve_binds_fetched_content(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    body = "Dental surgery receipt for 0.99. VAULT_BODY_MARKER"
    direct_vm.mock_web(re.escape(EVIDENCE_URL), {"status": 200, "body": body})
    # Matches only if the contract-fetched body is embedded in the prompt.
    direct_vm.mock_llm(
        r"--- BEGIN FETCHED CONTENT ---\s*" + re.escape(body),
        APPROVE_JSON,
    )
    direct_vm.sender = owner
    result = contract.request_withdrawal(
        vault_id=vid, amount=100, justification=JUSTIFICATION,
        evidence_url=EVIDENCE_URL,
    )
    assert result["decision"] == "approve"
    v = contract.get_vault(vid)
    assert v["request_status"] == "pending"
    assert v["request_amount"] == 100
    assert v["cooldown_until"] != ""

    with direct_vm.expect_revert("Withdrawal already pending"):
        _request(contract, direct_vm, owner, vid)


def test_request_denied_leaves_state_unchanged(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    result = _request(contract, direct_vm, owner, vid, llm_json=DENY_JSON)
    assert result["decision"] == "denied"
    v = contract.get_vault(vid)
    assert v["request_status"] == "none"
    assert v["denial_reasoning"] != ""

    # denial never blocks a retry with better justification
    result = _request(contract, direct_vm, owner, vid, llm_json=APPROVE_JSON)
    assert result["decision"] == "approve"
    assert contract.get_vault(vid)["request_status"] == "pending"


def test_unreachable_evidence_denied_without_llm(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    direct_vm.mock_web(re.escape(EVIDENCE_URL), {"status": 404, "body": "gone"})
    direct_vm.sender = owner
    result = contract.request_withdrawal(
        vault_id=vid, amount=100, justification=JUSTIFICATION,
        evidence_url=EVIDENCE_URL,
    )
    # no LLM mock was registered: reaching exec_prompt would raise
    assert result["decision"] == "denied"
    assert contract.get_vault(vid)["request_status"] == "none"


def test_claim_after_cooldown_single_payout(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    _request(contract, direct_vm, owner, vid, amount=100)

    direct_vm.sender = owner
    with direct_vm.expect_revert("Cooldown has not elapsed"):
        contract.claim_withdraw(vault_id=vid)

    base = datetime.now()
    direct_vm.warp((base + timedelta(days=8)).isoformat())
    contract.claim_withdraw(vault_id=vid)

    v = contract.get_vault(vid)
    assert v["request_status"] == "claimed"
    assert v["balance"] == 900
    assert v["total_withdrawn"] == 100

    with direct_vm.expect_revert("No pending withdrawal request"):
        contract.claim_withdraw(vault_id=vid)


def test_claim_only_owner(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")
    other = create_address("other")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    _request(contract, direct_vm, owner, vid, amount=100)
    base = datetime.now()
    direct_vm.warp((base + timedelta(days=8)).isoformat())

    direct_vm.sender = other
    with direct_vm.expect_revert("Only owner can claim"):
        contract.claim_withdraw(vault_id=vid)


def test_veto_bounded_never_permanent(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")
    other = create_address("other")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    _request(contract, direct_vm, owner, vid, amount=100)

    direct_vm.sender = other
    with direct_vm.expect_revert("Only guardian can veto"):
        contract.veto_request(vault_id=vid, reason="no")
    direct_vm.sender = guardian
    with direct_vm.expect_revert("Veto reason is required"):
        contract.veto_request(vault_id=vid, reason="  ")
    contract.veto_request(vault_id=vid, reason="Evidence looks altered")

    v = contract.get_vault(vid)
    assert v["request_status"] == "vetoed"
    assert v["veto_reason"] == "Evidence looks altered"

    # immediately after the veto: all paths are time-gated, none is open
    direct_vm.sender = owner
    with direct_vm.expect_revert("No pending withdrawal request"):
        contract.claim_withdraw(vault_id=vid)
    with direct_vm.expect_revert("wait 7 days to re-request"):
        contract.request_withdrawal(vault_id=vid, amount=100,
                                    justification=JUSTIFICATION,
                                    evidence_url=EVIDENCE_URL)
    with direct_vm.expect_revert("30 days) has not passed"):
        contract.force_claim(vault_id=vid)

    # guardian disappeared for a month: owner still gets the money
    base = datetime.now()
    direct_vm.warp((base + timedelta(days=31)).isoformat())
    contract.force_claim(vault_id=vid)

    v = contract.get_vault(vid)
    assert v["request_status"] == "claimed"
    assert v["balance"] == 900
    assert v["total_withdrawn"] == 100

    with direct_vm.expect_revert("No vetoed request"):
        contract.force_claim(vault_id=vid)


def test_vetoed_rerequest_fresh_approval_cycle(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=DENY_JSON)
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=APPROVE_JSON)
    direct_vm.sender = guardian
    contract.veto_request(vault_id=vid, reason="suspicious")

    base = datetime.now()
    direct_vm.warp((base + timedelta(days=8)).isoformat())
    direct_vm.sender = owner
    result = _request(contract, direct_vm, owner, vid, amount=50,
                      justification="Updated justification with new receipt.",
                      llm_json=APPROVE_JSON)
    assert result["decision"] == "approve"

    v = contract.get_vault(vid)
    assert v["request_status"] == "pending"
    assert v["request_amount"] == 50

    direct_vm.warp((base + timedelta(days=16)).isoformat())
    contract.claim_withdraw(vault_id=vid)
    v = contract.get_vault(vid)
    assert v["request_status"] == "claimed"
    assert v["balance"] == 950


def test_views(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    vid2 = _create(contract, direct_vm, owner, guardian, pot=0)

    by_owner = contract.get_vaults_by_owner(str(owner))
    assert [v["id"] for v in by_owner] == [vid, vid2]
    assert vid in contract.get_all_vaults()
    assert contract.get_vault(vid)["last_denied_at"] == ""
    with direct_vm.expect_revert("Vault not found"):
        contract.get_vault("nope")


def test_override_denial_bounded_escape(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")
    other = create_address("other")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)

    # no denial yet -> nothing to override
    direct_vm.sender = owner
    with direct_vm.expect_revert("No denial to override"):
        contract.override_denial(vault_id=vid, amount=100)

    _request(contract, direct_vm, owner, vid, amount=100, llm_json=DENY_JSON)
    v = contract.get_vault(vid)
    assert v["request_status"] == "none"
    assert v["last_denied_at"] != ""

    # denied -> still time-gated, and guardian/other have no override power
    with direct_vm.expect_revert("30 days) has not passed"):
        contract.override_denial(vault_id=vid, amount=100)
    direct_vm.sender = other
    with direct_vm.expect_revert("Only owner can override a denial"):
        contract.override_denial(vault_id=vid, amount=100)

    # denial that is later approved can no longer be overridden
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=APPROVE_JSON)
    assert contract.get_vault(vid)["last_denied_at"] == ""
    direct_vm.sender = owner
    base = datetime.now()
    direct_vm.warp((base + timedelta(days=31)).isoformat())
    with direct_vm.expect_revert("own path"):
        contract.override_denial(vault_id=vid, amount=100)
    contract.claim_withdraw(vault_id=vid)
    with direct_vm.expect_revert("No denial to override"):
        contract.override_denial(vault_id=vid, amount=100)


def test_override_denial_after_30_days_consumes_denial(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/guarded_savings_vault.py")
    owner = create_address("owner")
    guardian = create_address("guardian")

    vid = _create(contract, direct_vm, owner, guardian, pot=1000)
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=DENY_JSON)

    base = datetime.now()
    direct_vm.warp((base + timedelta(days=31)).isoformat())
    direct_vm.sender = owner
    contract.override_denial(vault_id=vid, amount=400)

    v = contract.get_vault(vid)
    assert v["request_status"] == "claimed"
    assert v["balance"] == 600
    assert v["total_withdrawn"] == 400
    assert v["last_denied_at"] == "", "each override consumes the denial"

    # replay / second override without a fresh denial is blocked
    with direct_vm.expect_revert("No denial to override"):
        contract.override_denial(vault_id=vid, amount=100)

    # an in-flight request takes precedence over the denial path
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=DENY_JSON)
    _request(contract, direct_vm, owner, vid, amount=100, llm_json=APPROVE_JSON)
    assert contract.get_vault(vid)["request_status"] == "pending"
    with direct_vm.expect_revert("own path"):
        contract.override_denial(vault_id=vid, amount=100)
