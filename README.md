# SharpLine

**An autonomous World Cup trading agent that prices, decides, and logs on Solana, with no human in the loop.**

Built for the TxODDS x Superteam World Cup Hackathon, Trading Tools and Agents track. Powered by the TxLINE live football feed on Solana.

## What it does

SharpLine does automatically, and with discipline, what a sharp bettor does by hand. It ingests the live TxLINE odds and scores feed, prices each match's fair value with an Elo model that learns from real results, compares that fair value against the de-vigged StablePrice consensus to find genuine edges, sizes a risk-controlled position, and writes every decision to Solana devnet as a verifiable audit trail. It polls, decides, and acts on its own.

## Why it is an agent, not an alert bot

Most entries in this category alert when a line moves. SharpLine adds the two things that separate a trader from an alarm:

- **A fair-value model.** An Elo plus Poisson model, seeded from World-Football-Elo ratings and continuously updated from actual match results, produces an independent 1x2 price for every fixture.
- **Real risk discipline.** It anchors its model to the sharp consensus, only acts on edges it can defend, sizes with fractional Kelly, caps every stake, refuses to back two outcomes of the same match, and skips extreme longshots where model error dominates.

The result is a system a desk could actually deploy, not a notification.

## How it works

```
 TxLINE feed         model              agent                      Solana
 (odds + scores) -> (fair value) -> (price, signal, size, decide) -> (memo log)
```

Each market update flows through one loop: de-vig the consensus line, anchor the Elo model to it, measure the edge and any sharp line movement, size a disciplined position or pass, and on the final whistle settle and record P&L. The same interface runs on live data or on historical replay.

## Repository layout

```
sharpline/
  auth/activate.ts       one-time on-chain subscribe + API activation (TxLINE free tier)
  agent/
    feed.py              TxLINE ingestion (snapshots + SSE streams + replay), normalized
    model.py             Elo + Poisson fair value, market anchoring, self-calibration
    ratings.py           World-Football-Elo result updates (learn from finished games)
    backfill_elo.py      replay finished games to learn the ratings (deterministic)
    pricing.py           de-vig + edge + expected value
    signals.py           model-vs-market edge + sharp-move detector + confidence
    discipline.py        sizing, stake caps, odds band, no-bet rules
    agent.py             the autonomous loop: process -> decide -> settle -> P&L
    run_live.py          run the agent end to end on real TxLINE data
  onchain/log.ts         write each decision to devnet as a Solana Memo transaction
  docs/                  technical write-up, demo script, API feedback
```

## Setup

```bash
# 1. TypeScript deps (auth + on-chain logging)
npm install

# 2. a funded devnet wallet
solana-keygen new -o wallet.json
solana transfer <wallet.json address> 1 --url devnet --allow-unfunded-recipient

# 3. TxLINE free-tier access (subscribes on devnet, writes the API token to .env)
#    download the Devnet IDL to sharpline/idl.json first
npx ts-node auth/activate.ts

# 4. Python deps
pip install requests
```

## Running

```bash
# learn the ratings from every finished game (group stage + completed knockouts)
python3 agent/backfill_elo.py --txline

# run the autonomous agent on a live fixture, calibrating and recording decisions
python3 agent/run_live.py --calibrate --log agent/decisions.jsonl

# post the recorded decisions to Solana devnet as memos (prints explorer links)
npm run log
```

Useful flags: `--once` for a single pass, `--record match.jsonl` to bank a live match for replay, `--model-weight` to set how far the model may deviate from the sharp line.

## On-chain audit trail

Every bet and settlement is written to Solana devnet as an SPL Memo transaction signed by the agent wallet. Each decision is public, timestamped, and tamper-evident, verifiable by its transaction signature on the devnet explorer. This is the trustless record that a market-making desk, or a hackathon judge, can audit end to end.

## How it maps to the judging criteria

- **Data ingestion:** consumes the live TxLINE odds and scores feed, and runs identically on historical replay.
- **Autonomous operation:** an unattended loop with no human in the decision path.
- **Logic and architecture:** a documented model (Elo plus Poisson, de-vig, fractional Kelly) that is deterministic and defensible, not a heuristic.
- **Innovation:** a fair-value model plus real risk discipline plus self-calibration from results, all anchored to a verifiable on-chain record.
- **Production readiness:** the same edge-and-discipline framework a desk would use, automated and auditable.

## Demo

A five-minute walkthrough on a replayed World Cup match: [link to be added].

## Stack

TypeScript for the on-chain pieces (subscribe, activate, memo logging, via the Anchor and web3.js SDKs). Python for the brain (the model, discipline, and agent loop). The two communicate through the local `.env` token and a decisions file.

## License

MIT. See [LICENSE](LICENSE).
