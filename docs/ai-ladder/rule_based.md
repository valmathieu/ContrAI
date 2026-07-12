# Rule-based AI

Hand-coded expert strategies (expert bidding table + card-play heuristics; specs SF-09, SF-10). They are the **first concrete rung** of the AI ladder, implemented in `contrai-engine` as the `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy` pair — concrete implementations of the `BiddingStrategy` / `CardPlayStrategy` interfaces. They are injected into `AiPlayer` (the default strategies) and registered as `AI_LEVELS["expert"]`, so a future MCTS or learned level is a new strategy class rather than an edit to `AiPlayer`. See the [engine docs](../engine/index.md#ai-players) for the injection seam.

`RuleBasedCardPlayStrategy` is stateless between calls: every decision is a pure function of the single frozen `PlayObservation` it is handed for that turn. Whatever card tracking the heuristics need — which cards have fallen, which seats are known void in trump — is derived fresh each turn by replaying the observation's public trick history, never carried across calls or rounds. Because the observation is the only input a strategy ever receives, it can never see another seat's hand.

**Explainability:** the rule trace is the rationale — log which rule fired for each bid/play. The strategy object is the natural home for that trace (AI roadmap §6.1).

> TODO: rule catalogue; planned extensions (deeper card counting, partner inference, signal-based bidding).
