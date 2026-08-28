# Rule-based AI

Hand-coded expert strategies (expert bidding table + card-play heuristics; specs SF-09, SF-10). They are the **first concrete rung** of the AI ladder, implemented in `contrai-engine` as the `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy` pair — concrete implementations of the `BiddingStrategy` / `CardPlayStrategy` interfaces. They are injected into `AiPlayer` (the default strategies) and registered as `AI_LEVELS["expert"]`, so a future MCTS or learned level is a new strategy class rather than an edit to `AiPlayer`. See the [engine docs](../engine/index.md#ai-players) for the injection seam.

`RuleBasedCardPlayStrategy` is stateless between calls: every decision is a pure function of the single frozen `PlayObservation` it is handed for that turn. Whatever card tracking the heuristics need — which cards have fallen, which seats are known void in trump (keyed by `Position`) — is derived fresh each turn by replaying the observation's public trick history, never carried across calls or rounds. Because the observation is the only input a strategy ever receives, and every seat it names is a bare `Position` — the observer, the `ObservedPlay` trick records, the `ObservedContract`, each `Bid[Position]` — the strategy is sealed off from another seat's hand by construction: no live player object is reachable by any path from it. Every table question the heuristics ask is therefore phrased in seat terms, including "did my side declare this?", which reads `Position.is_teammate(contract.declarer)`.

When the heuristics run out of ways to contest a trick they concede the cheapest card they can afford, and that ladder **spares trump before it spares points**. The ordering is load-bearing under the §9.5 under-trump exemption: a seat void in the led suit and holding only losing trump is excused from under-trumping, so its whole hand becomes legal and a worthless trump sits beside every plain card — ranking on points alone would pick the trump roughly as often as not and throw away the very card the exemption was written to preserve. Trump is filtered out first, masters second, and the full legal set is the fallback for a pure-trump hand and for every hand at all trump, where nothing is spendable in that sense.

## Explainability

Every return path of both strategies is explained. `choose_bid` answers with a `BidDecision` and `choose_card` with a `CardDecision` (`model/player/rationale.py`); each carries a `Rationale` naming four things:

| Field | What it holds |
| --- | --- |
| `rule` | The rule that fired, worded as the method's own docstring words it — *cash the master*, *ruff to win*, *support partner*. |
| `detail` | One sentence, in the §10 English vocabulary, on what that meant for this hand and this trick. |
| `considered` | The alternatives weighed, already rendered — the runner-up cards or contracts the ladder ranked below the chosen one. |
| `citations` | The `RuleConfig` knobs consulted, as `RuleCitation(knob, value, effect)` records. |

A citation is deliberately narrow: it names the knob, its value, and what it changed **at this decision**. The concede branch under the §9.5 exemption emits `RuleCitation("under_trump_exemption", "True", "discarded instead of under-trumping")` — and only when the exemption actually decided something, i.e. when a losing trump sat in the legal set beside the plain card being thrown. A seat holding no trump was never excused from anything, so it cites nothing.

**Why the return type and not a side-channel.** The obvious alternative — stash the trace on the strategy object and let the caller read it back — is only correct while the strategy is driven in live turn order. A search rolling out a hypothetical world, or a harness scoring one seat's policy against another's, would overwrite the attribute before anyone read it. Routing the trace through the return value also means the rungs above these rules need no second seam: an MCTS level explains itself with visit counts, a win-rate estimate and a principal variation; a learned policy with top-k action probabilities. Both are "why this move", and both fit `considered` / `citations` unchanged. That is the AI roadmap's §6.1 requirement satisfied at the one place it cannot quietly rot.

`Round` keeps what its AI seats decided in `bid_decisions` / `card_decisions`. A human seat contributes nothing — a person's reasoning is not the engine's to record.

> TODO: rule catalogue; planned extensions (deeper card counting, partner inference, signal-based bidding).
