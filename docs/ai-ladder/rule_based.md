# Rule-based AI

Hand-coded expert strategies (expert bidding table + card-play heuristics; specs SF-09, SF-10). They are the **first concrete rung** of the AI ladder, implemented in `contrai-engine` as the `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy` pair — concrete implementations of the `BiddingStrategy` / `CardPlayStrategy` interfaces. They are injected into `AiPlayer` (the default strategies) and registered as `AI_LEVELS["expert"]`, so a future MCTS or learned level is a new strategy class rather than an edit to `AiPlayer`. See the [engine docs](../engine/index.md#ai-players) for the injection seam.

**Explainability:** the rule trace is the rationale — log which rule fired for each bid/play. The strategy object is the natural home for that trace (AI roadmap §6.1).

> TODO: rule catalogue; planned extensions (deeper card counting, partner inference, signal-based bidding).
