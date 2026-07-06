"""
signals.py - turn priced markets into trade signals.

Two inputs combine into one signal per outcome:
  1. edge: the model-vs-market gap from pricing.py (a static read on value).
  2. sharp move: how fast and how far the market's own probability has shifted recently
     (sharp money moving the line). A move toward an outcome confirms it; a move against
     it warns that the edge may be stale.

Pure, no network. State is just a short rolling history of each outcome's market probability.
"""

from __future__ import annotations
from collections import defaultdict, deque


class SharpMoveDetector:
    """Tracks each outcome's de-vigged market probability over time and flags fast shifts."""

    def __init__(self, window_secs: float = 600, threshold: float = 0.04, maxlen: int = 50):
        self.window = window_secs          # look back this far
        self.threshold = threshold         # a move of this many prob points counts as "sharp"
        self.hist: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=maxlen))

    def update(self, key: tuple, market_p: float, ts: float) -> float:
        """Record a probability point. Returns the signed move (now minus the oldest point
        still inside the window). Positive = the market is moving toward this outcome."""
        h = self.hist[key]
        h.append((ts, market_p))
        ref = None
        for t, p in h:
            if ts - t <= self.window:
                ref = p
                break
        return 0.0 if ref is None else market_p - ref

    def is_sharp(self, move: float) -> bool:
        return abs(move) >= self.threshold


def make_signal(outcome: str, priced: dict, move: float, sharp: bool) -> dict:
    """Combine value edge and line movement into one signal with a confidence in [0, 1]."""
    edge = priced["edge"]                  # model_p - market_true_p
    direction = "BACK" if edge > 0 else ("LAY" if edge < 0 else "NONE")

    # base confidence from edge size, aligned to the 3% edge floor: a 3-point edge maps to
    # ~0.5 confidence (the bet floor), 6 points to full confidence. So clearing the edge floor
    # is enough to act on a static line; sharp moves below then confirm or damp it.
    conf = min(abs(edge) / 0.06, 1.0)

    # sharp money confirms or contradicts:
    #  - line moving toward the outcome we want to BACK confirms it (boost)
    #  - line moving away from it means the market already corrected (the edge is fading -> damp)
    if sharp and direction == "BACK":
        conf = min(1.0, conf + 0.20) if move > 0 else max(0.0, conf - 0.30)
    elif sharp and direction == "LAY":
        conf = min(1.0, conf + 0.20) if move < 0 else max(0.0, conf - 0.30)

    if sharp and move > 0 and edge <= 0:
        rationale = "sharp money moving in but no value edge; watch, do not chase"
    elif direction == "BACK" and sharp and move > 0:
        rationale = "model says underpriced and sharp money agrees"
    elif direction == "BACK":
        rationale = "model says underpriced; line has not moved yet"
    else:
        rationale = "no actionable value"

    return {
        "outcome": outcome,
        "direction": direction,
        "edge": round(edge, 4),
        "ev": priced["ev"],
        "marketMovePct": round(move * 100, 2),
        "sharp": sharp,
        "confidence": round(conf, 3),
        "rationale": rationale,
    }


if __name__ == "__main__":
    det = SharpMoveDetector(window_secs=600, threshold=0.04)
    key = ("FRA_v_SEN", "1x2", "DRAW")
    det.update(key, 0.22, ts=0)
    move = det.update(key, 0.28, ts=300)        # draw prob jumped 6 points in 5 min
    assert det.is_sharp(move), "6-point move should register as sharp"
    sig = make_signal("DRAW", {"edge": 0.05, "ev": 0.12}, move, sharp=True)
    print(sig)
    assert sig["direction"] == "BACK" and sig["confidence"] > 0.5
    print("signal checks pass")
