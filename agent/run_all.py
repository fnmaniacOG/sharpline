"""
run_all.py - the agent, autonomous, across the whole slate.

Monitors every relevant fixture at once. For each live line it prices, anchors, and either
takes one disciplined position or passes. It settles matches as they finish, tracks one
shared bankroll and P&L, logs every real decision, and feeds the dashboard. One command,
real trades across real games. No scripted data anywhere.

Run:
    python3 agent/run_all.py --dashboard dashboard/state.json --log agent/decisions.jsonl --calibrate
Then serve the dashboard:  python3 -m http.server 8000   (open http://localhost:8000/dashboard/)
"""

from __future__ import annotations
import argparse
import json
import time

import chain
from feed import TxLineClient, to_agent_update, winning_outcome
from model import model_probs, anchor_to_market, devig, tune_from_market
from agent import Agent

ORDER = ("HOME", "DRAW", "AWAY")
WINDOW_BEFORE_MS = 3 * 3600 * 1000      # include fixtures started up to 3h ago
WINDOW_AFTER_MS = 48 * 3600 * 1000      # and starting within the next two days


def rel_when(start, now) -> str:
    if not start:
        return ""
    h = (start - now) / 3600000.0
    if h < 0:
        return "live"
    if h < 1:
        return f"in {int(h*60)}m"
    return f"in {int(round(h))}h"


CACHE = "dashboard/panels_cache.json"   # last-known per-fixture panels (survives restarts)


def load_panels() -> dict:
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_panels(panels: dict) -> None:
    try:
        with open(CACHE, "w") as f:
            json.dump(panels, f)
    except OSError:
        pass


def log_decision(path: str, rec: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def panel_for(fx, anchored, odds, decision, phase="pre-match", score="0 - 0") -> dict:
    checks = []
    if decision:
        checks = [[f"edge {decision['edge']:.1%} ≥ 3%", "ok"],
                  [f"conf {decision['confidence']:.2f} ≥ 0.50", "ok"],
                  ["odds in band", "ok"], ["one per market", "ok"]]
    return {
        "home": fx["p1"], "away": fx["p2"], "phase": phase, "score": score,
        "model": [round(anchored.get(k, 0.0), 3) for k in ORDER],
        "odds": [round(odds.get(k, 0.0), 2) for k in ORDER],
        "decision": "BET" if decision else "PASS",
        "out": decision["outcome"] if decision else None,
        "stake": (f"{decision['stake']}" if decision else "—"),
        "odds_taken": decision["decimal"] if decision else None,
        "reason": decision["reason"] if decision else "no qualifying edge — standing down",
        "checks": checks,
    }


def write_state(path, panel, agent, wins, losses, logs, watch):
    st = agent.stats()
    state = {**panel, "bankroll": st["bankroll"], "pnl": st["realizedPnL"],
             "record": f"{wins}-{losses}", "logs": logs[-6:], "watch": watch[:10]}
    with open(path, "w") as f:
        json.dump(state, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--model-weight", type=float, default=0.30)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--learn", type=float, default=0.10)
    ap.add_argument("--log", default=None)
    ap.add_argument("--dashboard", default=None)
    ap.add_argument("--max-iters", type=int, default=100000)
    args = ap.parse_args()

    client = TxLineClient()
    agent = Agent(bankroll=args.bankroll)
    all_fixtures = client.fixtures()

    models, calibrated, settled = {}, set(), set()
    panels, logs = load_panels(), []          # restore last-known slate so restarts don't blank it
    for _p in panels.values():                # a fresh agent holds nothing yet; neutralize stale bets
        if _p.get("decision") == "BET":
            _p.update({"decision": "PASS", "out": None, "stake": "—", "odds_taken": None,
                       "reason": "re-pricing after restart"})
    wins = losses = 0

    onchain = chain.available()
    print(f"monitoring the slate ({len(all_fixtures)} fixtures known) | "
          f"on-chain logging: {'ON (devnet memos)' if onchain else 'off (pip install solana to enable)'}\n")
    REFRESH_EVERY = 15   # re-pull the fixture list every ~15 cycles to catch newly scheduled games
    for it in range(args.max_iters):
        if it and it % REFRESH_EVERY == 0:      # refresh the schedule so new fixtures appear
            try:
                all_fixtures = client.fixtures()
            except Exception:
                pass
        now = time.time() * 1000
        slate = [f for f in all_fixtures
                 if now - WINDOW_BEFORE_MS <= (f.get("start") or 0) <= now + WINDOW_AFTER_MS]
        slate.sort(key=lambda f: f.get("start") or 0)   # imminent first
        last_fid = None

        for f in slate:
            fid = f["fixture"]
            started = (f.get("start") or 0) <= now
            # --- odds -> decide (pre-match only; in-running odds do not fit a pre-match model) ---
            try:
                snaps = [] if started else client.odds_snapshot(fid)
            except Exception:
                snaps = []
            if snaps:
                snap = snaps[-1]
                if fid not in models:
                    models[fid] = model_probs(f["p1"], f["p2"], neutral=(f.get("competition_id") == 72))
                market_p = devig(snap.outcomes)
                if args.calibrate and fid not in calibrated:
                    tune_from_market(f["p1"], f["p2"], market_p, args.learn, persist=True)
                    models[fid] = model_probs(f["p1"], f["p2"], neutral=(f.get("competition_id") == 72))
                    calibrated.add(fid)
                anchored = anchor_to_market(models[fid], market_p, args.model_weight)
                decs = agent.process(to_agent_update(snap, anchored))
                prev = panels.get(fid, {})
                panel = panel_for(f, anchored, snap.outcomes, decs[0] if decs else None,
                                  prev.get("phase", "pre-match"), prev.get("score", "0 - 0"))
                if not decs:   # explain WHY it passed (which gate the best outcome missed)
                    tail = [l for l in agent.log[-3:] if l["fixture"] == fid]
                    if tail:
                        best = max(tail, key=lambda l: l["signal"].get("edge", -9))
                        why = best["decision"].get("reason", "")
                        if why:
                            panel["reason"] = f"closest was {best['signal']['outcome']}: {why}"
                panels[fid] = panel
                last_fid = fid
                for d in decs:
                    desc = (f"{f['p1']} v {f['p2']}: {d['outcome']} ${d['stake']} @ "
                            f"{d['decimal']:.2f} (edge {d['edge']:.1%})")
                    sig = chain.post_memo(f"SharpLine BET | {desc}") if onchain else None
                    logs.append({"kind": "BET", "desc": desc, "sig": sig})
                    print("BET    " + desc + (f"  ->  {sig}" if sig else ""))
                    if args.log:
                        log_decision(args.log, {"t": snap.ts, "type": "BET", "fixture": fid,
                                                "outcome": d["outcome"], "stake": d["stake"],
                                                "odds": d["decimal"], "edge": round(d["edge"], 4),
                                                "conf": round(d["confidence"], 3), "sig": sig})
            # --- scores -> update + settle ---
            try:
                scores = client.scores_snapshot(fid)
            except Exception:
                scores = []
            if scores:
                sc = scores[-1]
                if fid in panels:
                    panels[fid]["phase"] = sc.phase
                    panels[fid]["score"] = f"{sc.home} - {sc.away}"
                holding = any(p.fixture == fid for p in agent.positions)
                if sc.ended and fid not in settled and holding:
                    result = winning_outcome(sc)
                    pnl = agent.settle(fid, result)
                    settled.add(fid)
                    wins += 1 if pnl > 0 else 0
                    losses += 1 if pnl <= 0 else 0
                    desc = f"{f['p1']} v {f['p2']}: {result} · P&L {pnl:+.2f}"
                    sig = chain.post_memo(f"SharpLine SETTLE | {desc}") if onchain else None
                    logs.append({"kind": "SETTLE", "desc": desc, "sig": sig})
                    print("SETTLE " + desc + (f"  ->  {sig}" if sig else ""))
                    if args.log:
                        log_decision(args.log, {"t": sc.ts, "type": "SETTLE", "fixture": fid,
                                                "result": result, "pnl": pnl, "sig": sig})

        # spotlight the dashboard on a held position if any, else the last live fixture
        spot = None
        for p in agent.positions:
            if p.fixture in panels:
                spot = panels[p.fixture]
                break
        if not spot and last_fid:
            spot = panels.get(last_fid)
        # watchlist = every fixture in the window, each carrying its FULL panel so the
        # dashboard can load any game's edges on click. Unpriced ones are waiting stubs.
        watch = []
        for f in slate:
            p = panels.get(f["fixture"])
            if p:
                watch.append(p)
            else:
                watch.append({"home": f["p1"], "away": f["p2"],
                              "phase": rel_when(f.get("start"), now), "score": "0 - 0",
                              "model": None, "odds": None, "decision": "WAIT", "out": None,
                              "stake": "—", "odds_taken": None,
                              "reason": "waiting for the market to price this fixture", "checks": []})
        agent.log = agent.log[-300:]   # bound memory over long runs
        save_panels(panels)   # remember the slate for the next run
        if args.dashboard and (spot or watch):
            write_state(args.dashboard, spot or {"home": "—", "away": "—", "phase": "scanning",
                        "score": "0 - 0", "model": None, "odds": None, "decision": "PASS",
                        "out": None, "stake": "—", "odds_taken": None,
                        "reason": "waiting for the first priced line", "checks": []},
                        agent, wins, losses, logs, watch)

        st = agent.stats()
        print(f"[{it}] bankroll ${st['bankroll']} · P&L {st['realizedPnL']:+.2f} · "
              f"bets {st['bets']} · open {st['openPositions']} · record {wins}-{losses}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
