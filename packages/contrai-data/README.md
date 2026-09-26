# contrai-data

The ContrAI **game-record format**: one append-only JSONL file per game, one event per line,
shared by everything that produces or consumes a game of contrée — the engine's `--record`, the
scraper's observation of an online table, the verifier, the replay screens, and the learners
further up the AI ladder.

Pure data: frozen event dataclasses, a strict token codec, an append-and-flush writer, a
truncation-tolerant reader, a projection that folds a flat event stream back into rounds, and the
verdict model `contrai verify` writes beside the records, and `build_catalog` — a derived SQLite
index over a records root (`catalog.sqlite`, stdlib `sqlite3`) for the questions asked across a
corpus.
Depends on `contrai-core` and on nothing else.
