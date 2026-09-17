# agent-receipts

**Proof-of-behavior for software agents.** A ~200-line, zero-dependency Python core that makes an agent's consequential actions *auditable by construction*.

## Why

Regulators (EU AI Act transparency obligations, effective Aug 2026) and enterprises now demand verifiable evidence of what an autonomous agent did — and 60% of organizations report they cannot even terminate a misbehaving agent (Kiteworks 2026 forecast; Cloud Security Alliance research note, 2026). Payment rails for agents (x402 and friends) are being built faster than trustworthy agents exist.

The missing piece is not more infrastructure. It is agents whose own logs cannot lie to themselves.

## What it enforces

| Guarantee | Mechanism |
|---|---|
| Append-only history | every receipt carries the hash of the previous one; tampering breaks replay |
| Terminal truth | receipts are `EXECUTED` / `DENIED` / `FAILED` — never a mutable "pending" |
| Fail-closed halt | a halt oracle that *cannot be read* still refuses; absence of evidence is not permission |
| Duplicate protection | idempotency reads the same append-only file it writes |
| Silent-flip detection | config/state changes without an authorization record are flagged, not assumed benign |

## Install

Copy the single module `agent_receipts/__init__.py` (stdlib only), or:

```bash
pip install .   # from this repo
```

## Use

```python
from agent_receipts import ReceiptChain, gated, check_silent_flips

chain = ReceiptChain("receipts.jsonl")

@gated(halt_check=lambda: os.environ.get("AGENT_HALT") or None,
       chain=chain, actor="billing-agent", action="charge_card")
def charge(amount, idempotency_key=""):
    return provider.charge(amount)

charge(5, idempotency_key="inv-42")   # EXECUTED (once) — or DENIED, with a reason
chain.verify()                         # {'ok': True, ...}

# after any config change:
check_silent_flips(before_snapshot, after_snapshot, rules)
```

## Design notes

- The receipt file is the truth. No database required; one JSONL per agent scope.
- `FAILED` (e.g. a provider timeout) is *terminal and honest*: the outcome is unknown, and the log says what happened, never what should be retried.
- `halt_check` must return a reason string when halted AND when unreadable. A missing oracle file is a halt, not a pass.

## License

MIT. Provided as-is; receipts make behavior auditable, not correct.
