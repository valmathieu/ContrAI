# contrai-core

Shared domain model for the ContrAI workspace — pure data + invariants, no orchestration.

## Module map

Source lives at `packages/contrai-core/src/contrai_core/`:

| Module          | Contents                                                                                |
| --------------- | --------------------------------------------------------------------------------------- |
| `types.py`      | `Suit`, `Rank` enums and the `CARD_SUITS` tuple                                         |
| `card.py`       | `Card`                                                                                  |
| `deck.py`       | `Deck`                                                                                  |
| `hand.py`       | `Hand` (list-compatible API including `copy()` + query helpers)                         |
| `team.py`       | `Team`                                                                                  |
| `player.py`     | `BasePlayer` (engine `Player` extends it)                                               |
| `bid.py`        | `Bid`, `PassBid`, `ContractBid`, `DoubleBid`, `RedoubleBid` (frozen-dataclass sum type) |
| `auction.py`    | `Auction` (bidding-state rule oracle — see §below)                                      |
| `contract.py`   | `Contract`                                                                              |
| `trick.py`      | `Trick`, `current_winner` (module-level trick-winner rule, shared with `PlayState`)     |
| `play.py`       | `Play`, `PlayState` (play-phase rule oracle — see §below), `PlayObservation`            |
| `exceptions.py` | `ContraiError` (base), `InvalidPlayerCountError`, `InvalidCardCountError`, `IllegalBidError`, `IllegalPlayError` + `PlayRuleViolation`, `TrickStateError`, `InvalidContractError` |

Everything above is re-exported from `contrai_core/__init__.py` and is part of the public API — except `current_winner`, which stays a module-level import from `contrai_core.trick`.

## Class structure

```plantuml format="svg" source="class_core.puml"
```

The full domain model in one view. `Trick` is a dumb container of plays that does **not** store trump — `Trick.get_current_winner(trump_suit)` takes trump as a *required* argument (mirroring `Card.get_order`/`get_points`) and works on partial tricks. The engine builds `Trick()` bare and passes the authoritative trump suit from the contract, consuming `get_current_winner` for trick-winner determination, the partner-master legality check, and the view's live winner highlight. `Bid` and its four variants are now frozen `@dataclass(frozen=True, slots=True)` value carriers — player is `field(compare=False)` so equality is *what was announced, not who announced it*, and the auction-state rules ("is this bid legal now?") live entirely on `Auction`. `Auction.is_legal` / `legal_actions` / `apply` replace what used to be `Bid.is_valid_after` and the `BidValidator` utility — including the *auction-freezes-after-a-Double* rule from `contree-domain.md §5.3`. `Auction.apply` raises `IllegalBidError` rather than silently downgrading an illegal bid to a Pass. The defending team is computed at the game level, where both teams are in scope — `Contract` only knows its own attacking side. See [Diagrams](../diagrams/) for the colour convention.

`play.py` adds the play phase's sibling to `Auction`: `PlayState` is an immutable frozen dataclass (`slots=True`) that owns the flat chronological tuple of `Play` records — `Play` is a `NamedTuple` of `(player, card)`, unpacking exactly like the tuples `Trick` and the winner rule already consume. Like `Auction`, the bare constructor performs no validation so tests and search forks can inject arbitrary mid-round states directly; `PlayState.start(contract, players, hands)` is the validated entry point for a fresh deal (exactly 4 players, 4 eight-card hands, 32 distinct cards). Every other view — `trick_number`, `current_trick`, `completed_tricks`, `trick_winners`, `to_act`, `is_terminal()` — is recomputed from `plays` on each access rather than cached; `slots=True` forbids stashing lazy state, keeping the play history the single source of truth. `legal_actions(player)` is the player-parametric legality oracle: it enforces the follow/trump obligations (follow suit; over-trump when trump is led; trump if void and the partner is not currently master, over-trumping an opponent's ruff if able; free discard once the partner is master, or once no obligation applies at all — no trump suit, or none held) but deliberately **no turn check** — the same idiom `Auction.legal_actions` uses for bids, so a caller can ask "what would be legal for this player" independent of whose turn it is. `apply(play)` is the single legality-enforcing transition: it checks turn order, then hand membership, then the obligations, raising `IllegalPlayError` with the matching `PlayRuleViolation` — `OUT_OF_TURN` (wrong player, or the phase is already over), `CARD_NOT_IN_HAND`, or one of `MUST_FOLLOW_SUIT` / `MUST_TRUMP` / `MUST_OVERTRUMP` — plus the legal alternatives for diagnostics. `with_hands(hands)` forks the same public history (contract, players, plays) onto replacement per-seat hands: the determinization primitive a future search-based AI would sample worlds from — no search runs on it yet, this is only the fork primitive.

`observe(player, bids=...)` is `PlayState`'s sanctioned trust boundary: it projects the omniscient state — which holds every seat's hand — down to a `PlayObservation` carrying only `player`'s own hand, the public trick history, the established contract, the auction history the caller passes in, and `player`'s legal cards right now. This is the input surface AI card-play strategies are meant to be handed, never the raw `PlayState` — though the boundary is not fully sealed: the `Play` records in `completed_tricks` / `current_trick` still carry live `BasePlayer` references, so code reaching through `play.player.hand` could technically still see another seat's cards. That gap is a documented follow-up, not something this projection solves. Trick-winner determination for both `Trick` and `PlayState` shares one implementation, the module-level `current_winner(plays, trump_suit)` function in `trick.py`: `Trick.get_current_winner` delegates to it, and `PlayState.trick_winners` / `PlayObservation.current_winner` call it directly — `to_act` reaches the same rule indirectly, via `trick_winners` — one winner rule, not two that could drift apart.

**Exception hierarchy.** Every domain error now subclasses a single `ContraiError` base, so one `except ContraiError` catches the whole family. Each concrete error *also* subclasses `ValueError` (dual inheritance, `ValueError` kept in the MRO) so legacy `except ValueError` call sites keep working unchanged. `IllegalPlayError` is the card-play counterpart to `IllegalBidError`: it carries the offending `Card`, a machine-readable `PlayRuleViolation` reason (`MUST_FOLLOW_SUIT` / `MUST_TRUMP` / `MUST_OVERTRUMP` / `OUT_OF_TURN` / `CARD_NOT_IN_HAND`, a `StrEnum` for clean logging/JSON), and the set of legal alternatives — serving the §6.1 explainability goal and future RL/scraper/server consumers. `TrickStateError` (adding to a complete trick) and `InvalidContractError` (bad contract value/suit, or a redouble without an underlying double) replace the last bare `ValueError`s raised by `Trick`, `ContractBid`, and `Contract`.

## Consumers

- **`contrai-engine`** — direct dependency. Imports core types with `from contrai_core import …` and adds `Player` / `HumanPlayer` / `AiPlayer` / `Game` / `Round` on top.
- **`contrai-scraper`** — planned consumer. Observed games will be materialized into `Card` / `Bid` / `Trick` / … instances before being persisted to SQLite.
- **`contrai-analyzer`** — **does not** depend on core. The analyzer's `SuitSlot` (TRUMP/BLUE/GREEN/PURPLE) is a suit-agnostic abstraction for probability math, intentionally separate from `Suit`. See the [analyzer overview](../analyzer/index.md).

## Conventions

- Type hints everywhere, including private helpers.
- Google-style docstrings on every public class/method/function.
- Didactic comments are welcome — this is a learning project.
- Every Model-layer addition ships with `pytest` tests under `packages/contrai-core/tests/`.

## Tests

Coverage is now complete across every module:
`test_types.py`, `test_card.py`, `test_deck.py`, `test_hand.py`, `test_team.py`, `test_base_player.py`, `test_bid.py`, `test_auction.py`, `test_contract.py`, `test_trick.py`, `test_play_state.py`, `test_play_legality.py`, `test_play_observation.py`, `test_exceptions.py`.

`test_bid.py` covers the data contract of the frozen variants (construction validation, equality, ordering, `__str__`, immutability). The auction-state rules that used to be tested against `Bid.is_valid_after` and `BidValidator` now live in `test_auction.py` against `Auction.is_legal`, `legal_actions`, and `apply`. `test_play_state.py` covers `PlayState.start`'s validation, `apply`'s turn-order/hand-membership/immutability guarantees, `with_hands`'s determinization-fork validation, and the derived views' boundaries (including the `NO_TRUMP` degrade). `test_play_legality.py` exercises `legal_actions` against the follow/over-trump/trump obligation matrix and `apply`'s resulting `PlayRuleViolation` classification. `test_play_observation.py` covers `observe`'s own-hand-only projection, its legal-cards parity with `PlayState.legal_actions`, bids pass-through, the derived properties, and immutability. `test_exceptions.py` covers the dual-inheritance invariant (every domain error is a subclass of both `ContraiError` and `ValueError`), the `PlayRuleViolation` `StrEnum`, and the message/attribute contract of each error; the construction-validation tests in `test_bid.py` / `test_contract.py` / `test_trick.py` assert the specific new types. The remaining engine-side gap is `Round` — see [`engine/index.md`](../engine/index.md#open-work).
