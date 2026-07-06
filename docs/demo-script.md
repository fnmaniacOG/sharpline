# SharpLine: Demo Script (5 minutes)

A shot-by-shot plan for the submission video. Goal: show a real autonomous agent making a disciplined, model-driven decision on real TxLINE data and writing it to Solana, in five minutes. Record your terminal, talk over it, keep it moving.

Before recording, run once so the ratings are learned and you have a fixture in mind:

```bash
python3 agent/backfill_elo.py --txline
```

If no match is live at record time, capture one earlier with `python3 agent/run_live.py --record match.jsonl` and narrate over the replay. A working system on real data beats a live match that stalls.

---

## 0:00 to 0:35. The hook (open on the dashboard)

On screen: `dashboard/index.html` full-screen, running its animated walkthrough.

Say: "This is SharpLine, an autonomous trading agent for the World Cup. It reads the live TxLINE odds feed, prices every match with its own model, finds where the market is wrong, sizes a disciplined bet, and writes every decision to Solana. Here it is deciding in real time."

Point at: the model-versus-market bars and the edge badges as they animate. Let one full loop play so a bet fires and settles.

## 0:35 to 1:15. The data is real

On screen: cut to the terminal, run `run_live.py` startup so the fixtures list and a real market line appear.

Say: "That dashboard is driven by this. It authenticates to TxLINE, pulls the World Cup fixtures, and reads the de-vigged StablePrice consensus, the sharp line, already margin-free. Here is a real match and its real odds."

Point at: the fixture name and the three 1x2 prices.

## 1:15 to 2:15. The model, and that it learned

On screen: the `Elo model:` line the agent prints, and open `agent/elo_overrides.json` briefly.

Say: "SharpLine prices the match itself with an Elo and Poisson model. Crucially, these ratings are not static. It replays every finished game and updates each team from the actual result, the way Elo is meant to work. You can see the learned ratings here. Australia rose after beating Turkiye, the favorites settled to form. It learns from the tournament as it happens."

## 2:15 to 3:15. Discipline is the moat

On screen: the agent processing an update, and either a `BET` line or a pass.

Say: "Most bots just alert when a line moves. The difference here is discipline. It anchors its model to the sharp line, so it only bets when it has real conviction, not noise. It sizes with fractional Kelly, caps every stake, never backs two outcomes of the same match, and skips extreme longshots where the model is unreliable. Watch it either take one disciplined position, or correctly stand down."

If showing a pass, say: "No qualifying edge here, so it passes. A desk that only bets when it should is worth more than one that always bets."

## 3:15 to 4:15. On-chain, verifiable

On screen: run `npm run log`, then click one of the printed devnet explorer links.

Say: "Every decision is written to Solana devnet as a signed memo. Here it posts, and here is that exact bet on the explorer, timestamped and tamper-evident. Anyone can audit what the agent did and when, by transaction signature. That is the trustless record a real desk needs."

Integrity note: the signatures in the dashboard animation are visual placeholders. Show at least one real explorer transaction from `npm run log` here so the on-chain claim is backed by a real tx, not the animation.

## 4:15 to 5:00. Close

On screen: the stats line (bankroll, bets, win rate) and the architecture diagram from the README.

Say: "So: real feed in, a model that learns, disciplined sizing, settlement and P&L, and a verifiable on-chain trail, running autonomously. That is SharpLine. Thanks for watching."

---

## Checklist

- [ ] Ratings learned (`backfill_elo.py --txline` run).
- [ ] A fixture with a live or recorded line ready.
- [ ] Wallet funded on devnet (so `npm run log` posts).
- [ ] A decisions file with at least one bet to post (`run_live.py --log agent/decisions.jsonl`).
- [ ] `dashboard/index.html` open full-screen for the opening shot.
- [ ] Explorer tab open to paste a signature into if needed.
- [ ] Terminal font large enough to read on video.
- [ ] Under five minutes. Cut the model section first if you run long.
