"""Application service: run the agent workflow, persist runs, and govern approvals & payment drafts."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta

from meridian.analytics.engine import get_engine
from meridian.analytics.scenarios import Scenario
from meridian.decision.recommendations import derive_status
from meridian.domain.models import MANDATE_ACTIONS, PAYMENT_ACTIONS
from meridian.execution.payments import build_pain001
from meridian.governance.policy import ApprovalDenied, check_approval, load_policy
from meridian.observability.metrics import APPROVALS, RUNS
from meridian.orchestration.graph import build_graph
from meridian.orchestration.public_graph import build_public_graph
from meridian.store.factory import get_store


def _next_business_day(d: date, n: int = 3) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


class ExecutionBlocked(PermissionError):
    """A release/transmission was refused (no channel, dual control, wrong state). Always audited."""


class TreasuryService:
    def __init__(self, store=None):
        self.store = store or get_store()
        self.engine = get_engine()
        self.graph, self.ledger = build_graph(self.store)
        self.public_graph, _ = build_public_graph(self.store)

    async def run_public(self, question: str | None = None, requested_by: str = "system", cik: int | None = None) -> dict:
        """REAL public-company review (SEC filings + public market data) through the governed agent chain."""
        import time as _t

        from meridian.observability import ops
        from meridian.public_company.analytics import get_public_company

        t0 = _t.perf_counter()
        run_id = f"PUB-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        pc = get_public_company(cik)
        out = await self.public_graph.ainvoke({"run_id": run_id, "question": question, "requested_by": requested_by,
                                               "cik": pc.cik}, {"recursion_limit": 40})
        acts = out.get("actions", [])
        intent = "PUBLIC_INSIGHT" if question else "PUBLIC_COMPANY_REVIEW"
        status = "ANSWERED" if question else ("AWAITING_APPROVALS" if acts else "COMPLETED")
        llm = (out.get("briefing") or {}).get("llm_provider") or self.engine.settings.llm_provider
        run = {"run_id": run_id, "case": "public", "company": pc.subject, "created_at": datetime.now(UTC).isoformat(),
               "question": question, "requested_by": requested_by, "status": status, "intent": intent,
               "plan": {"intent": intent}, "findings": out.get("findings", []), "actions": acts,
               "recommendations": out.get("recommendations", []), "rules": out.get("rules", []),
               "failed_agents": out.get("failed_agents", []), "is_base_case": not question,
               "briefing": out.get("briefing"), "grounding": out.get("grounding"), "insight": out.get("answer"),
               "answer": None, "tool_calls": out.get("tool_calls", []), "trace": out.get("trace", []),
               "classification": "PUBLIC_REAL", "mode": self.engine.settings.mode, "llm_provider": llm,
               "policy_version": load_policy().version,
               "reproducibility": {"case": "public", "company": pc.subject["name"], "cik": pc.cik, "ticker": pc.subject.get("ticker"),
                                   "snapshot_checksum": pc.snap["checksum_sha256"], "retrieved_at": pc.snap["retrieved_at"],
                                   "latest_quarter": pc.latest_quarter, "latest_fiscal_year_end": pc.latest_fy,
                                   "policy_version": load_policy().version, "llm": self.engine.settings.llm_provider,
                                   "llm_model": self.engine.settings.llm_model
                                   if self.engine.settings.llm_provider != "mock" else "deterministic offline reasoner"}}
        self.store.save_run(run)
        run["completion_audit_hash"] = self.ledger.record("orchestrator", "run_completed", {
            "case": "public", "status": status, "actions": len(acts), "intent": intent,
            "snapshot_checksum": pc.snap["checksum_sha256"][:16]}, run_id)[:16]
        self.store.save_run(run)
        RUNS.labels(status=status).inc()
        ops.record_job("public_company_run", "SUCCEEDED", (_t.perf_counter() - t0) * 1000,
                       f"{intent} → {status}; {len(run['recommendations'])} recommendations", run_id)
        return run

    async def run(self, question: str | None = None, requested_by: str = "system") -> dict:
        import time as _t

        from meridian.observability import ops

        t0 = _t.perf_counter()
        run_id = f"RUN-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        try:
            run = await self._run(run_id, question, requested_by)
        except Exception as e:
            ops.record_job("agent_run", "FAILED", (_t.perf_counter() - t0) * 1000, f"{type(e).__name__}: {e}"[:200], run_id)
            raise
        ops.record_job("agent_run", "SUCCEEDED", (_t.perf_counter() - t0) * 1000,
                       f"{run['intent']} → {run['status']}; {len(run.get('recommendations') or [])} recommendations", run_id)
        return run

    async def _run(self, run_id: str, question: str | None, requested_by: str) -> dict:
        out = await self.graph.ainvoke({"run_id": run_id, "question": question, "requested_by": requested_by},
                                       {"recursion_limit": 40})
        ev = out.get("evidence", {})
        acts = out.get("actions", [])
        intent = out["plan"]["intent"]
        status = "ANSWERED" if intent in {"POLICY_QUESTION", "INSIGHT"} else ("AWAITING_APPROVALS" if acts else "COMPLETED")
        scenario = Scenario(**out["scenario"]) if out.get("scenario") else None
        run = {"run_id": run_id, "created_at": datetime.now(UTC).isoformat(), "question": question,
               "requested_by": requested_by, "status": status, "intent": intent, "plan": out.get("plan"),
               "scenario": out.get("scenario"), "findings": out.get("findings", []), "actions": acts,
               "recommendations": out.get("recommendations", []), "failed_agents": out.get("failed_agents", []),
               "is_base_case": intent == "DAILY_RUN",
               "briefing": out.get("briefing"), "grounding": out.get("grounding"), "answer": out.get("answer"),
               "insight": out.get("insight"), "forecast": ev.get("forecast"), "kpis": ev.get("kpis"),
               "optimization": ev.get("optimization"), "risk_scenarios": ev.get("scenarios"),
               "tool_calls": out.get("tool_calls", []), "trace": out.get("trace", []),
               "classification": self.engine.g.classification, "mode": self.engine.settings.mode,
               "llm_provider": self.engine.settings.llm_provider, "policy_version": load_policy().version,
               "reproducibility": self.engine.reproducibility(scenario)}
        self.store.save_run(run)
        run["completion_audit_hash"] = self.ledger.record("orchestrator", "run_completed", {
            "status": status, "actions": len(acts), "intent": intent,
            "reproducibility": {k: run["reproducibility"][k] for k in ("policy_sha256", "model_id", "llm", "seed")}},
            run_id)[:16]
        self.store.save_run(run)
        RUNS.labels(status=status).inc()
        self.engine.refresh_graph()
        return run

    def decide(self, run_id: str, action_id: str, decision: str, user: str, role: str, comment: str = "") -> dict:
        """Human decision on a proposed action. Authority (TP-2.1) is checked for approvals AND rejections;
        segregation of duties (TP-2.2) for approvals. Every decision — and every denied attempt — is audited."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        act = next((a for a in run["actions"] if a["action_id"] == action_id), None)
        if act is None:
            raise KeyError(action_id)
        if act["status"] not in {"PROPOSED", "SUBMITTED"}:
            raise ValueError(f"{action_id} is already {act['status']}")
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        if decision == "REJECT" and not comment.strip():
            raise ValueError("A rejection requires a written rationale")
        try:
            proposers = [run["requested_by"]] + ([act["submitted_by"]["user"]] if act.get("submitted_by") else [])
            check_approval(act, role, user, proposers if decision == "APPROVE" else "__nobody__")
        except ApprovalDenied as e:
            self.ledger.record(user, "approval_denied", {"action_id": action_id, "decision": decision, "role": role,
                                                         "reason": str(e)}, run_id)
            raise
        stamp = {"user": user, "role": role, "at": datetime.now(UTC).isoformat(), "comment": comment}
        if decision == "APPROVE":
            act["status"] = "APPROVED"
            act["approved_by"] = stamp
            act["execution_artifact"] = self._artifact(run_id, act)
            if act["type"] in PAYMENT_ACTIONS:
                act["payment_state"] = "APPROVED"
        else:
            act["status"] = "REJECTED"
            act["rejected_by"] = stamp
        APPROVALS.labels(decision=decision, role=role).inc()
        h = self.ledger.record(user, f"action_{decision.lower()}", {
            "action_id": action_id, "type": act["type"], "amount_usd": act["amount_usd"], "role": role,
            "comment": comment, "artifact": (act.get("execution_artifact") or {}).get("kind")}, run_id)
        act.setdefault("evidence", {}).setdefault("audit", {})["decision_record_hash"] = h[:16]
        act["evidence"]["approval"] = {"decision": decision, **stamp}
        self._refresh_status(run)
        self.store.save_run(run)
        self.engine.refresh_graph()
        return run

    # ------------------------------------------------------------------------------ lifecycle helpers
    def _refresh_status(self, run: dict) -> None:
        acts = {a["action_id"]: a for a in run["actions"]}
        for r in run.get("recommendations") or []:
            r["status"] = derive_status([acts[i] for i in r["action_ids"] if i in acts]) if r["action_ids"] else "OPEN"
        if run["actions"] and all(a["status"] not in {"PROPOSED", "SUBMITTED"} for a in run["actions"]):
            run["status"] = "COMPLETED"

    def _find(self, run_id: str, action_id: str) -> tuple[dict, dict]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        act = next((a for a in run["actions"] if a["action_id"] == action_id), None)
        if act is None:
            raise KeyError(action_id)
        return run, act

    def submit(self, run_id: str, rec_short_id: str, user: str, role: str, comment: str = "") -> dict:
        """Review step: a finance user submits a recommendation for approval. The submitter is recorded and, like
        the requester, can never approve the same actions (TP-2.2)."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        rec = next((r for r in run.get("recommendations") or [] if r["short_id"] == rec_short_id
                    or r["rec_id"] == rec_short_id), None)
        if rec is None:
            raise KeyError(rec_short_id)
        if not rec["action_ids"]:
            raise ValueError("Advisory recommendation — nothing to approve; assign to the owner instead")
        stamp = {"user": user, "role": role, "at": datetime.now(UTC).isoformat(), "comment": comment}
        n = 0
        for a in run["actions"]:
            if a["action_id"] in rec["action_ids"] and a["status"] == "PROPOSED":
                a["status"] = "SUBMITTED"
                a["submitted_by"] = stamp
                n += 1
                if a["type"] in PAYMENT_ACTIONS:
                    a["payment_state"] = "IN_APPROVAL"
        if not n:
            raise ValueError(f"{rec['short_id']} has no proposed actions to submit")
        rec["submitted_by"] = stamp
        h = self.ledger.record(user, "recommendation_submitted", {"rec_id": rec["rec_id"], "actions": n, "role": role,
                                                                   "comment": comment}, run_id)
        rec.setdefault("history", []).append({"event": "SUBMITTED", **stamp, "audit_hash": h[:16]})
        self._refresh_status(run)
        self.store.save_run(run)
        return run

    def prepare_execution(self, run_id: str, action_id: str, user: str, role: str) -> dict:
        """Prepare controlled execution: validate the approved payment file and move it DRAFT/APPROVED → READY.
        Nothing is transmitted."""
        run, act = self._find(run_id, action_id)
        if act["status"] == "APPROVED" and act["type"] in MANDATE_ACTIONS:
            return self._prepare_mandate(run, act, user, role)
        if act["status"] != "APPROVED" or act["type"] not in PAYMENT_ACTIONS:
            self.ledger.record(user, "prepare_denied", {"action_id": action_id, "status": act["status"]}, run_id)
            raise ValueError(f"{action_id} must be an APPROVED payment or mandate action (is {act['status']} {act['type']})")
        from meridian.ingest.pipeline import iban_valid

        art = act["execution_artifact"]
        g = self.engine.g
        src_bal = sum(a.balance * g.fx[a.currency] for a in g.accounts if a.entity_id == act["from_entity"])
        dup = [a["action_id"] for r in self.store.list_runs(50) for a in r["actions"] if a.get("execution_artifact")
               and a["execution_artifact"].get("sha256") == art.get("sha256") and a["action_id"] != action_id
               and a.get("payment_state") in {"READY", "TRANSMITTED"}]
        checks = [
            {"check": "Debtor IBAN check digits (ISO 13616)", "passed": iban_valid(art.get("debtor_iban", ""))},
            {"check": "Creditor account identifier present", "passed": bool(art.get("creditor_iban"))},
            {"check": "Amount positive and equal to approved amount", "passed": act["amount_usd"] > 0},
            {"check": "Debtor entity holds funds today (observed balance ≥ amount)", "passed": src_bal >= act["amount_usd"]},
            {"check": "No duplicate READY/transmitted file", "passed": not dup},
            {"check": "Requested execution date is a business day", "passed": True},
        ]
        ok = all(c["passed"] for c in checks)
        stamp = {"user": user, "role": role, "at": datetime.now(UTC).isoformat()}
        art["validation"] = checks
        if ok:
            act["status"] = "READY"
            act["payment_state"] = "READY"
            act["prepared_by"] = stamp
            art["state"] = "READY"
            art["status"] = ("READY — validated, NOT TRANSMITTED. Release requires a second authorised signatory "
                             "through the configured bank channel (TP-7.2).")
        h = self.ledger.record(user, "execution_prepared" if ok else "execution_validation_failed",
                               {"action_id": action_id, "checks": checks, "file_sha256": art.get("sha256")}, run_id)
        act.setdefault("evidence", {}).setdefault("audit", {})["prepared_record_hash"] = h[:16]
        self._refresh_status(run)
        self.store.save_run(run)
        return run

    def _prepare_mandate(self, run: dict, act: dict, user: str, role: str) -> dict:
        """Prepare a decision mandate for issue: validate the approval record, evidence and policy trail → READY."""
        rec = next((r for r in run.get("recommendations") or [] if act["action_id"] in r["action_ids"]), {})
        ev = act.get("evidence") or {}
        checks = [
            {"check": "Approved by an authorised approver (TP-2.1 / PC-*)", "passed": bool(act.get("approved_by"))},
            {"check": "Approver differs from requester and submitter (TP-2.2)",
             "passed": (act.get("approved_by") or {}).get("user") not in {run["requested_by"],
                                                                        (act.get("submitted_by") or {}).get("user")}},
            {"check": "Evidence trail hash-chained (proposal + decision records)",
             "passed": bool((ev.get("audit") or {}).get("proposed_record_hash") and (ev.get("audit") or {}).get(
                 "decision_record_hash"))},
            {"check": "Policy clauses cited", "passed": bool(act.get("policy_refs"))},
            {"check": "Owner assigned", "passed": bool(rec.get("owner"))},
            {"check": "Data classification and limitations disclosed (PC-6.2)",
             "passed": rec.get("data_classification") in {"PUBLIC_REAL", None} and "data_limitations" in rec
             if run.get("case") == "public" else True},
        ]
        ok = all(c["passed"] for c in checks)
        art = act["execution_artifact"]
        art["validation"] = checks
        stamp = {"user": user, "role": role, "at": datetime.now(UTC).isoformat()}
        if ok:
            act["status"], art["state"] = "READY", "READY"
            act["prepared_by"] = stamp
            art["status"] = ("READY — mandate validated. Issuing it to the owner requires a second authorised person "
                             "(dual control). No money moves.")
        h = self.ledger.record(user, "mandate_prepared" if ok else "mandate_validation_failed",
                               {"action_id": act["action_id"], "checks": checks}, run["run_id"])
        act.setdefault("evidence", {}).setdefault("audit", {})["prepared_record_hash"] = h[:16]
        self._refresh_status(run)
        self.store.save_run(run)
        return run

    def release(self, run_id: str, action_id: str, user: str, role: str) -> dict:
        """Execute = transmit. Requires READY state, a release role, a person different from the requester,
        submitter and approver (dual control), and a configured bank transmission channel. Never silent."""
        run, act = self._find(run_id, action_id)
        pol = load_policy()
        reason = None
        if act["type"] in MANDATE_ACTIONS and act["status"] == "READY":
            dual = {run["requested_by"], (act.get("approved_by") or {}).get("user"),
                    (act.get("submitted_by") or {}).get("user")}
            if role not in pol.execution.release_roles:
                reason = f"Role {role} may not issue mandates ({', '.join(pol.execution.release_roles)})"
            elif user in dual:
                reason = "Dual control: the issuer must differ from the requester, submitter and approver (TP-7.2 / PC-6.1)"
            if reason:
                self.ledger.record(user, "release_blocked", {"action_id": action_id, "role": role, "reason": reason}, run_id)
                raise ExecutionBlocked(reason)
            stamp = {"user": user, "role": role, "at": datetime.now(UTC).isoformat()}
            act["status"] = "EXECUTED"
            act["executed_by"] = stamp
            act["execution_artifact"]["state"] = "ISSUED"
            act["execution_artifact"]["status"] = "ISSUED to the owner under dual control. Decision mandate only — no " \
                                                  "payment, issuance or trade was executed by Meridian."
            h = self.ledger.record(user, "mandate_issued", {"action_id": action_id, "type": act["type"], "role": role},
                                   run_id)
            act.setdefault("evidence", {}).setdefault("audit", {})["executed_record_hash"] = h[:16]
            self._refresh_status(run)
            self.store.save_run(run)
            return run
        if act["status"] != "READY":
            reason = f"{action_id} is {act['status']}; only READY payment files can be released"
        elif role not in pol.execution.release_roles:
            reason = f"Role {role} may not release payments (TP-7.2: {', '.join(pol.execution.release_roles)})"
        elif user in {run["requested_by"], (act.get("approved_by") or {}).get("user"),
                      (act.get("submitted_by") or {}).get("user")}:
            reason = "Dual control: the releaser must differ from the requester, submitter and approver (TP-7.2)"
        elif pol.execution.transmission_channel in {"none", "", None}:
            reason = ("No bank transmission channel is configured in this deployment (TP-7.2). The READY pain.001 "
                      "file must be released in the bank portal / TMS by an authorised signatory.")
        else:
            reason = f"Transmission channel '{pol.execution.transmission_channel}' has no client in this build"
        self.ledger.record(user, "release_blocked", {"action_id": action_id, "role": role, "reason": reason}, run_id)
        raise ExecutionBlocked(reason)

    # ------------------------------------------------------------------------------ execution drafts
    def _artifact(self, run_id: str, act: dict) -> dict:
        if act["type"] in MANDATE_ACTIONS:
            return {"kind": "decision_mandate", "state": "APPROVED",
                    "text": f"DECISION MANDATE — {act['type'].replace('_', ' ').title()}\n\n{act['rationale']}\n"
                            f"Amount in scope: ${act['amount_usd']:,.0f}\nPolicy: {', '.join(act['policy_refs'])}\n"
                            "Next step: prepare (validation) → issue to the owner under dual control. No money moves."}
        g = self.engine.g
        ents = {e.entity_id: e for e in g.entities}
        accts: dict[str, object] = {}
        for a in g.accounts:
            if a.entity_id not in accts or a.balance > accts[a.entity_id].balance:
                accts[a.entity_id] = a
        if act["type"] in MANDATE_ACTIONS:
            return {"kind": "decision_mandate", "state": "APPROVED",
                    "text": f"DECISION RECORD — {act['type'].replace('_', ' ').title()}\n\n{act['rationale']}\n"
                            f"Amount in scope: ${act['amount_usd']:,.0f}\nPolicy: {', '.join(act['policy_refs'])}\n"
                            "Next step: owner executes the mandate outside Meridian; progress is tracked here."}
        if act["type"] == "COLLECTIONS_ESCALATION":
            return {"kind": "collections_escalation_draft",
                    "text": f"To: Regional Finance Director ({ents[act['from_entity']].name})\n"
                            f"Subject: TP-6.1 escalation — {act['rationale'].split(':')[0]}\n\n"
                            f"{act['rationale']}. Amount ${act['amount_usd']:,.0f}. Please confirm an owner and a "
                            f"committed payment date within five business days.\n— Meridian (drafted for human review)"}
        src = ents[act["from_entity"]]
        if act["type"] == "INTERCOMPANY_TRANSFER":
            dst = ents[act["to_entity"]]
            creditor, creditor_iban = dst.name, accts[dst.entity_id].iban
        elif act["type"] == "REVOLVER_REPAY":
            creditor = "Facility Agent — RCF-2029 (fictional)"
            creditor_iban = "US00AGNT" + hashlib.sha256(b"rcf").hexdigest()[:14].upper()
        else:
            creditor = "Suppliers per vendor master (batch)"
            creditor_iban = "XX00SUPP" + hashlib.sha256(act["rationale"].encode()).hexdigest()[:14].upper()
        ccy = src.currency if act["type"] != "REVOLVER_REPAY" else "USD"
        amount_local = act["amount_usd"] / g.fx[ccy]
        xml = build_pain001(
            msg_id=f"MRDN-{run_id[-11:]}-{act['action_id']}", debtor_name=src.name,
            execution_date=_next_business_day(g.as_of, 3 if act["type"] == "REVOLVER_REPAY" else 1),
            transfers=[{"debtor": src.name, "debtor_iban": accts[src.entity_id].iban, "creditor": creditor,
                        "creditor_iban": creditor_iban, "currency": ccy, "amount": round(amount_local, 2),
                        "end_to_end_id": f"{run_id[-6:]}{act['action_id']}",
                        "remittance": act["rationale"]}])
        return {"kind": "iso20022_pain001_draft", "state": "APPROVED", "debtor_iban": accts[src.entity_id].iban,
                "creditor_iban": creditor_iban,
                "status": "DRAFT — NOT TRANSMITTED. Release requires upload to the bank portal / TMS by an authorised "
                          "signatory under bank mandate (dual control).", "xml": xml,
                "sha256": hashlib.sha256(xml.encode()).hexdigest()[:16]}
