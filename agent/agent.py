"""
agent.py - the autonomous loop.

Per market update: price it, detect movement, form a signal, decide with discipline, and
(paper) execute. No manual input. Settle positions when a fixture resolves and track P&L.
This is the heart of the submission; the live TxLINE feed and the on-chain logger plug into
the same interface (`process` for each update, `settle` per result).

Run:  python agent.py   (runs a synthetic replay end to end with sanity checks)
"""

from __future__ import annotations
from dataclasses import dataclass, field

from pricing import price_market
from signals import SharpMoveDetector, make_signal
from discipline import decide, Params


@dataclass
class Position:
    fixture: str
    market: str
    outcome: str
    stake: float
    decimal: float
    opened_ts: float


@dataclass
class Agent:
    bankroll: float = 1000.0
    params: Params = field(default_factory=Params)
    detector: SharpMoveDetector = field(default_factory=SharpMoveDetector)
    positions: list[Position] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    realized: float = 0.0
    bets: int = 0
    wins: int = 0

    def _holding(self, fixture, market, outcome) -> bool:
        return any(p.fixture == fixture and p.market == market and p.outcome == outcome
                   for p in self.positions)

    def _holding_market(self, fixture, market) -> bool:
        """Already have a position anywhere in this market (e.g. any 1x2 outcome)."""
        return any(p.fixture == fixture and p.market == market for p in self.positions)

    def process(self, update: dict) -> list[dict]:
        """update: {fixture, market, ts, outcomes:{name:decimal}, model_probs:{name:p}}"""
        priced = price_market(update["model_probs"], update["outcomes"])
        candidates = []
        for outcome, pr in priced.items():
            key = (update["fixture"], update["market"], outcome)
            move = self.detector.update(key, pr["marketTrueP"], update["ts"])
            sharp = self.detector.is_sharp(move)
            sig = make_signal(outcome, pr, move, sharp)
            dec = decide(sig, pr, self.bankroll, self.params)
            self.log.append({"ts": update["ts"], "fixture": update["fixture"],
                             "signal": sig, "decision": dec})
            if dec["action"] == "BET":
                candidates.append((dec, pr, outcome))

        # Discipline: at most ONE position per market. Never back two mutually exclusive
        # outcomes of the same match (that just pays two margins). Act only on the single
        # highest expected-value disagreement.
        decisions = []
        if candidates and not self._holding_market(update["fixture"], update["market"]):
            dec, pr, outcome = max(candidates, key=lambda c: c[1].get("ev", c[0]["edge"]))
            self.bankroll -= dec["stake"]
            self.positions.append(Position(update["fixture"], update["market"],
                                           outcome, dec["stake"], dec["decimal"], update["ts"]))
            self.bets += 1
            decisions.append(dec)
        return decisions

    def settle(self, fixture: str, winning_outcome: str) -> float:
        """Resolve all open positions on a fixture. BACK bets pay stake*decimal if they hit."""
        pnl = 0.0
        keep = []
        for pos in self.positions:
            if pos.fixture != fixture:
                keep.append(pos); continue
            if pos.outcome == winning_outcome:
                payout = pos.stake * pos.decimal
                self.bankroll += payout
                pnl += payout - pos.stake
                self.wins += 1
            else:
                pnl -= pos.stake          # stake already deducted at open
        self.positions = keep
        self.realized += pnl
        return round(pnl, 2)

    def settle_markets(self, fixture: str, winners: dict) -> float:
        """Settle each open position on a fixture against its market's winning outcome.
        `winners` maps market -> winning outcome (e.g. {"1x2":"HOME","DNB":"HOME"}, or "PUSH")."""
        pnl = 0.0
        keep = []
        for pos in self.positions:
            if pos.fixture != fixture:
                keep.append(pos); continue
            win = winners.get(pos.market)
            if win is None:
                keep.append(pos); continue          # market not resolved; leave open
            if win == "PUSH":                        # draw-no-bet on a draw: refund the stake
                self.bankroll += pos.stake
                continue
            if pos.outcome == win:
                self.bankroll += pos.stake * pos.decimal
                pnl += pos.stake * (pos.decimal - 1)
                self.wins += 1
            else:
                pnl -= pos.stake
        self.positions = keep
        self.realized += pnl
        return round(pnl, 2)

    def stats(self) -> dict:
        return {
            "bankroll": round(self.bankroll, 2),
            "realizedPnL": round(self.realized, 2),
            "bets": self.bets,
            "wins": self.wins,
            "winRate": round(self.wins / self.bets, 3) if self.bets else None,
            "openPositions": len(self.positions),
        }


if __name__ == "__main__":
    # Synthetic replay of France v Senegal 1x2. The model sees the DRAW as underpriced,
    # and over three updates sharp money confirms by drifting toward the draw.
    a = Agent(bankroll=1000)
    model = {"FRA": 0.62, "DRAW": 0.27, "SEN": 0.11}   # model fair value (from the WC sim)

    a.process({"fixture": "FRA_v_SEN", "market": "1x2", "ts": 0,
               "outcomes": {"FRA": 1.50, "DRAW": 4.8, "SEN": 8.0}, "model_probs": model})
    a.process({"fixture": "FRA_v_SEN", "market": "1x2", "ts": 300,
               "outcomes": {"FRA": 1.55, "DRAW": 4.2, "SEN": 8.5}, "model_probs": model})  # draw shortens
    a.process({"fixture": "FRA_v_SEN", "market": "1x2", "ts": 600,
               "outcomes": {"FRA": 1.60, "DRAW": 3.9, "SEN": 9.0}, "model_probs": model})

    bet_logs = [l for l in a.log if l["decision"]["action"] == "BET"]
    print("bets placed:", len(bet_logs))
    for l in bet_logs[:1]:
        print("  ->", l["decision"]["outcome"], "stake", l["decision"]["stake"],
              "@", l["decision"]["decimal"], "|", l["decision"]["reason"])

    pnl = a.settle("FRA_v_SEN", winning_outcome="DRAW")     # the draw lands
    print("settled PnL:", pnl)
    print("stats:", a.stats())

    assert a.bets >= 1, "agent should have found the draw edge"
    assert a.realized > 0, "backing the winning draw should profit"
    print("agent loop checks pass")
