# SharpLine (working name)

Autonomous World Cup trading agent for the TxODDS x Superteam hackathon, **Trading Tools and Agents** track ($16K, 10/4/2). Submissions close July 19, 2026.

## Concept

An agent that does, automatically and with discipline, what a sharp bettor does by hand: ingest live TxLINE odds and scores, price each market's fair value with a Monte Carlo model, de-vig the consensus odds, detect where the model and the market disagree (and where the line is moving sharply), then size and manage a risk-controlled position. Every signal and position is logged on Solana devnet for a verifiable, auditable trail. Fully autonomous: it polls, decides, and acts with no manual input.

## Why it wins (maps to the judging criteria)

- **Core functionality & data ingestion:** consumes the TxLINE SSE odds and scores streams live, and runs identically on historical replay.
- **Autonomous operation:** an unattended loop. No human in the decision path.
- **Logic & code architecture:** the decision logic is a real, documented model (Monte Carlo fair value + de-vig + Kelly-sized discipline), not a heuristic alert. Deterministic and defensible.
- **Innovation & novelty:** most entries will be "alert when the line moves." Ours adds a fair-value MODEL and RISK DISCIPLINE, which is the difference between an alert and an agent. This is the moat, and it is already built (the WC simulator and the trade-discipline skill).
- **Production readiness:** a trading desk could deploy it. It is the same edge-and-discipline framework, just automated.

## TxLINE endpoints we use

Auth (free World Cup tier, no payment):
- `POST /auth/guest/start` -> guest JWT.
- On-chain subscribe (devnet) via the published IDL + program addresses, Service Level 1 (60s delay) or 12 (real-time).
- `POST /api/token/activate` -> long-lived API token. All data calls use `Authorization: Bearer <apiToken>`.

Data:
- Fixtures: latest fixtures snapshot -> the 104 WC matches and their fixture IDs.
- Odds: real-time SSE stream of odds updates (live); historical 5-minute-interval odds and as-of snapshots (replay for the demo).
- Scores: real-time SSE stream; full historical sequence of score updates per fixture (replay).
- Verification (optional, the on-chain angle): Merkle-proof endpoints for odds, scores, and fixtures.

Resources: quickstart and World Cup docs at txline.txodds.com/documentation, program addresses + devnet IDL at txline-docs.txodds.com/documentation/programs, support on the TxODDS Discord.

## Repo structure

```
sharpline/
├── README.md                  # product pitch + install + demo link (for judges)
├── ARCHITECTURE.md            # this file
├── auth/                      # TypeScript: one-time on-chain subscribe + activate
│   └── activate.ts            # -> writes the API token to .env (gitignored)
├── agent/                     # Python: the brain
│   ├── feed.py                # TxLINE ingestion (SSE live + historical replay)
│   ├── pricing.py             # Monte Carlo fair value + de-vig of consensus odds
│   ├── signals.py             # model-vs-market edge + sharp-move detector + confidence
│   ├── discipline.py          # sizing, R:R floor, exits, no-bet rule (from trade-discipline)
│   ├── agent.py               # the autonomous loop: ingest -> price -> signal -> decide -> log
│   └── pnl.py                 # paper/devnet P&L tracking
├── onchain/                   # TypeScript/Anchor: devnet program to log signals + positions
├── tests/                     # offline tests on recorded replay fixtures
└── docs/
    ├── demo-script.md         # the 5-minute video walkthrough plan
    └── api-feedback.md        # required TxLINE feedback for the submission
```

## Build sequence (about one week)

1. TxLINE access: wallet, free-tier subscribe on devnet, activate, confirm an authenticated call.
2. Data feed: fixtures + live odds/scores streams + replay.
3. Pricing brain: port the WC simulator + de-vig.
4. Signal engine: edge + sharp-move + confidence, with outcome tracking.
5. Discipline + autonomous loop + P&L.
6. On-chain devnet logging, then the submission package (demo video on a replayed match, public repo, technical doc, API feedback).

## Demo plan (this is heavily weighted)

Matches finish before judging, so there is no live activity during review. Use TxLINE historical replay: pick a real past match (ideally one of the upsets), replay its odds and scores stream at speed, and record a 5-minute walkthrough showing the agent ingesting the real feed, pricing the market, flagging an edge or a sharp move, sizing a disciplined position, and tracking P&L, with the signals written on devnet. A working system on real replayed data beats a polished mockup.

## Stack

TypeScript for the on-chain pieces (subscribe/activate and devnet logging, since their SDK is Anchor/TS). Python for the brain (the simulator and discipline logic are already Python). The two talk through a small local interface (the agent reads the API token from `.env`, writes log records the TS logger picks up, or calls a thin TS CLI).
