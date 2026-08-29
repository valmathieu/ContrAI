# Rule-based AI

Hand-coded expert strategies (expert bidding table + card-play heuristics; specs SF-09, SF-10). They are the **first concrete rung** of the AI ladder, implemented in `contrai-engine` as the `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy` pair — concrete implementations of the `BiddingStrategy` / `CardPlayStrategy` interfaces. They are injected into `AiPlayer` (the default strategies) and registered as `AI_LEVELS["expert"]`, so a future MCTS or learned level is a new strategy class rather than an edit to `AiPlayer`. See the [engine docs](../engine/index.md#ai-players) for the injection seam.

`RuleBasedCardPlayStrategy` is stateless between calls: every decision is a pure function of the single frozen `PlayObservation` it is handed for that turn. Whatever card tracking the heuristics need — which cards have fallen, which seats are known void in trump (keyed by `Position`) — is derived fresh each turn by replaying the observation's public trick history, never carried across calls or rounds. Because the observation is the only input a strategy ever receives, and every seat it names is a bare `Position` — the observer, the `ObservedPlay` trick records, the `ObservedContract`, each `Bid[Position]` — the strategy is sealed off from another seat's hand by construction: no live player object is reachable by any path from it. Every table question the heuristics ask is therefore phrased in seat terms, including "did my side declare this?", which reads `Position.is_teammate(contract.declarer)`.

When the heuristics run out of ways to contest a trick they concede the cheapest card they can afford, and that ladder **spares trump before it spares points**. The ordering is load-bearing under the §9.5 under-trump exemption: a seat void in the led suit and holding only losing trump is excused from under-trumping, so its whole hand becomes legal and a worthless trump sits beside every plain card — ranking on points alone would pick the trump roughly as often as not and throw away the very card the exemption was written to preserve. Trump is filtered out first, masters second, and the full legal set is the fallback for a pure-trump hand and for every hand at all trump, where nothing is spendable in that sense.

## Bidding tables

Two tables, chosen by the *shape* of the trump being priced rather than by a chain of `if mode is NO_TRUMP`. `_evaluate_modes(rules)` sweeps whatever `bookable_suits(rules)` returns — four suits at a classic table, six when `extended_trump_choices` is on — so the AI can never evaluate a mode the auction would refuse.

### The suit table

Unchanged: rows from 80 to 160 plus the two Slam-family rows, gated on trump length, the trump ladder's own honours (Jack / 9 / Ace), aces held **outside** the trump suit, a trick floor, and a Belote requirement at the top. Trump length matters here and nowhere else — long trump wins tricks by exhaustion, which a plain suit cannot, since anyone may cut it.

### The honours table

No trump and all trump share **one** table, because everything that separates them is carried by the ladder `rules_for(mode)` hands back:

| Contract | Honours (masters + complements) | `tricks_min` |
| --- | --- | --- |
| 80 | 2 | 2 |
| 90 | 3 | 3 |
| 100 | 4 | 4 |
| 110 | 5 | 5 |
| 120 | 6 | 6 |
| 130 | 7 | 7 |
| 140 | 8 | 8 |

- A **master** is a card nothing outranks in its own suit — `rules.higher_ranks(rank, suit) == ()`. The Ace at no trump, the Jack at all trump.
- A **complement** is the card whose *only* superior is held in the same hand — `len(rules.higher_ranks(rank, suit)) == 1` and that rank is in hand. The 10 under its own Ace, the 9 under its own Jack.

Both are **certain tricks** the moment the hand holds them, which is why the ladder prices them identically at +10 over a base of 60. Read at all trump the table says: 80 is two Jacks, 90 three, 100 four, and every 9 sitting under its own Jack adds a rung. Read at no trump: the same rows with Ace for Jack and 10 for 9. That reproduces the house convention's three stated anchors exactly and degrades gracefully in between — three Aces plus one guarded 10 is also 100.

Below every row sits the **opening floor**, `masters >= 2`. A hand cannot open on complements alone: a lone Jack + 9 is two honours but one master, and the 80 rung is the convention's for *two Jacks*. Structurally `complements <= masters`, so every higher row's master floor follows from the honour count and needs no column of its own.

**The point argument behind the convention.** At no trump the Ace is 19 of its suit's 38 (§3.4), so four Aces are half the deck on their own. At all trump the Jack is 14 and the 9 is nine (§3.3) — four Jacks alone are 56 of 152 — while an Ace is only the *third* card of its ladder and worth six. An ace-heavy hand is therefore a lock at no trump and a **trap** at all trump, and the same eight cards (four Aces, four Tens) count 8 honours in one mode and 0 in the other. That single fact is why the table can be shared: the ladder, not the table, is what differs.

**`tricks_min` tracks the honour count**, and deliberately so. Every honour is a certain trick, and `_estimate_tricks` reads position off the *same* ladder `_honours` does, so it provably returns at least `honours` — the floor can never independently fail. Floors set any higher gated the convention's own anchors shut: two Jacks and junk is two certain tricks and cannot be filled to four without adding honours, which would then be a different row. The column earns its place as an **agreement invariant**: the day `_honours` and `_top_card_tricks` stop answering the same question about what tops a ladder, these rows fall out and the tests say so.

**The belote add-on** is the only thing that can lift a bid past the honours ladder's own 140 ceiling. Each K + Q pair the table will actually mark is +20 (§6.6): one per pair under `four`, at most one under `single`, none under `none`, read off `rules_for(mode).belote_suits` filtered by `all_trump_belote`. At no trump the add-on is structurally zero — `NoTrumpRules.belote_suits` is empty — so the knob is inert there whatever it says. The `single` credit is a deliberate over-estimate: only the first pair *announced in play* marks, and an opponent may announce first, so crediting a full +20 for a pair merely held is optimistic and tunable.

**The cap is never re-derived.** Every evaluation ends at `ladder_top(mode, rules)`, which already encodes all trump's three ceilings (160 / 180 / 240 by belote regime) and no trump's 160.

**The Slam rows are shared and unchanged** — trick-gated at `tricks_min = 8` and mode-independent, since the Slam family exists under every trump choice at the same base values (§5.2). A hand of four Jacks and four 9s reaches them through the trick estimator, not the honours ladder.

### What it measures

300 seeded auctions and rounds under `extended_trump_choices = true`, `all_trump_belote = single`. The baseline on `dev` was **0 no-trump and 0 all-trump contracts in 300 auctions** — the AI could not name either mode.

| | contracts / 300 | avg value | made |
| --- | --- | --- | --- |
| all trump | 142 | 99.4 | 57.0% |
| no trump | 84 | 96.9 | 54.8% |
| the four suits | 70 | 98.3 | 91.4% |
| all passed | 4 | — | — |

With the extended modes switched **off** the same 300 deals reproduce the classic table exactly: 125 all-pass, 175 suit contracts, **zero** of either new mode. The belote regime moves all trump the way it should — under `none` the average all-trump value drops to 94.9 and the mode is bid 124 times rather than 142.

Two calibration signals fall out of that table and neither is addressed here. The honours rows are **loose** — a 55–57% make rate against 91% for suit contracts — while the suit table stays **conservative**, all-pass collapsing from 125 to 4 the moment a second family of tables is available. The seven honour rows are starting values; balancing the two tables against each other wants its own measured branch, not a guess folded into this one.

## Per-mode play

No trump and all trump were *playable* by the engine well before they were playable by the AI. Three heuristics silently assumed trump is exactly one suit, and each is now one regime-neutral rule instead of three parallel code paths — the `TrumpRules` ladder answers the mode-specific part, so nothing branches on `if mode is NO_TRUMP`.

**"Can what I play be cut?" is not "how many spades are left".** `_opponents_might_have_trump` narrowed the round's trump to `round_trumps[0]` — spades, at all trump, where every suit is trump — and then counted them. Once the spades had fallen it answered `False`, meaning "the opponents cannot ruff", which then emptied the lead path's plain-cards filter. All trump answers `False` up front now, and for a real reason rather than an accident of counting: §6.4 forbids cutting across suits, so a trump an opponent holds is never a threat to another suit. No trump was already correct — its trump enumeration is empty — and a suit contract still counts.

**The top of the ladder replaces `Rank.ACE`.** Two lead branches hardcoded the ace as "the card that wins its suit outright". That is right at a suit contract and at no trump and wrong at all trump, where the ace is only the *third* card of its ladder (`J 9 A 10 K Q 8 7`) and worth 6 — cashing it hands the trick to any Jack or 9 behind it. `_top_of_ladder` asks `rules.higher_ranks(rank, suit)` instead and keeps whatever tops the suit: the ace under two regimes, the Jack under the third. The rule also sharpens the *classic* game, where a trump ace is likewise not a winner: it is no longer led as one just because trump happens to be the longest holding.

**Holding trump back needs trump to be a proper subset.** The lead path drops trump from its winner search once both opponents are proven trump-void, keeping it as the guaranteed late winner. At all trump that filter removes the entire hand and drops the ladder straight through to conceding, so it is skipped there — where nothing can be cut, nothing needs holding back.

**The opening lead reads the regime, not the hand's shape.** The declaring branch was gated on `if trump_cards:`, which is `False` for every no-trump hand — so a no-trump declarer fell through to the concede rule and gave away trick 1. It now dispatches on the regime: strongest trump at a suit contract (avoiding the 9 first), and otherwise the top of the *longest* suit, so the lead has continuation behind it. A suit-blind global maximum on `rank_in_suit` would have been meaningless at all trump, where all four suits rank alike. The defender branch generalises the same way — top of the *shortest* suit, before the declarer can cut it.

Each of these rationales names the regime it played under and cites `extended_trump_choices`, the §9.2 knob that put no trump and all trump on the table at all.

**The trick estimator asks the ladder too.** `_estimate_tricks(mode)` resolves the regime once and sums `_top_card_tricks` across all four suits, adding the length bonus only where the regime treats a suit as trump. The per-suit rule is what the trump-only version always said, read off `rules.higher_ranks` instead of spelled out as *Jack and 9 → 2, Jack alone → 1, 9 with support → 1*: **the top of the ladder is a trick, and the second is a trick when the hand holds another card of the suit to back it.** The same code then answers for a plain suit (ace, then ten) and for an all-trump one (Jack, then 9). Before, a hand of four aces and four tens estimated a clean sweep of 8 at *every* mode — correct at no trump, badly wrong at all trump, where each of those aces sits under its own Jack and 9. It reads 8 and 0 respectively now. That matters beyond bidding: `_should_double` prices its threat with the same estimator, so a doubling decision against an all-trump contract was being made on plain-suit assumptions.

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
