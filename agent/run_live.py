"""
run_live.py - the autonomous agent, end to end, on real TxLINE data.

Pipeline per poll:
    feed (real odds)  ->  model (fair value)  ->  agent (price, signal, size, decide)
and on the final whistle: settle and report P&L. No human in the loop.

This is the program the demo records. It picks a fixture that currently has 1x2 odds,
attaches the model's fair value, and runs the agent loop until the match ends or the
iteration cap is hit.

Run on your machine (needs the API token in ../.env):
    pip install requests
    python3 agent/run_live.py                 # auto-pick a fixture with odds
    python3 agent/run_live.py --fixture 18175397 --interval 20
    python3 agent/run_live.py --once          # single pass (quick smoke test)
"""

from __future__ import annotations
import argparse
import time

from feed import TxLineClient, to_agent_update, winning_outcome
from model import model_probs, anchor_to_market, devig, tune_from_market
from agent import Agent


def pick_fixture(client: TxLineClient, fixture_id: str | None) -> dict | None:
    fixtures = client.fixtures()
    if fixture_id:
        return next((f for f in fixtures if str(f["fixture"]) == str(fixture_id)), None)
    now = time.time() * 1000
    upcoming = sorted((f for f in fixtures if (f["start"] or 0) >= now), key=lambda f: f["start"] or 0)
    started = sorted((f for f in fixtures if (f["start"] or 0) < now), key=lambda f: -(f["start"] or 0))
    for f in upcoming + started:                       # first fixture that actually has 1x2 odds
        try:
            if client.odds_snapshot(f["fixture"]):
                return f
        except Exception:
            continue
    return None


def fmt(p: dict) -> str:
    return "  ".join(f"{k} {v:.0%}" for k, v in p.items())


def log_decision(path: str, rec: dict) -> None:
    """Append one compact decision record for the on-chain memo logger to pick up."""
    import json
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


ORDER = ("HOME", "DRAW", "AWAY")


def write_state(path: str, fx: dict, anchored: dict, odds: dict, phase: str, score: str,
                decision: dict | None, agent, logs: list) -> None:
    """Write the current agent state as JSON for the dashboard's live mode."""
    import json
    st = agent.stats()
    checks = []
    if decision:
        checks = [[f"edge {decision['edge']:.1%}", "ok"], [f"conf {decision['confidence']:.2f}", "ok"],
                  ["odds in band", "ok"], ["one per market", "ok"]]
    state = {
        "home": fx["p1"], "away": fx["p2"], "phase": phase, "score": score,
        "model": [round(anchored.get(k, 0), 3) for k in ORDER],
        "odds": [round(odds.get(k, 0), 2) for k in ORDER],
        "decision": "BET" if decision else "PASS",
        "out": decision["outcome"] if decision else None,
        "stake": decision["stake"] if decision else None,
        "odds_taken": decision["decimal"] if decision else None,
        "reason": decision["reason"] if decision else "no qualifying edge — standing down",
        "bankroll": st["bankroll"], "pnl": st["realizedPnL"], "checks": checks,
        "logs": logs[-4:],
    }
    with open(path, "w") as f:
        json.dump(state, f)


def diagnose(client: TxLineClient) -> None:
    """When nothing has 1x2 odds, show what the feed actually has right now."""
    import time
    now = time.time() * 1000
    fixtures = client.fixtures()
    print(f"\n{len(fixtures)} fixtures. raw odds-record counts (any market):")
    for f in sorted(fixtures, key=lambda x: x.get("start") or 0):
        fid = f["fixture"]
        when = (f.get("start") or 0)
        rel = "started" if when < now else f"in {int((when-now)/3.6e6)}h"
        try:
            raw = client._get(f"/api/odds/snapshot/{fid}")
            rows = raw if isinstance(raw, list) else raw.get("odds", [])
            types = sorted({r.get("SuperOddsType") for r in rows if isinstance(r, dict)})
        except Exception as e:
            rows, types = [], [f"err:{e}"]
        print(f"  {fid}  {f['p1']} v {f['p2']:<16}  {rel:>9}  "
              f"odds_records={len(rows)}  markets={types}")
    print("\nif a started match shows records but no 1X2_PARTICIPANT_RESULT, its 1x2 "
          "market is suspended (in-running). pre-match 1x2 returns once a fixture nears kickoff.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=None, help="TxLINE FixtureId (default: auto-pick)")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--interval", type=float, default=20.0, help="seconds between polls")
    ap.add_argument("--max-iters", type=int, default=400)
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--record", default=None, help="append raw odds+scores JSONL to this file")
    ap.add_argument("--model-weight", type=float, default=0.30,
                    help="how much to tilt the sharp line toward the Elo model (0=pure market, 1=pure model)")
    ap.add_argument("--calibrate", action="store_true",
                    help="slowly nudge the two teams' Elo toward this fixture's sharp line (persists)")
    ap.add_argument("--learn", type=float, default=0.10,
                    help="calibration rate per fixture (0.10 = move 10%% toward the market gap)")
    ap.add_argument("--log", default=None,
                    help="append bets and settlements to this JSONL for the on-chain logger")
    ap.add_argument("--dashboard", default=None,
                    help="write live agent state to this JSON for the dashboard (e.g. dashboard/state.json)")
    args = ap.parse_args()

    client = TxLineClient()
    fx = pick_fixture(client, args.fixture)
    if not fx:
        print("no fixture with 1x2 odds available right now.")
        diagnose(client)
        return

    fid = fx["fixture"]
    model_raw = model_probs(fx["p1"], fx["p2"], neutral=(fx.get("competition_id") == 72))
    agent = Agent(bankroll=args.bankroll)

    print(f"fixture {fid}: {fx['p1']} (HOME) v {fx['p2']} (AWAY)  [{fx.get('competition')}]")
    print(f"Elo model:  {fmt(model_raw)}   (anchored to the market at weight {args.model_weight})")
    print(f"bankroll ${args.bankroll:.0f} | polling every {args.interval:.0f}s\n")

    last_ts = None
    calibrated = False
    cur_phase, cur_score = "pre-match", "0 - 0"
    last_decision = None
    last_odds: dict = {}
    last_anchored: dict = {}
    dash_logs: list = []
    for i in range(1 if args.once else args.max_iters):
        if args.record:                        # bank raw records for demo replay
            import json as _json
            with open(args.record, "a") as fh:
                for kind, path, key in (("odds", f"/api/odds/snapshot/{fid}", "odds"),
                                        ("score", f"/api/scores/snapshot/{fid}", "scores")):
                    try:
                        raw = client._get(path)
                        rows = raw if isinstance(raw, list) else raw.get(key, [])
                        for r in rows:
                            fh.write(_json.dumps({"kind": kind, "rec": r}) + "\n")
                    except Exception:
                        pass

        try:
            snaps = client.odds_snapshot(fid)
        except Exception as e:
            print(f"[{i}] odds fetch error: {e}")
            snaps = []

        if snaps:
            snap = snaps[-1]                       # latest 1x2 consensus
            if snap.ts != last_ts:                 # only act on a fresh line
                last_ts = snap.ts
                market_p = devig(snap.outcomes)
                # slow auto-calibration: nudge Elo toward the sharp line ONCE per fixture, then
                # rebuild the model from the learned ratings (persists across runs).
                if args.calibrate and not calibrated:
                    rep = tune_from_market(fx["p1"], fx["p2"], market_p, args.learn, persist=True)
                    model_raw = model_probs(fx["p1"], fx["p2"], neutral=(fx.get("competition_id") == 72))
                    calibrated = True
                    print(f"[{i}] calibrated: gap {rep['cur_gap']}->{rep['applied_gap']} "
                          f"(market {rep['market_gap']}) | {fx['p1']} {rep[fx['p1']][0]}->{rep[fx['p1']][1]}, "
                          f"{fx['p2']} {rep[fx['p2']][0]}->{rep[fx['p2']][1]}")
                # anchor the Elo model to THIS de-vigged line, then look for an edge
                anchored = anchor_to_market(model_raw, market_p, args.model_weight)
                last_anchored, last_odds = anchored, snap.outcomes
                mkt = "  ".join(f"{k} {v:.2f}" for k, v in snap.outcomes.items())
                print(f"[{i}] market {mkt}")
                for d in agent.process(to_agent_update(snap, anchored)):
                    print(f"     -> BET {d['outcome']} ${d['stake']} @ {d['decimal']:.2f} "
                          f"(edge {d['edge']:.1%}, conf {d['confidence']:.2f}) | {d['reason']}")
                    last_decision = d
                    dash_logs.append({"kind": "BET",
                                      "desc": f"{d['outcome']} ${d['stake']} @ {d['decimal']:.2f} "
                                              f"(edge {d['edge']:.1%})"})
                    if args.log:
                        log_decision(args.log, {"t": snap.ts, "type": "BET", "fixture": fid,
                                                "outcome": d["outcome"], "stake": d["stake"],
                                                "odds": d["decimal"], "edge": round(d["edge"], 4),
                                                "conf": round(d["confidence"], 3)})
                if args.dashboard:
                    write_state(args.dashboard, fx, anchored, snap.outcomes,
                                cur_phase, cur_score, last_decision, agent, dash_logs)

        try:
            scores = client.scores_snapshot(fid)
        except Exception:
            scores = []
        if scores:
            sc = scores[-1]
            cur_phase, cur_score = sc.phase, f"{sc.home} - {sc.away}"
            print(f"[{i}] {sc.phase}  {fx['p1']} {sc.home} - {sc.away} {fx['p2']}")
            if sc.ended:
                result = winning_outcome(sc)
                pnl = agent.settle(fid, result)
                print(f"\nFULL TIME. settled P&L: {pnl:+.2f}")
                dash_logs.append({"kind": "SETTLE", "desc": f"{result} · P&L {pnl:+.2f}"})
                if args.log:
                    log_decision(args.log, {"t": sc.ts, "type": "SETTLE", "fixture": fid,
                                            "result": result, "pnl": pnl})
                if args.dashboard:
                    write_state(args.dashboard, fx, last_anchored, last_odds, cur_phase,
                                cur_score, last_decision, agent, dash_logs)
                break

        if args.once:
            break
        time.sleep(args.interval)

    print("\nstats:", agent.stats())


if __name__ == "__main__":
    main()
