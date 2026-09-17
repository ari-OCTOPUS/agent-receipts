"""agent-receipts — a tiny proof-of-behavior core for software agents.

Every consequential action an agent takes appends one immutable, hash-chained
receipt. Every check fails closed. This is the pattern, extracted standalone;
it has no opinions about your agent, your transport, or your data.

Guarantees this module actually enforces:

  append-only   receipts are never rewritten; each carries the hash of the
                previous one, so tampering is detectable by replay.
  terminal      a receipt records what HAPPENED (EXECUTED / DENIED / FAILED),
                never what should be retried; there is no PENDING state that
                could change later.
  fail-closed   a halt condition (operator flag or environment marker) makes
                every gated call refuse, and an unreadable halt oracle also
                refuses — absence of evidence is not permission.
  silent-flip   a config/state snapshot checker that flags any value which
                changed without an accompanying authorization record.

MIT licensed. No dependencies beyond the standard library.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCHEMA = "agent-receipts/1"
TERMINAL = ("EXECUTED", "DENIED", "FAILED")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


class ReceiptChain:
    """Append-only, hash-chained receipt log. The file is the truth."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _head(self) -> str | None:
        last = None
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line).get("chain_hash")
                except json.JSONDecodeError:
                    continue
        return last

    def append(self, actor: str, action: str, status: str, detail: dict | None = None) -> dict:
        if status not in TERMINAL:
            raise ValueError("receipts are terminal-only: EXECUTED / DENIED / FAILED")
        receipt = {
            "schema": SCHEMA,
            "recorded_at_utc": _now_iso(),
            "actor": actor,
            "action": action,
            "status": status,
            "detail": detail or {},
            "previous_chain_hash": self._head(),
        }
        receipt["chain_hash"] = _sha({k: v for k, v in receipt.items()
                                      if k != "chain_hash"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return receipt

    def verify(self) -> dict:
        """Replay the chain: linkage and per-row hashes. Returns a report."""
        rows, bad_link, bad_hash = [], [], []
        prev = None
        if self.path.exists():
            for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    bad_hash.append(i)
                    continue
                if row.get("previous_chain_hash") != prev:
                    bad_link.append(i)
                calc = _sha({k: v for k, v in row.items() if k != "chain_hash"})
                if calc != row.get("chain_hash"):
                    bad_hash.append(i)
                prev = row.get("chain_hash")
                rows.append(row)
        return {"rows": len(rows), "bad_link": bad_link, "bad_hash": bad_hash,
                "ok": not bad_link and not bad_hash, "head": prev}

    def executed(self, action: str, key: str, value: str) -> bool:
        """Has this exact (action, key, value) already produced an EXECUTED
        receipt? Duplicate protection reads the same file it appends to."""
        if not self.path.exists():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (row.get("status") == "EXECUTED" and row.get("action") == action
                    and row.get("detail", {}).get(key) == value):
                return True
        return False


def gated(halt_check: Callable[[], str | None],
          chain: ReceiptChain, actor: str, action: str):
    """Run `halt_check()` first; a truthy halt reason denies, untouched.

    `halt_check` must itself be fail-closed: if it cannot read its oracle
    (missing file, bad env), it must RETURN a reason, not None. Absence of
    evidence is not permission.
    """
    def decorator(fn: Callable):
        def wrapper(*args, **kwargs):
            reason = halt_check()
            if reason:
                return chain.append(actor=actor, action=action, status="DENIED",
                                    detail={"rule": "halted", "halt_reason": reason})
            if chain.executed(action, "idempotency_key", kwargs.get("idempotency_key", "")):
                return chain.append(actor=actor, action=action, status="DENIED",
                                    detail={"rule": "duplicate-idempotency-key"})
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # outcome genuinely unknown/failed
                return chain.append(actor=actor, action=action, status="FAILED",
                                    detail={"error": type(exc).__name__,
                                            "idempotency_key": kwargs.get("idempotency_key", "")})
            return chain.append(actor=actor, action=action, status="EXECUTED",
                                detail={"result": str(result)[:200],
                                        "idempotency_key": kwargs.get("idempotency_key", "")})
        return wrapper
    return decorator


@dataclass
class SnapshotRule:
    key: str
    authorize_field: str  # a change must cite an authorization id here


def check_silent_flips(before: dict, after: dict, rules: list[SnapshotRule]) -> dict:
    """Flag values that changed between two snapshots WITHOUT an accompanying
    authorization record. A flip WITH authorization is normal operations; a
    flip WITHOUT one is the thing nobody notices until it matters."""
    flipped, authorized, silent = {}, {}, []
    for rule in rules:
        b, a = before.get(rule.key), after.get(rule.key)
        if b != a:
            flipped[rule.key] = (b, a)
            auth = (a or {}).get(rule.authorize_field) if isinstance(a, dict) else None
            if auth:
                authorized[rule.key] = auth
            else:
                silent.append(rule.key)
    return {"flipped": sorted(flipped), "authorized": sorted(authorized),
            "SILENT_FLIP": sorted(silent), "ok": not silent}
