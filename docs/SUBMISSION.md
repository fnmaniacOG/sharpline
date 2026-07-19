# SharpLine: Technical Overview

## Core idea

SharpLine is an autonomous World Cup trading agent. It does, automatically and with discipline, what a sharp bettor does by hand: read the live TxLINE feed, price each match's fair value with its own model, compare that price to the de-vigged StablePrice consensus, take a risk-controlled position where a real edge exists, and write every decision to Solana as a verifiable record. It polls, decides, sizes, and settles with no human in the loop.

The model is Elo plus Poisson. Team strength comes from World-Football-Elo ratings that the agent refines from actual tournament results (all 103 group and knockout games), converted to expected goals and summed into 1x2 and draw-no-bet fair values. Because a pure model overrates underdogs against a sharp line, the agent anchors its price to the de-vigged consensus and only deviates with conviction. Discipline then decides: a minimum edge and confidence, an odds band that skips near-certain favorites and extreme longshots, fractional Kelly sizing capped per market, and at most one position per outcome. Every bet and settlement is posted to Solana devnet as a signed Memo transaction, giving an auditable, tamper-evident trail anyone can verify by signature.

## Highlights

Technical: a documented fair-value model rather than a heuristic alert; live market anchoring plus self-calibration from results; two markets priced from one model (1x2 and draw-no-bet); per-market settlement; result-based Elo learning; and an on-chain audit trail written directly from Python via solders and JSON-RPC. A local control-panel dashboard shows the model versus the sharp line, the decision and its discipline checks, P&L, and clickable devnet signatures.

Business: it is a deployable, auditable trading-desk primitive. The verifiable on-chain decision log is the trust layer a real desk or counterparty needs, and the edge-and-discipline framework generalizes well beyond the World Cup to any market TxLINE covers.

## TxLINE endpoints used

Access and auth
- On-chain `subscribe` instruction on the TxLINE Solana program (devnet), free World Cup tier
- `POST /auth/guest/start` for the guest JWT
- `POST /api/token/activate` for the long-lived API token
- All data calls send both `Authorization: Bearer <JWT>` and `X-Api-Token: <apiToken>`

Data
- `GET /api/fixtures/snapshot` (optionally by `competitionId`) for the schedule
- `GET /api/odds/snapshot/{fixtureId}` for StablePrice odds (used the demargined `1X2_PARTICIPANT_RESULT` and the level-line `ASIANHANDICAP_PARTICIPANT_GOALS` as draw-no-bet)
- `GET /api/scores/snapshot/{fixtureId}` for live scores
- `GET /api/scores/updates/{fixtureId}` and `GET /api/scores/historical/{fixtureId}` as settlement fallbacks for finished games
- SSE `/api/odds/stream` and `/api/scores/stream` are supported in the feed layer

Stack: Python for the agent (feed, model, discipline, loop) and TypeScript plus solders for the on-chain pieces.
