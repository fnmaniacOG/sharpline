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
from feed import TxLineClient, to_agent_update, winning_outcome, DNB_MARKET
from model import model_probs, anchor_to_market, devig, tune_from_market, dnb_probs
from ratings import learn_from_results
from agent import Agent, Position

SEED_GAMES = 20   # ratings start seeded from the 20 group-stage results (backfill_elo.py)

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


CACHE = "dashboard/panels_cache.json"     # last-known per-fixture panels (survives restarts)
SESSION = "dashboard/session.json"        # full trading session (bankroll, positions, trade log)


def load_session(agent) -> dict:
    """Restore a prior session into `agent`. Returns the session bookkeeping dict."""
    try:
        with open(SESSION) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    agent.bankroll = s.get("bankroll", agent.bankroll)
    agent.realized = s.get("realized", 0.0)
    agent.bets = s.get("bets", 0)
    agent.wins = s.get("wins", 0)
    agent.positions = [Position(**p) for p in s.get("positions", [])]
    return {"wins": s.get("wins", 0), "losses": s.get("losses", 0),
            "settled": set(s.get("settled", [])), "logs": s.get("logs", []),
            "learned": set(s.get("learned", [])),
            "games_learned": s.get("games_learned", SEED_GAMES)}


def save_session(agent, ss, logs) -> None:
    try:
        with open(SESSION, "w") as f:
            json.dump({"bankroll": round(agent.bankroll, 2), "realized": round(agent.realized, 2),
                       "bets": agent.bets, "wins": ss["wins"], "losses": ss["losses"],
                       "positions": [vars(p) for p in agent.positions],
                       "settled": list(ss["settled"]), "learned": list(ss["learned"]),
                       "games_learned": ss["games_learned"], "logs": logs[-80:]}, f)
    except OSError:
        pass


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


def write_state(path, panel, agent, wins, losses, logs, watch, games_learned):
    st = agent.stats()
    state = {**panel, "bankroll": st["bankroll"], "pnl": st["realizedPnL"],
             "record": f"{wins}-{losses}", "logs": logs[-14:], "watch": watch[:10],
             "games_learned": games_learned}
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

    models, calibrated = {}, set()
    panels = load_panels()                              # last-known slate (survives restarts)
    ss = load_session(agent)                            # continue the same trading session
    wins, losses = ss["wins"], ss["losses"]
    settled, learned, logs = ss["settled"], ss["learned"], ss["logs"]
    games_learned = ss["games_learned"]
    if logs:
        print(f"resumed session: bankroll ${agent.stats()['bankroll']}, "
              f"{agent.stats()['bets']} bets, {len(agent.positions)} open, "
              f"record {wins}-{losses}, learned from {games_learned} games")

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
            neutral = (f.get("competition_id") == 72)
            # --- odds -> decide across markets (pre-match only) ---
            try:
                markets = [] if started else client.all_markets(fid)
            except Exception:
                markets = []
            if markets:
                if fid not in models:
                    models[fid] = model_probs(f["p1"], f["p2"], neutral=neutral)
                onextwo = next((m for m in markets if m.market == "1x2"), None)
                if args.calibrate and fid not in calibrated and onextwo:
                    tune_from_market(f["p1"], f["p2"], devig(onextwo.outcomes), args.learn, persist=True)
                    models[fid] = model_probs(f["p1"], f["p2"], neutral=neutral)
                    calibrated.add(fid)
                dnb_summary = None
                for snap in markets:
                    raw_probs = models[fid] if snap.market == "1x2" \
                        else dnb_probs(f["p1"], f["p2"], neutral=neutral)
                    anchored = anchor_to_market(raw_probs, devig(snap.outcomes), args.model_weight)
                    decs = agent.process(to_agent_update(snap, anchored))
                    if snap.market == "1x2":     # the main panel tracks the 1x2 market
                        prev = panels.get(fid, {})
                        panel = panel_for(f, anchored, snap.outcomes, decs[0] if decs else None,
                                          prev.get("phase", "pre-match"), prev.get("score", "0 - 0"))
                        if not decs:
                            tail = [l for l in agent.log[-3:] if l["fixture"] == fid]
                            if tail:
                                best = max(tail, key=lambda l: l["signal"].get("edge", -9))
                                why = best["decision"].get("reason", "")
                                if why:
                                    panel["reason"] = f"closest was {best['signal']['outcome']}: {why}"
                        panels[fid] = panel
                        last_fid = fid
                    elif snap.market == DNB_MARKET:
                        d0 = decs[0] if decs else None
                        dnb_summary = {
                            "odds": {"HOME": round(snap.outcomes.get("HOME", 0), 2),
                                     "AWAY": round(snap.outcomes.get("AWAY", 0), 2)},
                            "decision": "BET" if d0 else "PASS",
                            "out": d0["outcome"] if d0 else None,
                        }
                    for d in decs:
                        desc = (f"{f['p1']} v {f['p2']} [{snap.market}]: {d['outcome']} "
                                f"${d['stake']} @ {d['decimal']:.2f} (edge {d['edge']:.1%})")
                        sig = chain.post_memo(f"SharpLine BET | {desc}") if onchain else None
                        logs.append({"kind": "BET", "desc": desc, "sig": sig})
                        print("BET    " + desc + (f"  ->  {sig}" if sig else ""))
                        if args.log:
                            log_decision(args.log, {"t": snap.ts, "type": "BET", "fixture": fid,
                                                    "market": snap.market, "outcome": d["outcome"],
                                                    "stake": d["stake"], "odds": d["decimal"],
                                                    "edge": round(d["edge"], 4),
                                                    "conf": round(d["confidence"], 3), "sig": sig})
                if dnb_summary and fid in panels:
                    panels[fid]["dnb"] = dnb_summary
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
                # learn the ratings from the actual result (once per finished game)
                if sc.ended and fid not in learned:
                    learn_from_results([(f["p1"], f["p2"], sc.home, sc.away)], persist=True)
                    models.clear()                 # re-price every fixture with the updated ratings
                    learned.add(fid)
                    games_learned += 1
                    print(f"LEARNED {f['p1']} {sc.home}-{sc.away} {f['p2']} "
                          f"(ratings now from {games_learned} games)")
                held = [p for p in agent.positions if p.fixture == fid]
                if sc.ended and fid not in settled and held:
                    onextwo_win = winning_outcome(sc)
                    dnb_win = "PUSH" if sc.home == sc.away else ("HOME" if sc.home > sc.away else "AWAY")
                    winners = {"1x2": onextwo_win, DNB_MARKET: dnb_win}
                    for pos in held:                      # settle and log each market's result
                        win = winners.get(pos.market)
                        if win == "PUSH":
                            ppnl, tag = 0.0, "PUSH (refund)"
                        elif pos.outcome == win:
                            ppnl, tag = round(pos.stake * (pos.decimal - 1), 2), "WON"
                            wins += 1
                        else:
                            ppnl, tag = round(-pos.stake, 2), "lost"
                            losses += 1
                        desc = f"{f['p1']} v {f['p2']} [{pos.market}]: {win} · {tag} {ppnl:+.2f}"
                        sig = chain.post_memo(f"SharpLine SETTLE | {desc}") if onchain else None
                        logs.append({"kind": "SETTLE", "desc": desc, "sig": sig})
                        print("SETTLE " + desc + (f"  ->  {sig}" if sig else ""))
                        if args.log:
                            log_decision(args.log, {"t": sc.ts, "type": "SETTLE", "fixture": fid,
                                                    "market": pos.market, "result": win,
                                                    "pnl": ppnl, "sig": sig})
                    agent.settle_markets(fid, winners)
                    settled.add(fid)

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
        save_panels(panels)                              # remember the slate
        ss.update(wins=wins, losses=losses, games_learned=games_learned)  # settled/learned shared refs
        save_session(agent, ss, logs)                    # persist the trading session
        if args.dashboard and (spot or watch):
            write_state(args.dashboard, spot or {"home": "—", "away": "—", "phase": "scanning",
                        "score": "0 - 0", "model": None, "odds": None, "decision": "PASS",
                        "out": None, "stake": "—", "odds_taken": None,
                        "reason": "waiting for the first priced line", "checks": []},
                        agent, wins, losses, logs, watch, games_learned)

        st = agent.stats()
        print(f"[{it}] bankroll ${st['bankroll']} · P&L {st['realizedPnL']:+.2f} · "
              f"bets {st['bets']} · open {st['openPositions']} · record {wins}-{losses}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
