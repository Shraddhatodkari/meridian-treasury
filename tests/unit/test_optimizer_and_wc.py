from meridian.governance.policy import load_policy


def test_optimizer_respects_policy(engine):
    opt = engine.optimize()
    assert opt["status"] == "optimal"
    ents = {e.entity_id: e for e in engine.g.entities}
    fc = engine.forecast()
    for t in opt["transfers"]:
        e = ents[t["from_entity"] if t["to_entity"] == "E01" else t["to_entity"]]
        if t["to_entity"] == "E01" and e.capital_controls:  # trapped cash only within allowance (CR-*)
            assert t["amount_usd"] <= e.repatriation_allowance_usd + 1_000
        if t["from_entity"] == "E01":                      # TP-3.2: fund only up to the operating need
            import numpy as np
            trough = float(np.percentile(fc.entity_paths_usd[e.entity_id].min(axis=1), 10))
            assert t["amount_usd"] <= fc.entity_buffers_usd[e.entity_id] - trough + 1_000
    assert not any(t["from_entity"] == "E12" for t in opt["transfers"])  # China: no discretionary upstreaming
    rv = load_policy().revolver
    rep = opt["revolver_repay_usd"]
    assert rep == 0 or (rep >= rv.prepay_min_usd and rep % rv.prepay_multiple_usd == 0)  # RCF-8.1
    assert opt["annualized_benefit_usd"] > 0


def test_no_residual_shortfalls_after_optimisation(engine):
    assert engine.optimize()["residual_shortfalls"] == []


def test_early_pay_only_when_return_beats_cost_of_funds(engine):
    ep = engine.early_pay()
    assert ep["selected"] and all(b["implied_annual_return"] > ep["cost_of_funds"] for b in ep["selected"])


def test_kpis(engine):
    k = engine.kpis()
    assert 40 < k["dso_days"] < 100 and k["trapped_cash_usd"] > 0 and k["net_leverage_x"] < k["max_net_leverage_x"]
