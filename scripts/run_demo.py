"""Narrated end-to-end demo (~20 s first run incl. model training, no API key):  python scripts/run_demo.py"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.governance.audit import AuditLedger  # noqa: E402
from meridian.governance.policy import ApprovalDenied  # noqa: E402
from meridian.orchestration.service import TreasuryService  # noqa: E402

L = "─" * 100


def wrap(t: str, ind: str = "  ") -> str:
    return textwrap.fill(t, 100, initial_indent=ind, subsequent_indent=ind + "  ")


async def main() -> None:
    svc = TreasuryService()
    e = svc.engine
    k, card, bt = e.kpis(), e.model.card, e.backtest()
    print(f"\n{L}\n1. The problem — Orion Industrial Group (fictional), as of {k['as_of']}\n{L}")
    print(wrap(f"${k['cash_usd']/1e6:,.1f}M cash across {k['entities']} entities, {k['bank_accounts']} bank accounts and "
               f"{k['currencies']} currencies; ${k['trapped_cash_usd']/1e6:,.1f}M trapped by capital controls; "
               f"DSO {k['dso_days']} days with ${k['overdue_ar_usd']/1e6:,.1f}M overdue; revolver "
               f"${k['revolver']['drawn_usd']/1e6:,.0f}M drawn at {k['revolver']['rate']:.2%}."))
    print(f"\n{L}\n2. Predicting when customers really pay (ML) \n{L}")
    print(wrap(f"Payment-delay MAE: model {card['mae_days_model_p50']} days vs customer-average "
               f"{card['mae_days_customer_average']} vs ERP 'pays on due date' {card['mae_days_erp_due_date_assumption']}."))
    print(wrap(f"13-week collections back-test error (WAPE): {bt['wape_model']:.1%} vs ERP assumption "
               f"{bt['wape_erp_due_date']:.1%}."))

    print(f"\n{L}\n3. Daily liquidity run — 7 agents over MCP tools\n{L}")
    run = await svc.run(None, requested_by="ana")
    b = run["briefing"]
    print(wrap(b["executive_summary"]))
    for d in b["decisions_required"]:
        print(wrap("▶ " + d))
    print(wrap(f"Grounding: {run['grounding']['claims']} dollar figures checked against tool output → "
               f"{'all grounded' if run['grounding']['grounded'] else 'ungrounded!'}"))
    print("  Agent trace: " + " → ".join(t["actor"] for t in run["trace"]))

    print(f"\n{L}\n4. Governed approvals (TP-2.1 approval matrix, TP-2.2 segregation of duties)\n{L}")
    act = next(a for a in run["actions"] if a["type"] == "REVOLVER_REPAY")
    print(wrap(f"{act['action_id']}: repay ${act['amount_usd']/1e6:,.0f}M of the revolver — needs {act['required_approver']}"))
    try:
        svc.decide(run["run_id"], act["action_id"], "APPROVE", "tom", "treasurer")
    except ApprovalDenied as ex:
        print(wrap(f"✗ Treasurer blocked: {ex}"))
    done = svc.decide(run["run_id"], act["action_id"], "APPROVE", "carla", "cfo", "Approved — notice to agent today")
    art = next(a for a in done["actions"] if a["action_id"] == act["action_id"])["execution_artifact"]
    print(wrap(f"✓ CFO approved → ISO 20022 pain.001 draft generated ({art['status']})"))

    print(f"\n{L}\n5. CFO asks a stress question in plain English\n{L}")
    q = "What if customers pay 30% slower and rates rise 50 bps?"
    s = await svc.run(q, requested_by="tom")
    print(wrap(f"Q: {q}"))
    print(wrap(f"Planner → collection_delay_pct={s['scenario']['collection_delay_pct']}, "
               f"rate_shock_bps={s['scenario']['rate_shock_bps']}"))
    print(wrap(f"Min P10 covenant headroom: ${s['forecast']['min_covenant_headroom_p10_usd']/1e6:,.1f}M "
               f"(base ${run['forecast']['min_covenant_headroom_p10_usd']/1e6:,.1f}M)"))

    print(f"\n{L}\n6. Policy question answered from the documents (hybrid RAG with clause citations)\n{L}")
    p = await svc.run("Who approves a revolver prepayment?", requested_by="carla")
    for kp in p["answer"]["key_points"][:2]:
        print(wrap("• " + kp[:220]))
    v = AuditLedger(svc.store).verify()
    print(f"\n{L}\n7. Audit ledger: valid={v['valid']} · {v['records']} hash-chained records\n{L}\n")


if __name__ == "__main__":
    asyncio.run(main())
