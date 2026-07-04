# SharpLine: Technical Document

An autonomous World Cup trading agent on the TxLINE feed and Solana. This document explains the design, the model, the risk discipline, and the on-chain record, and is honest about what the system does and does not claim.

## 1. Overview

SharpLine runs a single autonomous loop. For each market update it prices the fixture, compares its price to the sharp consensus, decides whether a disciplined bet exists, sizes it, and on settlement records the result. Every decision is written to Solana devnet. There is no human in the decision path.

The design goal is not a black box that claims to beat the market. It is a transparent, deterministic, auditable agent whose every step can be explained and verified. That is what makes it deployable and what makes it judgeable.

## 2. Data layer (`agent/feed.py`)

The feed normalizes the TxLINE API into two event types the rest of the system consumes: a `MarketSnapshot` (1x2 decimal odds for a fixture at a timestamp) and a `ScoreUpdate` (goals and game phase).

Authentication uses two headers on every data call: a short-lived guest JWT from `POST /auth/guest/start` as the bearer, plus the long-lived API token from `POST /api/token/activate` in `X-Api-Token`. The client fetches a fresh JWT on start and refreshes once on a 401.

Endpoints used: `/api/fixtures/snapshot`, `/api/odds/snapshot/{id}`, `/api/scores/snapshot/{id}`, `/api/scores/historical/{id}`, and the `/api/odds/stream` and `/api/scores/stream` SSE feeds.

Two TxLINE specifics are handled centrally. Odds are StablePrice records where `Prices` are decimal odds times 1000 and the `TXLineStablePriceDemargined` bookmaker is already de-vigged, so its implied probabilities sum to one and are the consensus fair line. Scores encode the phase in `StatusId` (5, 10, 13 are ended states) and carry goals in a nested `Score.Participant1.Total.Goals` object. All of this lives in one parse block so it can be adjusted in one place if the feed changes.

The same `replay()` driver merges historical odds and scores into one time-ordered stream, so the agent runs identically on a recorded match, which is how the demo is produced.

## 3. Fair-value model (`agent/model.py`, `agent/ratings.py`)

The model is Elo plus Poisson, the same approach as the project's World Cup simulator. A team's strength is a World-Football-Elo rating. The rating gap converts to an expected goal supremacy by `s = (eloHome - eloAway) / 420` with a base total of 2.6 goals. Two independent Poisson distributions over the scoreline grid are summed analytically into `P(home)`, `P(draw)`, `P(away)`. The analytic sum is exact and deterministic, which is preferable to Monte Carlo sampling for pricing.

### Learning from results

Ratings are not static. `ratings.py` applies the canonical World-Football-Elo update after each finished game: both teams move by how far the result beat expectation, scaled by a goal-difference multiplier and a tournament weight (K of 60 for the World Cup). `backfill_elo.py` replays every finished game, group stage first then completed knockouts, and rewrites the ratings from actual outcomes. It resets to a pristine base table each run, so learning is deterministic and does not compound. This learning is not circular: it updates from what happened on the pitch, not from the market.

### Anchoring to the sharp line

A pure Elo plus Poisson model has a known limitation: it structurally overrates underdogs relative to a sharp line, because Poisson assigns more upset probability than efficient markets do. No rating source or constant fixes this, because it is a property of the model family, not the inputs. Left unchecked, the agent would back long-odds outcomes on raw model disagreement and lose.

The fix is to treat the de-vigged StablePrice consensus as the sharpest available signal and anchor the model to it. The traded fair value is a weighted blend, `w * model + (1 - w) * market`, normalized, with a default model weight of 0.3. The edge the agent acts on becomes `w * (model - market)`, so it only bets when the model has real conviction against the line, never on noise.

### Self-calibration

Running with `--calibrate` performs a second, slower form of learning. Once per fixture, the agent backs the market-implied strength gap out of the de-vigged line (using only the decisive win split, never the draw, to avoid copying the market) and nudges the two teams' ratings toward it by a small rate. Learned ratings persist to `elo_overrides.json` and accumulate across runs.

## 4. Signals and discipline (`agent/pricing.py`, `agent/signals.py`, `agent/discipline.py`)

Pricing de-vigs the market and computes the edge (model probability minus market probability) and expected value per outcome. The signal layer combines that edge with a sharp-move detector that tracks each outcome's market probability over time and flags fast, meaningful moves, producing a direction and a confidence.

The discipline layer turns a signal into a sized bet or a pass. It requires a minimum edge (3 points), a minimum confidence (0.5), and an odds band (skip near-certain favorites below 1.10 and extreme longshots above 7.0 where model error dominates). It sizes with half-Kelly and caps any single stake at 2 percent of bankroll. A market-level rule allows at most one position per match, choosing the single highest expected-value outcome, so the agent never backs two mutually exclusive results.

## 5. The autonomous loop (`agent/agent.py`, `agent/run_live.py`)

Each update is priced, run through the sharp-move detector, turned into a signal, and passed to the discipline layer. A bet, if any, deducts from bankroll and opens a position. On an ended score the fixture is settled, positions pay out or lose, and realized P&L and win rate update. `run_live.py` wires the live feed, the anchored model, optional calibration, and optional decision logging into this loop, and can also do a single pass or record a match for replay.

## 6. On-chain record (`onchain/log.ts`)

Each bet and settlement is posted to Solana devnet as an SPL Memo transaction signed by the agent wallet. The agent appends compact decision records to a JSONL file; the logger posts any not-yet-logged records and stores their signatures, so re-runs only post new ones. The choice of the Memo program over a custom Anchor program is deliberate: it yields the same public, timestamped, verifiable trail (auditable by transaction signature) with far less surface area and risk for a hackathon timeframe. Each posted decision prints a devnet explorer link.

## 7. Testing

Every Python module ships with an offline self-test that runs without network access: de-vig correctness, the sharp-move detector, discipline pass and bet paths, the Elo result update (monotonic in goal difference and in opponent strength), the parsers against real TxLINE record shapes, and the full replay-to-settlement loop. A separate probe confirmed the live API shapes before the parsers were finalized.

## 8. Honest limitations

The model is a demo-grade Elo plus Poisson, not a proven market-beating engine, and the anchoring reflects that: it respects the sharp line and only deviates modestly. The value of the system is the autonomous, disciplined, auditable framework, which is what this track is judged on, not a claim of profitability. Devnet feed coverage is thin at any given moment, so the live demo streams or replays whatever fixture is covered. TxLINE historical odds for finished matches are limited, so result-based rating learning uses final scores rather than closing lines.

## 9. Future work

Deeper markets (Asian handicap and totals, already parsed and filtered), a totals-aware draw model to capture low-scoring matchups the strength model cannot, closing-line-value tracking to grade decisions against the market's final price, and a custom Anchor program if a typed on-chain position account is wanted over memos.
