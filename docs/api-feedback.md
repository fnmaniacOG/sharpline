# TxLINE API Feedback

Our experience building SharpLine against the TxLINE devnet API and Solana program. Overall the feed is clean and the on-chain design is a genuinely novel primitive we enjoyed building on. Notes below are meant to be constructive, and reflect real things we hit.

## What we liked most

The de-vigged StablePrice consensus is the standout. Getting a single, margin-free fair line whose implied probabilities already sum to one removed an entire layer of work and is exactly what a fair-value model wants to compare against. It let us focus on the model and the discipline instead of cleaning odds.

The permissionless access model is elegant. Paying (or, for the World Cup tier, subscribing at zero cost) with an on-chain instruction and then activating an API token is a clean, novel flow, and it worked end to end on devnet.

The on-chain verifiability is a strong idea. Being able to anchor odds and scores to Merkle roots, and to write our own decisions to the chain as signed memos, made the "auditable trading agent" story real rather than aspirational.

The score payload is rich once understood: nested per-participant, per-phase breakdowns with goals, cards, and corners.

## Where we hit friction

Two-header auth is easy to miss. Data calls need both `Authorization: Bearer <guest JWT>` and `X-Api-Token: <apiToken>`. A natural first attempt puts the API token in the bearer and gets a 401 with no hint. One line in the 401 body, or a bold callout in the data examples, would save time.

Finished games were the hardest part. Once a match ends, `GET /api/scores/snapshot/{id}` returns empty, and the final record arrives with `Action: "game_finalised"`, `GameState: "scheduled"` (a string), and no `StatusId`. We were keying "ended" off `StatusId`, so we read finished games as not-started and failed to settle them until we saw a raw sample. Documenting the finalization record, the `Action` values, and that `GameState` is not a reliable finished flag would prevent a whole class of bugs.

Devnet exposes only a rotating window of fixtures. Games that finished a few hours earlier drop out of the fixtures list and the snapshot endpoints, so settling or backfilling purely from the API is not possible once a game ages out. We had to widen our lookback and fall back to a verified-results table for older games.

Distinguishing "no coverage" from "no data yet" is hard. For uncovered fixtures (some friendlies) the odds and score endpoints returned empty bodies or JSON-parse errors rather than a clear "not covered" status, which made diagnosis slow.

The OpenAPI file at `/docs/docs.yaml` is served as binary and was awkward to read directly, and the `SuperOddsType` enum values are not enumerated anywhere, so we discovered `1X2_PARTICIPANT_RESULT` and `ASIANHANDICAP_PARTICIPANT_GOALS` empirically. A rendered reference and an enum list would help.

The dual stat representation (the `(period * 1000) + key` encoding versus the nested JSON `Score` object) needed a live sample to disambiguate; a sentence on which appears where would prevent a wrong first implementation.

## Net

The core primitive, verifiable de-vigged consensus odds anchored on-chain, is strong and worth building on. Almost all of our friction was documentation discoverability and the finished-game/coverage semantics on devnet, not the API design itself.
