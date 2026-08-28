# Rule-based AI

Hand-coded expert strategies (expert bidding table + card-play heuristics; specs SF-09, SF-10). They are the **first concrete rung** of the AI ladder, implemented in `contrai-engine` as the `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy` pair — concrete implementations of the `BiddingStrategy` / `CardPlayStrategy` interfaces. They are injected into `AiPlayer` (the default strategies) and registered as `AI_LEVELS["expert"]`, so a future MCTS or learned level is a new strategy class rather than an edit to `AiPlayer`. See the [engine docs](../engine/index.md#ai-players) for the injection seam.

`RuleBasedCardPlayStrategy` is stateless between calls: every decision is a pure function of the single frozen `PlayObservation` it is handed for that turn. Whatever card tracking the heuristics need — which cards have fallen, which seats are known void in trump (keyed by `Position`) — is derived fresh each turn by replaying the observation's public trick history, never carried across calls or rounds. Because the observation is the only input a strategy ever receives, and every seat it names is a bare `Position` — the observer, the `ObservedPlay` trick records, the `ObservedContract`, each `Bid[Position]` — the strategy is sealed off from another seat's hand by construction: no live player object is reachable by any path from it. Every table question the heuristics ask is therefore phrased in seat terms, including "did my side declare this?", which reads `Position.is_teammate(contract.declarer)`.

When the heuristics run out of ways to contest a trick they concede the cheapest card they can afford, and that ladder **spares trump before it spares points**. The ordering is load-bearing under the §9.5 under-trump exemption: a seat void in the led suit and holding only losing trump is excused from under-trumping, so its whole hand becomes legal and a worthless trump sits beside every plain card — ranking on points alone would pick the trump roughly as often as not and throw away the very card the exemption was written to preserve. Trump is filtered out first, masters second, and the full legal set is the fallback for a pure-trump hand and for every hand at all trump, where nothing is spendable in that sense.

## Per-mode play

No trump and all trump were *playable* by the engine well before they were playable by the AI. Three heuristics silently assumed trump is exactly one suit, and each is now one regime-neutral rule instead of three parallel code paths — the `TrumpRules` ladder answers the mode-specific part, so nothing branches on `if mode is NO_TRUMP`.

**"Can what I play be cut?" is not "how many spades are left".** `_opponents_might_have_trump` narrowed the round's trump to `round_trumps[0]` — spades, at all trump, where every suit is trump — and then counted them. Once the spades had fallen it answered `False`, meaning "the opponents cannot ruff", which then emptied the lead path's plain-cards filter. All trump answers `False` up front now, and for a real reason rather than an accident of counting: §6.4 forbids cutting across suits, so a trump an opponent holds is never a threat to another suit. No trump was already correct — its trump enumeration is empty — and a suit contract still counts.

**The top of the ladder replaces `Rank.ACE`.** Two lead branches hardcoded the ace as "the card that wins its suit outright". That is right at a suit contract and at no trump and wrong at all trump, where the ace is only the *third* card of its ladder (`J 9 A 10 K Q 8 7`) and worth 6 — cashing it hands the trick to any Jack or 9 behind it. `_top_of_ladder` asks `rules.higher_ranks(rank, suit)` instead and keeps whatever tops the suit: the ace under two regimes, the Jack under the third. The rule also sharpens the *classic* game, where a trump ace is likewise not a winner: it is no longer led as one just because trump happens to be the longest holding.

**Holding trump back needs trump to be a proper subset.** The lead path drops trump from its winner search once both opponents are proven trump-void, keeping it as the guaranteed late winner. At all trump that filter removes the entire hand and drops the ladder straight through to conceding, so it is skipped there — where nothing can be cut, nothing needs holding back.

**The opening lead reads the regime, not the hand's shape.** The declaring branch was gated on `if trump_cards:`, which is `False` for every no-trump hand — so a no-trump declarer fell through to the concede rule and gave away trick 1. It now dispatches on the regime: strongest trump at a suit contract (avoiding the 9 first), and otherwise the top of the *longest* suit, so the lead has continuation behind it. A suit-blind global maximum on `rank_in_suit` would have been meaningless at all trump, where all four suits rank alike. The defender branch generalises the same way — top of the *shortest* suit, before the declarer can cut it.

Each of these rationales names the regime it played under and cites `extended_trump_choices`, the §9.2 knob that put no trump and all trump on the table at all.

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
