# TxLINE API Feedback

Feedback from integrating SharpLine against the TxLINE devnet API and Solana programs. Submitted as part of the hackathon requirement. Overall the feed was clean and the on-chain design is a genuinely novel and useful primitive. Notes below are meant to be constructive.

## What worked well

- The de-vigged `TXLineStablePriceDemargined` book is excellent. Getting a consensus line whose implied probabilities already sum to one, from a single source, removed a whole layer of work and is exactly what a fair-value model wants to compare against.
- The nested `Score` object with per-phase breakdowns (H1, HT, H2, ET, Total) is rich and unambiguous once understood.
- The permissionless on-chain subscribe plus API activation flow is a clean idea and worked on devnet once the account setup was right.
- The SSE streams and the snapshot endpoints share field shapes, so one parser handles both live and replay.

## Friction points

1. **Two-header auth is easy to miss.** Data calls need both `Authorization: Bearer <guest JWT>` and `X-Api-Token: <apiToken>`. A first integration naturally tries the API token as the bearer and gets a 401 with no hint. A one-line note in the 401 body, or a bold callout at the top of the data examples, would save time.

2. **Empty results are ambiguous.** `/api/odds/snapshot/{id}` returns an empty array both when a fixture is unpriced (too far out) and when its market is suspended (in-running). Distinguishing "no coverage yet" from "suspended" without cross-referencing the score phase is hard. A status field on the odds response would help.

3. **Historical endpoints are hard to discover and bounded.** `/api/odds/historical/{id}` returns 404 (there appears to be no odds analog to `/api/scores/historical/{id}`), and the scores historical window (started between two weeks and six hours ago) is easy to fall outside of without a clear error. For a hackathon where matches finish before judging, a documented way to pull a finished fixture's full odds sequence would be very valuable for replay demos.

4. **Encoded vs JSON stat representation.** The soccer feed documents stats as `(period * 1000) + key` encodings, but the JSON scores feed uses the nested `Score` object. It took a live probe to learn the encoded form is only for the on-chain Merkle proofs. A sentence clarifying which representation appears where would prevent a wrong first implementation.

5. **Devnet coverage is thin.** At times only one fixture was live, which makes it hard to exercise a multi-fixture agent. More continuous devnet sample data (even synthetic replays on a loop) would improve the developer experience.

## Minor

- The Devnet IDL link and the exact free-tier account list (PDAs, treasury vault, token program) would be easier to find grouped on one page. The subscribe instruction's account set was the single hardest thing to get right.
- The OpenAPI file at `/docs/docs.yaml` is served as binary and was not directly readable through a simple fetch; a rendered reference or a plain-text mirror would help.

## Net

The primitive (verifiable, de-vigged consensus odds anchored on-chain) is strong and worth building on. Most of the friction was documentation discoverability rather than the API itself.
