"""
feed.py - TxLINE ingestion for the SharpLine agent.

Two modes, one normalized output:
  - live:   Server-Sent-Events streams of odds and scores (during a real match)
  - replay: historical odds/scores pulled once, then re-emitted in time order
            (this is what the demo uses, since WC matches finish before judging)

Both modes yield the SAME normalized events, so agent.py does not care which it is.

Separation of concerns: the feed only carries the MARKET (TxLINE's odds + scores).
The fair-value model_probs come from the pricing brain, not from here. Use
`to_agent_update(snapshot, model_probs)` to merge them right before agent.process().

AUTH (verified against TxLINE docs): every data call needs BOTH headers:
    Authorization: Bearer <guest JWT>     (short-lived, from POST /auth/guest/start)
    X-Api-Token:   <apiToken>             (long-lived, written to .env by activate.ts)
So this client fetches a fresh guest JWT on init and pairs it with the saved apiToken.

The only still-uncertain shapes are the ODDS records (StablePrice format). They live in
the PARSE MAP block; probe.py reveals them and we adjust only that block.

Setup:  pip install requests
        the API token is read from sharpline/.env (written by auth/activate.ts)
"""

from __future__ import annotations
import os
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

import requests


def _first(*vals):
    """Return the first value that is not None (unlike `or`, keeps 0 and '')."""
    for v in vals:
        if v is not None:
            return v
    return None


# ----------------------------------------------------------------------------
# config / .env
# ----------------------------------------------------------------------------

def load_env(env_path: Optional[str] = None) -> dict:
    """Tiny .env reader (KEY=VALUE per line). Returns a dict; does not touch os.environ."""
    p = Path(env_path or (Path(__file__).resolve().parent.parent / ".env"))
    out: dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    for k in ("TXLINE_API_TOKEN", "TXLINE_BASE"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


# ----------------------------------------------------------------------------
# normalized events (what the brain consumes)
# ----------------------------------------------------------------------------

HOME, DRAW, AWAY = "HOME", "DRAW", "AWAY"

# soccer game-phase encoding (TxODDS soccer feed). Ended phases settle a fixture.
PHASE = {1: "NS", 2: "H1", 3: "HT", 4: "H2", 5: "F", 6: "WET", 7: "ET1", 8: "HTET",
         9: "ET2", 10: "FET", 11: "WPE", 12: "PE", 13: "FPE", 14: "I", 15: "A",
         16: "C", 17: "TXCC", 18: "TXCS", 19: "P"}
ENDED_PHASES = {5, 10, 13}   # F, FET, FPE

# odds: the 1x2 market type, and the integer price scale (Prices are decimal odds * 1000)
SUPER_1X2 = "1X2_PARTICIPANT_RESULT"
PRICE_SCALE = 1000.0
# part1 = Participant1 (the feed's "home" side per Participant1IsHome), part2 = Participant2
PRICE_TO_OUTCOME = {"part1": HOME, "draw": DRAW, "part2": AWAY}

# second market: Asian Handicap line 0 == draw-no-bet (back part1 or part2; a draw refunds).
DNB_MARKET = "DNB"


@dataclass
class MarketSnapshot:
    fixture: str
    market: str                 # e.g. "1x2"
    ts: float
    outcomes: dict              # {outcome_name: decimal_odds}

@dataclass
class ScoreUpdate:
    fixture: str
    ts: float
    home: int                   # Participant1 goals (feed home designation)
    away: int                   # Participant2 goals
    phase: str                  # "NS".."FPE"
    ended: bool


# ----------------------------------------------------------------------------
# PARSE MAP - TxLINE-shape-specific. Odds fields are best-guess until probe.py runs.
# ----------------------------------------------------------------------------

def parse_fixture(raw: dict) -> dict:
    """Normalize a /api/fixtures/snapshot record. home/away by Participant1IsHome."""
    p1_home = bool(_first(raw.get("Participant1IsHome"), True))
    p1 = _first(raw.get("Participant1"), raw.get("participant1"))
    p2 = _first(raw.get("Participant2"), raw.get("participant2"))
    return {
        "fixture": str(_first(raw.get("FixtureId"), raw.get("fixtureId"), raw.get("id"))),
        "p1": p1,                       # Participant1 == odds "part1" == model HOME
        "p2": p2,                       # Participant2 == odds "part2" == model AWAY
        "home": p1 if p1_home else p2,  # display only (venue designation)
        "away": p2 if p1_home else p1,
        "p1_is_home": p1_home,
        "start": _first(raw.get("StartTime"), raw.get("startTime")),
        "competition_id": _first(raw.get("CompetitionId"), raw.get("competitionId")),
        "competition": _first(raw.get("Competition"), raw.get("competition")),
    }


def parse_odds_event(raw: dict) -> Optional[MarketSnapshot]:
    """Map one TxLINE StablePrice record -> MarketSnapshot. Only the 1x2 market; other
    SuperOddsTypes (Asian handicap, totals, etc.) return None. Prices are decimal odds * 1000.
    The Demargined bookmaker is already de-vigged, so these are the consensus fair line.
    """
    try:
        if raw.get("SuperOddsType") != SUPER_1X2:
            return None
        fixture = str(_first(raw.get("FixtureId"), raw.get("fixtureId")))
        ts = float(_first(raw.get("Ts"), raw.get("ts"), raw.get("Timestamp"), time.time()))
        names = raw.get("PriceNames") or []
        prices = raw.get("Prices") or []
        if len(names) != len(prices):
            return None
        outcomes: dict[str, float] = {}
        for name, price in zip(names, prices):
            label = PRICE_TO_OUTCOME.get(str(name).lower())
            if label and price:
                outcomes[label] = float(price) / PRICE_SCALE
        if set(outcomes) != {HOME, DRAW, AWAY}:
            return None
        return MarketSnapshot(fixture, "1x2", ts, outcomes)
    except (TypeError, ValueError, KeyError):
        return None


def _market_line(raw: dict):
    """Extract the numeric line from MarketParameters like 'line=2.5'."""
    mp = str(raw.get("MarketParameters") or "")
    for part in mp.replace(";", ",").split(","):
        if "line" in part.lower() and "=" in part:
            try:
                return float(part.split("=")[1])
            except (ValueError, IndexError):
                pass
    return None


def parse_dnb_event(raw: dict) -> Optional[MarketSnapshot]:
    """Asian Handicap on the level line (0) -> draw-no-bet MarketSnapshot {HOME, AWAY}.
    Other handicap lines return None (we only trade the level line).
    """
    try:
        st = str(raw.get("SuperOddsType") or "").upper()
        if "HANDICAP" not in st:
            return None
        line = _market_line(raw)
        if line is None or abs(line) > 1e-9:          # only the level (0) line is draw-no-bet
            return None
        names = [str(n).lower() for n in (raw.get("PriceNames") or [])]
        if "part1" not in names or "part2" not in names:
            return None
        prices = dict(zip(names, raw.get("Prices") or []))
        outcomes = {HOME: float(prices["part1"]) / PRICE_SCALE,
                    AWAY: float(prices["part2"]) / PRICE_SCALE}
        if not (outcomes[HOME] and outcomes[AWAY]):
            return None
        fixture = str(_first(raw.get("FixtureId"), raw.get("fixtureId")))
        ts = float(_first(raw.get("Ts"), raw.get("ts"), time.time()))
        return MarketSnapshot(fixture, DNB_MARKET, ts, outcomes)
    except (TypeError, ValueError, KeyError):
        return None


def _goals(score: dict, participant: str) -> int:
    """Total goals for Participant1/Participant2 from the nested Score object."""
    p = score.get(participant) if isinstance(score, dict) else None
    if not isinstance(p, dict):
        return 0
    tot = p.get("Total") or {}
    return int(tot.get("Goals", 0) or 0)


def parse_score_event(raw: dict) -> Optional[ScoreUpdate]:
    """Map a /api/scores record. Phase from StatusId; goals from nested Score.*.Total.Goals."""
    try:
        fixture = str(_first(raw.get("FixtureId"), raw.get("fixtureId"), raw.get("fixture")))
        ts = float(_first(raw.get("Ts"), raw.get("ts"), raw.get("Timestamp"), time.time()))
        sid = _first(raw.get("StatusId"), raw.get("statusId"), raw.get("gameState"))
        if sid is not None and str(sid).isdigit():
            phase = PHASE.get(int(sid), str(sid))
            ended = int(sid) in ENDED_PHASES
        else:                                   # string GameState like "scheduled"/"finished"
            gs = str(_first(raw.get("GameState"), "NS"))
            phase = gs
            ended = gs.lower() in ("finished", "ended", "complete", "ft")
        score = raw.get("Score") or {}
        home = _goals(score, "Participant1")
        away = _goals(score, "Participant2")
        return ScoreUpdate(fixture, ts, home, away, phase, ended)
    except (TypeError, ValueError):
        return None


def winning_outcome(score: ScoreUpdate) -> str:
    """Final 1x2 result from an ended score (regulation/ET goals; pens settle separately)."""
    if score.home > score.away:
        return HOME
    if score.home < score.away:
        return AWAY
    return DRAW


# ----------------------------------------------------------------------------
# bridge to the brain
# ----------------------------------------------------------------------------

def to_agent_update(snap: MarketSnapshot, model_probs: dict) -> dict:
    """Merge a market snapshot with the model's fair-value probs -> agent.process() input."""
    return {
        "fixture": snap.fixture,
        "market": snap.market,
        "ts": snap.ts,
        "outcomes": snap.outcomes,
        "model_probs": model_probs,
    }


# ----------------------------------------------------------------------------
# TxLINE client (auth: fresh guest JWT + saved X-Api-Token)
# ----------------------------------------------------------------------------

class TxLineClient:
    def __init__(self, api_token: Optional[str] = None, base: Optional[str] = None):
        env = load_env()
        self.api_token = api_token or env.get("TXLINE_API_TOKEN")
        self.base = (base or env.get("TXLINE_BASE") or "https://txline-dev.txodds.com").rstrip("/")
        if not self.api_token:
            raise RuntimeError("no TXLINE_API_TOKEN - run auth/activate.ts first")
        self.s = requests.Session()
        self.refresh_jwt()

    def refresh_jwt(self) -> None:
        r = requests.post(f"{self.base}/auth/guest/start", timeout=30)
        r.raise_for_status()
        self.jwt = _first(r.json().get("token"), r.json().get("jwt"))
        self.s.headers.update({
            "Authorization": f"Bearer {self.jwt}",
            "X-Api-Token": self.api_token,
            "Content-Type": "application/json",
        })

    def _get(self, path: str, **params) -> object:
        r = self.s.get(f"{self.base}{path}", params=params or None, timeout=60)
        if r.status_code == 401:               # JWT expired -> refresh once, retry
            self.refresh_jwt()
            r = self.s.get(f"{self.base}{path}", params=params or None, timeout=60)
        r.raise_for_status()
        return r.json()

    # --- snapshots ---
    def fixtures(self, competition_id: Optional[int] = None) -> list[dict]:
        d = self._get("/api/fixtures/snapshot", **({"competitionId": competition_id} if competition_id else {}))
        rows = d.get("fixtures", d) if isinstance(d, dict) else d
        return [parse_fixture(x) for x in rows]

    def odds_snapshot(self, fixture_id) -> list[MarketSnapshot]:
        d = self._get(f"/api/odds/snapshot/{fixture_id}")
        rows = d.get("odds", d) if isinstance(d, dict) else d
        return [s for s in (parse_odds_event(x) for x in rows) if s]

    def all_markets(self, fixture_id) -> list[MarketSnapshot]:
        """The 1x2 and draw-no-bet snapshots we trade, from one odds call."""
        d = self._get(f"/api/odds/snapshot/{fixture_id}")
        rows = d.get("odds", d) if isinstance(d, dict) else d
        out = []
        for x in rows:
            s = parse_odds_event(x) or parse_dnb_event(x)
            if s:
                out.append(s)
        return out

    def scores_snapshot(self, fixture_id) -> list[ScoreUpdate]:
        d = self._get(f"/api/scores/snapshot/{fixture_id}")
        rows = d.get("scores", d) if isinstance(d, dict) else d
        return [s for s in (parse_score_event(x) for x in rows) if s]

    def scores_historical(self, fixture_id) -> list[ScoreUpdate]:
        d = self._get(f"/api/scores/historical/{fixture_id}")
        rows = d.get("scores", d) if isinstance(d, dict) else d
        return [s for s in (parse_score_event(x) for x in rows) if s]

    # --- live SSE ---
    def _sse(self, path: str) -> Iterator[dict]:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        with self.s.get(f"{self.base}{path}", headers=headers, stream=True, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload in ("", "[DONE]"):
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue

    def stream_odds(self) -> Iterator[MarketSnapshot]:
        for raw in self._sse("/api/odds/stream"):
            snap = parse_odds_event(raw)
            if snap:
                yield snap

    def stream_scores(self) -> Iterator[ScoreUpdate]:
        for raw in self._sse("/api/scores/stream"):
            su = parse_score_event(raw)
            if su:
                yield su


# ----------------------------------------------------------------------------
# replay (the demo path) - merge historical odds + scores into one time-ordered stream
# ----------------------------------------------------------------------------

def replay(odds_raw: Iterable[dict], scores_raw: Iterable[dict]) -> Iterator[tuple[str, object]]:
    """Yield ('odds', MarketSnapshot) and ('score', ScoreUpdate) in timestamp order.

    Feed agent.process() on each 'odds' event, and agent.settle() when a 'score'
    event is ended. Drives identically to a live match, just faster.
    """
    events: list[tuple[float, str, object]] = []
    for raw in odds_raw:
        snap = parse_odds_event(raw)
        if snap:
            events.append((snap.ts, "odds", snap))
    for raw in scores_raw:
        su = parse_score_event(raw)
        if su:
            events.append((su.ts, "score", su))
    events.sort(key=lambda e: e[0])
    for _, kind, obj in events:
        yield kind, obj


if __name__ == "__main__":
    # Offline smoke test: no network, synthetic raw payloads through the real parsers.
    odds_raw = [
        {"FixtureId": "FRA_v_SEN", "Ts": 0, "SuperOddsType": "1X2_PARTICIPANT_RESULT",
         "PriceNames": ["part1", "draw", "part2"], "Prices": [1500, 4800, 8000]},
        {"FixtureId": "FRA_v_SEN", "Ts": 300, "SuperOddsType": "1X2_PARTICIPANT_RESULT",
         "PriceNames": ["part1", "draw", "part2"], "Prices": [1550, 4200, 8500]},
        # an Asian-handicap record that must be ignored by the 1x2 parser
        {"FixtureId": "FRA_v_SEN", "Ts": 150, "SuperOddsType": "ASIANHANDICAP_PARTICIPANT_GOALS",
         "PriceNames": ["part1", "part2"], "Prices": [1814, 2229], "MarketParameters": "line=0.5"},
    ]
    scores_raw = [
        {"FixtureId": "FRA_v_SEN", "Ts": 100, "StatusId": 2,
         "Score": {"Participant1": {"Total": {"Goals": 0}}, "Participant2": {"Total": {"Goals": 0}}}},
        {"FixtureId": "FRA_v_SEN", "Ts": 6000, "StatusId": 5,
         "Score": {"Participant1": {"Total": {"Goals": 1}}, "Participant2": {"Total": {"Goals": 1}}}},
    ]

    seq = list(replay(odds_raw, scores_raw))
    odds_events = [e for e in seq if e[0] == "odds"]
    assert len(odds_events) == 2, "the Asian-handicap record must be filtered out"
    assert seq[0][0] == "odds" and isinstance(seq[0][1], MarketSnapshot)
    assert seq[0][1].outcomes["DRAW"] == 4.8 and seq[0][1].outcomes["HOME"] == 1.5
    last_kind, last = seq[-1]
    assert last_kind == "score" and last.ended and last.phase == "F"
    assert last.home == 1 and last.away == 1
    assert winning_outcome(last) == "DRAW"

    upd = to_agent_update(seq[0][1], {"HOME": 0.62, "DRAW": 0.27, "AWAY": 0.11})
    assert upd["model_probs"]["DRAW"] == 0.27 and upd["outcomes"]["HOME"] == 1.50
    print("feed parse + replay + bridge checks pass")
