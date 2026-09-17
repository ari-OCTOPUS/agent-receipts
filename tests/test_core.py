import json
import os
import tempfile
import unittest

from agent_receipts import ReceiptChain, check_silent_flips, gated


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.chain = ReceiptChain(os.path.join(self.dir, "receipts.jsonl"))

    def test_append_and_verify(self):
        self.chain.append("agent", "send_email", "EXECUTED", {"to": "masked"})
        self.chain.append("agent", "send_email", "DENIED", {"rule": "halted"})
        report = self.chain.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(report["rows"], 2)

    def test_non_terminal_rejected(self):
        with self.assertRaises(ValueError):
            self.chain.append("agent", "x", "PENDING")

    def test_tamper_detected(self):
        self.chain.append("agent", "a", "EXECUTED", {"n": 1})
        path = self.chain.path
        rows = path.read_text().splitlines()
        row = json.loads(rows[0])
        row["detail"]["n"] = 999  # tamper
        path.write_text(json.dumps(row) + "\n")
        report = self.chain.verify()
        self.assertFalse(report["ok"])
        self.assertTrue(report["bad_hash"])

    def test_chain_links(self):
        r1 = self.chain.append("agent", "a", "EXECUTED")
        r2 = self.chain.append("agent", "b", "EXECUTED")
        self.assertIsNone(r1["previous_chain_hash"])
        self.assertEqual(r2["previous_chain_hash"], r1["chain_hash"])


class GateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.chain = ReceiptChain(os.path.join(self.dir, "r.jsonl"))
        self.halt_reason = None
        self.calls = []

    def test_halt_denies_untouched(self):
        self.halt_reason = "operator-flag"

        @gated(lambda: self.halt_reason, self.chain, "agent", "pay")
        def pay(amount):
            self.calls.append(amount)
            return "paid"

        r = pay(5)
        self.assertEqual(r["status"], "DENIED")
        self.assertEqual(self.calls, [])

    def test_pass_and_duplicate(self):
        @gated(lambda: self.halt_reason, self.chain, "agent", "pay")
        def pay(amount, idempotency_key=""):
            self.calls.append(amount)
            return "paid"

        r1 = pay(5, idempotency_key="k1")
        r2 = pay(5, idempotency_key="k1")
        self.assertEqual(r1["status"], "EXECUTED")
        self.assertEqual(r2["status"], "DENIED")
        self.assertEqual(r2["detail"]["rule"], "duplicate-idempotency-key")
        self.assertEqual(len(self.calls), 1)

    def test_exception_is_failed_not_executed(self):
        @gated(lambda: None, self.chain, "agent", "call")
        def boom():
            raise TimeoutError

        r = boom()
        self.assertEqual(r["status"], "FAILED")


class SilentFlipTest(unittest.TestCase):
    def test_silent_vs_authorized(self):
        before = {"rate_limit": 10, "endpoint": "a.example"}
        after = {"rate_limit": {"value": 99, "authorized_by": "change-17"},
                 "endpoint": "b.example"}
        rules = [("rate_limit", "authorized_by"), ("endpoint", "authorized_by")]
        rules = [type("R", (), {"key": k, "authorize_field": f})() for k, f in rules]
        out = check_silent_flips(before, after, rules)
        self.assertEqual(out["SILENT_FLIP"], ["endpoint"])
        self.assertEqual(out["authorized"], ["rate_limit"])
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
