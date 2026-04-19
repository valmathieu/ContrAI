# Supervised learning

Train a policy on scraped human games. Not yet implemented.

Pipeline (target): scrape → SQLite (handled by `contrai-scraper`) → feature engineering → train (PyTorch default) → evaluate against `AiPlayer` baseline.

**Explainability:** top-k action probabilities; attention / attribution methods.

> TODO: feature schema; training methodology; evaluation harness.
