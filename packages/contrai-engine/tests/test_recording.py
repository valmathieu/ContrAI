"""Pins the RecordingView wrapper: forwarding, event emission, lifecycle.

Games are driven with real :class:`AiPlayer` seats and either a seeded
RNG or a fully stacked deck rather than mocks — the point of a decorator
over the view is that it survives the *real* engine, and a mock game
would prove nothing about the hooks the model actually fires.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest
from contrai_core import (
    Card,
    Deck,
    Position,
    Rank,
    RuleConfig,
    Suit,
    TeamSide,
)
from contrai_data import (
    FORMAT,
    BeloteHeld,
    BidMade,
    CardPlayed,
    EndReason,
    GameEnded,
    GameStarted,
    HandsDerivation,
    Header,
    RecordSource,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    ScoreSource,
    SeatKind,
    SlamOutcome,
    load_game,
    read_events,
)

from contrai_engine.cli import main
from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.model.round import marked_components
from contrai_engine.options import TableAids
from contrai_engine.recording import (
    UNSET,
    RecordingView,
    RecordRequest,
    finish_recording,
)


# ---------------------------------------------------------------------------
# Inner-view doubles
# ---------------------------------------------------------------------------


class _Spy:
    """An inner view recording which hooks reached it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.aids = TableAids()

    def attach(self, game, target_score):
        self.calls.append(("attach", target_score))

    def on_round_dealt(self, round_):
        self.calls.append(("on_round_dealt", round_.round_number))

    def on_bid_made(self, player, bid, history):
        self.calls.append(("on_bid_made", player.position, len(history)))

    def on_card_played(self, player, card, plays):
        self.calls.append(("on_card_played", player.position, card))

    def on_belote_announced(self, player, kind, suit, round_):
        self.calls.append(("on_belote_announced", player.position, kind, suit))

    def on_round_complete(self, round_, running_scores):
        self.calls.append(("on_round_complete", round_.round_number))

    def show_end_game(self, status):
        self.calls.append(("show_end_game",))
        return "q"


class _Deaf:
    """An inner view with none of the push hooks — the hasattr contract."""

    def show_end_game(self, status):
        return "q"


# ---------------------------------------------------------------------------
# Deck stacking — the inverse of ``Deck.deal``'s 3-2-3 pattern
# ---------------------------------------------------------------------------


def _stack_deck(hands: dict[str, list[Card]]) -> Deck:
    """Build a ``Deck`` whose ``deal()`` reproduces exactly ``hands``.

    The same seat-letter wrapper ``test_round_lifecycle`` uses over
    ``Deck.stacked``: index 0=N, 1=E, 2=S, 3=W is the deal order these
    scenarios are written in.

    Args:
        hands: Seat letter to the eight cards that seat must be dealt.

    Returns:
        A deck stacked for that deal.
    """
    return Deck.stacked([hands[seat] for seat in ("N", "E", "S", "W")])


def _stacked_game(hands: dict[str, list[Card]], rules: RuleConfig | None = None) -> Game:
    """A four-AI game whose first deal is exactly ``hands``, in deal order.

    ``Game`` owns its deck and shuffles it before the first deal, so the
    stack is installed *and* the shuffle neutralised — a no-op shuffle on
    a pre-stacked deck is the only way to reach a chosen deal through the
    real ``Game.manage_round`` path.

    The seat letters are **deal order**, not compass seats: ``Game``
    deals from the seat after the dealer, and the dealer is drawn at
    random. What the letters pin is the *shape* of the deal — "N" and "S"
    are partners, "N" opens the bidding — which is what these scenarios
    are about. Use :func:`_seat_of` to name the positions they landed on.

    Args:
        hands: Deal-order letter to the eight cards that seat must be dealt.
        rules: The table ruleset, or ``None`` for the defaults.

    Returns:
        The game, not yet dealt.
    """
    players = [
        AiPlayer("N", Position.NORTH),
        AiPlayer("E", Position.EAST),
        AiPlayer("S", Position.SOUTH),
        AiPlayer("W", Position.WEST),
    ]
    game = Game(players, rules=rules)
    game.deck = _stack_deck(hands)
    game.deck.shuffle = lambda: None
    game.deck.cut = lambda: None
    return game


# The belote deal from ``test_round_lifecycle``: North holds the King and
# the Queen of the trump suit it wins the auction in.
BELOTE_HANDS: dict[str, list[Card]] = {
    "N": [
        Card(Suit.SPADES, Rank.JACK),
        Card(Suit.SPADES, Rank.QUEEN),
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.SPADES, Rank.NINE),
        Card(Suit.SPADES, Rank.SEVEN),
        Card(Suit.HEARTS, Rank.ACE),
        Card(Suit.DIAMONDS, Rank.ACE),
        Card(Suit.CLUBS, Rank.SEVEN),
    ],
    "E": [
        Card(Suit.HEARTS, Rank.NINE),
        Card(Suit.HEARTS, Rank.TEN),
        Card(Suit.HEARTS, Rank.JACK),
        Card(Suit.HEARTS, Rank.QUEEN),
        Card(Suit.DIAMONDS, Rank.NINE),
        Card(Suit.DIAMONDS, Rank.JACK),
        Card(Suit.CLUBS, Rank.NINE),
        Card(Suit.CLUBS, Rank.TEN),
    ],
    "S": [
        Card(Suit.SPADES, Rank.EIGHT),
        Card(Suit.SPADES, Rank.TEN),
        Card(Suit.SPADES, Rank.ACE),
        Card(Suit.HEARTS, Rank.SEVEN),
        Card(Suit.HEARTS, Rank.EIGHT),
        Card(Suit.DIAMONDS, Rank.SEVEN),
        Card(Suit.DIAMONDS, Rank.EIGHT),
        Card(Suit.CLUBS, Rank.EIGHT),
    ],
    "W": [
        Card(Suit.HEARTS, Rank.KING),
        Card(Suit.DIAMONDS, Rank.TEN),
        Card(Suit.DIAMONDS, Rank.QUEEN),
        Card(Suit.DIAMONDS, Rank.KING),
        Card(Suit.CLUBS, Rank.JACK),
        Card(Suit.CLUBS, Rank.QUEEN),
        Card(Suit.CLUBS, Rank.KING),
        Card(Suit.CLUBS, Rank.ACE),
    ],
}

# The all-pass deal: every seat holds two cards of each suit, so no suit
# can clear the bidding table's most lenient row.
ALL_PASS_HANDS: dict[str, list[Card]] = {
    "N": [
        Card(Suit.SPADES, Rank.SEVEN),
        Card(Suit.SPADES, Rank.TEN),
        Card(Suit.HEARTS, Rank.EIGHT),
        Card(Suit.HEARTS, Rank.JACK),
        Card(Suit.DIAMONDS, Rank.NINE),
        Card(Suit.DIAMONDS, Rank.QUEEN),
        Card(Suit.CLUBS, Rank.TEN),
        Card(Suit.CLUBS, Rank.KING),
    ],
    "E": [
        Card(Suit.SPADES, Rank.EIGHT),
        Card(Suit.SPADES, Rank.JACK),
        Card(Suit.HEARTS, Rank.NINE),
        Card(Suit.HEARTS, Rank.KING),
        Card(Suit.DIAMONDS, Rank.SEVEN),
        Card(Suit.DIAMONDS, Rank.TEN),
        Card(Suit.CLUBS, Rank.JACK),
        Card(Suit.CLUBS, Rank.QUEEN),
    ],
    "S": [
        Card(Suit.SPADES, Rank.NINE),
        Card(Suit.SPADES, Rank.KING),
        Card(Suit.HEARTS, Rank.TEN),
        Card(Suit.HEARTS, Rank.QUEEN),
        Card(Suit.DIAMONDS, Rank.EIGHT),
        Card(Suit.DIAMONDS, Rank.ACE),
        Card(Suit.CLUBS, Rank.SEVEN),
        Card(Suit.CLUBS, Rank.NINE),
    ],
    "W": [
        Card(Suit.SPADES, Rank.QUEEN),
        Card(Suit.SPADES, Rank.ACE),
        Card(Suit.HEARTS, Rank.SEVEN),
        Card(Suit.HEARTS, Rank.ACE),
        Card(Suit.DIAMONDS, Rank.JACK),
        Card(Suit.DIAMONDS, Rank.KING),
        Card(Suit.CLUBS, Rank.EIGHT),
        Card(Suit.CLUBS, Rank.ACE),
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def contrai_home(tmp_path, monkeypatch):
    """Keep every test in this file out of the developer's real home.

    ``records_root()`` reads ``$CONTRAI_HOME``, and a bare ``--record``
    resolves through it — an unredirected run would scatter records under
    ``~/.contrai``.
    """
    home = tmp_path / "contrai-home"
    monkeypatch.setenv("CONTRAI_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def pinned_rng():
    """Pin the global RNG, and restore it afterwards.

    The card-play strategy draws from the global ``random`` module to
    break ties nothing else separates, so a recorded game is only
    reproducible with the draw pinned.
    """
    state = random.getstate()
    random.seed(20260911)
    yield
    random.setstate(state)


def _play_to_the_end(game: Game, view: RecordingView) -> None:
    """Drive ``game`` through ``view`` until a side crosses the target."""
    while not game.check_game_over().game_over:
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)
    view.show_end_game(game.check_game_over())


def _only_record(root: Path):
    """The one record file under ``root``, and the events it holds."""
    (path,) = (Path(root) / "games").glob("*.jsonl")
    return path, read_events(path).events


@pytest.fixture
def recorded_game(tmp_path):
    """One 4-AI game played to the target under ``tournament``, recorded."""
    players = [AiPlayer(f"AI {p.name}", p) for p in Position]
    game = Game(players, rules=RuleConfig.tournament())
    view = RecordingView(_Spy(), tmp_path, preset="tournament")
    view.attach(game, target_score=game.rules.target_score)
    _play_to_the_end(game, view)
    path, events = _only_record(tmp_path)
    return game, path, events


@pytest.fixture
def stacked_belote_game(tmp_path):
    """One stacked round whose declarer holds the King and Queen of trump."""
    game = _stacked_game(BELOTE_HANDS)
    view = RecordingView(_Spy(), tmp_path)
    view.attach(game, target_score=game.rules.target_score)
    game.manage_round(view=view)
    view.on_round_complete(game.current_round, game.scores)
    view.close_record()
    path, events = _only_record(tmp_path)
    return game, path, events


@pytest.fixture
def all_pass_game(tmp_path):
    """One stacked round nobody opens the bidding on."""
    game = _stacked_game(ALL_PASS_HANDS)
    view = RecordingView(_Spy(), tmp_path)
    view.attach(game, target_score=game.rules.target_score)
    game.manage_round(view=view)
    view.on_round_complete(game.current_round, game.scores)
    view.close_record()
    path, events = _only_record(tmp_path)
    return game, path, events


def _of(events, kind):
    """Every event of one type, in file order."""
    return [event for event in events if isinstance(event, kind)]


def _seat_of(round_) -> dict[str, Position]:
    """Which compass seat each deal-order letter landed on.

    Args:
        round_: The round whose ``players_order`` was dealt to.

    Returns:
        ``{"N": …, "E": …, "S": …, "W": …}``, the letters being the
        order :func:`_stack_deck` stacked for.
    """
    return {
        letter: player.position
        for letter, player in zip(("N", "E", "S", "W"), round_.players_order)
    }


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


class TestForwarding:
    def test_unknown_attributes_reach_the_inner_view(self, tmp_path):
        spy = _Spy()
        wrapper = RecordingView(spy, tmp_path)

        assert wrapper.aids is spy.aids
        assert wrapper.calls is spy.calls

    def test_assignment_reaches_the_inner_view(self, tmp_path):
        spy = _Spy()
        wrapper = RecordingView(spy, tmp_path)

        wrapper.aids = TableAids(live_round_score=False)

        assert spy.aids == TableAids(live_round_score=False)
        assert "aids" not in wrapper.__dict__

    def test_record_prefixed_state_stays_on_the_wrapper(self, tmp_path):
        spy = _Spy()
        wrapper = RecordingView(spy, tmp_path)

        wrapper._record_round = 7

        assert wrapper.__dict__["_record_round"] == 7
        assert not hasattr(spy, "_record_round")

    def test_a_missing_attribute_still_raises(self, tmp_path):
        wrapper = RecordingView(_Deaf(), tmp_path)

        with pytest.raises(AttributeError):
            wrapper.no_such_thing

    def test_dunder_lookups_are_not_forwarded(self, tmp_path):
        wrapper = RecordingView(_Deaf(), tmp_path)

        with pytest.raises(AttributeError):
            wrapper.__deepcopy__

    def test_a_view_without_the_push_hooks_is_not_called(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        wrapper = RecordingView(_Deaf(), tmp_path)
        wrapper.attach(game, target_score=game.rules.target_score)
        game.start_new_round()

        # The inner view has no ``on_round_dealt``: forwarding must not
        # raise, and the event must be written all the same.
        wrapper.on_round_dealt(game.current_round)
        wrapper.close_record()

        _, events = _only_record(tmp_path)
        assert len(_of(events, RoundDealt)) == 1

    @pytest.mark.parametrize(
        "hook",
        [
            "attach",
            "on_round_dealt",
            "on_bid_made",
            "on_card_played",
            "on_belote_announced",
            "on_round_complete",
            "show_end_game",
        ],
    )
    def test_hasattr_is_true_for_every_intercepted_hook(self, tmp_path, hook):
        # The engine guards each hook with ``hasattr``; a wrapper that
        # answered False for one would drop that hook's events entirely.
        assert hasattr(RecordingView(_Deaf(), tmp_path), hook)

    @pytest.mark.parametrize(
        "hook", ["on_all_pass_redeal", "on_contract_established", "on_trick_complete"]
    )
    def test_the_derived_hooks_are_not_intercepted(self, tmp_path, hook):
        # Deliberately absent: each carries a fact the format re-derives.
        assert not hasattr(RecordingView(_Deaf(), tmp_path), hook)

    def test_the_spy_saw_the_whole_game(self, tmp_path):
        spy = _Spy()
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(spy, tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)
        view.show_end_game(game.check_game_over())

        seen = {call[0] for call in spy.calls}
        assert seen == {
            "attach",
            "on_round_dealt",
            "on_bid_made",
            "on_card_played",
            "on_belote_announced",
            "on_round_complete",
            "show_end_game",
        }

    def test_show_end_game_passes_the_inner_return_value_through(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)

        assert view.show_end_game(game.check_game_over()) == "q"


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


class TestEmission:
    """One seeded 4-AI game, recorded, then read back."""

    def test_header_and_game_started(self, recorded_game):
        game, path, events = recorded_game
        header = _of(events, Header)[0]
        started = _of(events, GameStarted)[0]

        assert events[0] is header
        assert events[1] is started
        assert header.format == FORMAT
        assert header.source is RecordSource.ENGINE
        assert header.generator.startswith("contrai-engine")
        assert header.game_id.startswith("engine-")
        assert path.stem == header.game_id
        assert started.ruleset.preset == "tournament"
        assert started.ruleset.config == game.rules
        assert set(started.seats) == set(Position)
        assert started.observed_from is None
        for seat in started.seats.values():
            assert seat.kind is SeatKind.AI
            assert seat.name == "ai:expert"
            assert seat.level == "expert"
            assert seat.id is None
            assert seat.account is None

    def test_a_human_seat_is_named_human(self, tmp_path):
        players = [
            HumanPlayer("You", position=Position.SOUTH)
            if seat is Position.SOUTH
            else AiPlayer(seat.value, position=seat)
            for seat in Position
        ]
        game = Game(players)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        view.close_record()

        _, events = _only_record(tmp_path)
        seats = _of(events, GameStarted)[0].seats
        assert seats[Position.SOUTH].kind is SeatKind.HUMAN
        assert seats[Position.SOUTH].name == "human"
        assert seats[Position.SOUTH].level is None
        # No display name ever reaches a record.
        assert "You" not in {seat.name for seat in seats.values()}

    def test_a_hand_mixed_ai_records_as_custom(self, tmp_path):
        class _OtherBidding:
            def __init__(self, player):
                self.player = player

        players = [AiPlayer(seat.value, position=seat) for seat in Position]
        players[0].bidding = _OtherBidding(players[0])
        game = Game(players)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        view.close_record()

        _, events = _only_record(tmp_path)
        seats = _of(events, GameStarted)[0].seats
        assert seats[players[0].position].level == "custom"
        assert seats[players[0].position].name == "ai:custom"

    def test_round_dealt_carries_the_real_deal(self, stacked_belote_game):
        game, _, events = stacked_belote_game
        (dealt,) = _of(events, RoundDealt)
        seat_of = _seat_of(game.current_round)

        assert dealt.round == 1
        assert dealt.hands_derivation is HandsDerivation.SELF_PLAY
        assert set(dealt.hands) == set(Position)
        assert dealt.dealer is game.dealer.position
        for letter, hand in BELOTE_HANDS.items():
            assert list(dealt.hands[seat_of[letter]]) == hand

    def test_the_dealer_is_the_round_s_own(self, recorded_game):
        game, _, events = recorded_game
        dealt = _of(events, RoundDealt)
        assert len(dealt) == game.round_number
        assert all(event.dealer in set(Position) for event in dealt)

    def test_bid_seq_is_1_based_and_gapless(self, recorded_game):
        _, _, events = recorded_game
        bids = _of(events, BidMade)
        assert bids

        by_round: dict[int, list[BidMade]] = {}
        for event in bids:
            by_round.setdefault(event.round, []).append(event)
        for round_bids in by_round.values():
            assert [e.seq for e in round_bids] == list(
                range(1, len(round_bids) + 1)
            )
            for event in round_bids:
                assert event.bid.player is event.position

    def test_trick_numbers_advance_every_fourth_card(self, recorded_game):
        _, _, events = recorded_game
        cards = _of(events, CardPlayed)
        assert cards

        by_round: dict[int, list[CardPlayed]] = {}
        for event in cards:
            by_round.setdefault(event.round, []).append(event)
        for round_cards in by_round.values():
            assert len(round_cards) == 32
            expected = [trick for trick in range(1, 9) for _ in range(4)]
            assert [e.trick for e in round_cards] == expected
            assert all(e.derived is False for e in round_cards)
            assert all(e.think_ms is None for e in round_cards)

    def test_one_belote_event_per_pair(self, stacked_belote_game):
        game, _, events = stacked_belote_game
        belotes = _of(events, BeloteHeld)

        # The hook fires twice per pair — Belote then Rebelote — and one
        # event per pair is what the format holds.
        assert len(belotes) == 1
        (held,) = belotes
        assert held.round == 1
        assert held.position is _seat_of(game.current_round)["N"]
        assert held.announced is True
        assert held.cards == (
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.SPADES, Rank.QUEEN),
        )

    def test_a_paid_pot_is_written_as_carried_over(self, tmp_path):
        # The stacked belote round is a made contract; hand it an open
        # dispute pot and its declaring side collects it on the record.
        game = _stacked_game(BELOTE_HANDS)
        game.dispute_pot = 161
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)
        view.close_record()
        _, events = _only_record(tmp_path)
        (scored,) = _of(events, RoundScored)
        side = game.current_round.contract.player.position.team_side
        assert scored.carried_over[side] == 161
        assert sum(scored.carried_over.values()) == 161

    def test_round_scored_mirrors_RoundScore(self, stacked_belote_game):
        game, _, events = stacked_belote_game
        (scored,) = _of(events, RoundScored)
        round_ = game.current_round
        score = round_.round_score

        assert scored.round == round_.round_number
        assert scored.outcome is RoundOutcome.MADE
        assert scored.declarer is round_.contract.player.position
        assert scored.contract.value == round_.contract.value
        assert scored.contract.suit == round_.contract.suit
        assert scored.contract.multiplier == score.multiplier
        assert scored.taken == score.card_points
        assert scored.belote == score.belote_points
        assert scored.totals == game.scores
        assert scored.last_trick is score.last_trick_side
        assert scored.source is ScoreSource.ENGINE
        assert scored.announcements == {side: 0 for side in TeamSide}
        assert scored.carried_over == {side: 0 for side in TeamSide}
        # The record states the figures a sheet carries, not the components
        # the scorer works in: a doubled round's are already multiplied.
        for side, mark in score.marks.items():
            made, announced = marked_components(
                mark, score.multiplier, round_.rules
            )
            assert scored.marked[side].made == made
            assert scored.marked[side].announced == announced

    def test_all_pass_round_scores_as_all_pass(self, all_pass_game):
        _, _, events = all_pass_game
        (scored,) = _of(events, RoundScored)

        assert scored.outcome is RoundOutcome.ALL_PASS
        assert scored.contract is None
        assert scored.declarer is None
        assert scored.slam is SlamOutcome.NONE
        assert scored.last_trick is None
        for mapping in (
            scored.taken,
            scored.belote,
            scored.announcements,
            scored.carried_over,
            scored.marked,
            scored.totals,
        ):
            assert set(mapping) == set(TeamSide)
        # The deal is on disk before the bidding starts, so an all-pass
        # still records the hands it redealt.
        assert len(_of(events, RoundDealt)) == 1
        assert not _of(events, CardPlayed)

    def test_an_ordinary_round_is_not_a_slam(self, stacked_belote_game):
        _, _, events = stacked_belote_game
        (scored,) = _of(events, RoundScored)
        assert scored.slam is SlamOutcome.NONE

    @pytest.mark.parametrize(
        "contract_kind, sweep, expected",
        [
            ("solo_slam", False, SlamOutcome.SOLO_SLAM),
            ("slam", False, SlamOutcome.SLAM),
            ("none", True, SlamOutcome.UNANNOUNCED),
            ("none", False, SlamOutcome.NONE),
        ],
    )
    def test_slam_outcome(self, contract_kind, sweep, expected):
        from contrai_engine.recording import _slam

        class _Contract:
            def __init__(self, kind):
                self._kind = kind

            def is_solo_slam(self):
                return self._kind == "solo_slam"

            def is_slam(self):
                return self._kind == "slam"

        class _Score:
            def __init__(self, sweep):
                self.unannounced_slam = object() if sweep else None

        contract = None if contract_kind == "none" else _Contract(contract_kind)
        assert _slam(contract, _Score(sweep)) is expected

    def test_a_bid_slam_outranks_a_swept_one(self):
        from contrai_engine.recording import _slam

        class _Contract:
            def is_solo_slam(self):
                return False

            def is_slam(self):
                return True

        class _Score:
            unannounced_slam = object()

        assert _slam(_Contract(), _Score()) is SlamOutcome.SLAM


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_attach_opens_a_new_file_per_game(self, tmp_path):
        view = RecordingView(_Spy(), tmp_path)
        first = _stacked_game(BELOTE_HANDS)
        second = _stacked_game(BELOTE_HANDS)

        view.attach(first, target_score=first.rules.target_score)
        view.attach(second, target_score=second.rules.target_score)
        view.close_record()

        files = sorted((tmp_path / "games").glob("*.jsonl"))
        assert len(files) == 2
        assert files[0].stem != files[1].stem

    def test_attach_closes_an_open_record_as_interrupted(self, tmp_path):
        view = RecordingView(_Spy(), tmp_path)
        first = _stacked_game(BELOTE_HANDS)
        view.attach(first, target_score=first.rules.target_score)
        first_path = view._record_writer.path

        second = _stacked_game(BELOTE_HANDS)
        view.attach(second, target_score=second.rules.target_score)
        view.close_record()

        ended = _of(read_events(first_path).events, GameEnded)
        assert [e.reason for e in ended] == [EndReason.INTERRUPTED]

    def test_show_end_game_closes_with_target_reached(self, recorded_game):
        game, _, events = recorded_game
        status = game.check_game_over()
        (ended,) = _of(events, GameEnded)

        assert events[-1] is ended
        assert ended.reason is EndReason.TARGET_REACHED
        assert ended.totals == status.final_scores
        assert ended.winner is status.winner

    def test_close_record_is_idempotent(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)

        view.close_record()
        view.close_record()

        _, events = _only_record(tmp_path)
        assert len(_of(events, GameEnded)) == 1

    def test_close_record_writes_interrupted_with_the_last_totals(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)

        view.close_record()

        _, events = _only_record(tmp_path)
        (ended,) = _of(events, GameEnded)
        assert ended.reason is EndReason.INTERRUPTED
        assert ended.totals == game.scores
        assert ended.winner is None

    def test_close_record_before_any_round_writes_null_totals(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)

        view.close_record()

        _, events = _only_record(tmp_path)
        (ended,) = _of(events, GameEnded)
        assert ended.totals is None
        assert ended.winner is None

    def test_hooks_before_attach_are_dropped_not_crashed(self, tmp_path):
        spy = _Spy()
        view = RecordingView(spy, tmp_path)
        game = _stacked_game(BELOTE_HANDS)
        game.start_new_round()

        view.on_round_dealt(game.current_round)
        view.on_round_complete(game.current_round, game.scores)

        assert not (tmp_path / "games").exists()
        # The inner view still saw both, recording or not.
        assert [call[0] for call in spy.calls] == [
            "on_round_dealt",
            "on_round_complete",
        ]

    def test_close_record_without_a_writer_does_nothing(self, tmp_path):
        view = RecordingView(_Spy(), tmp_path)

        view.close_record()

        assert not (tmp_path / "games").exists()

    def test_a_round_without_a_score_logs_and_writes_nothing(self, tmp_path, caplog):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        game.start_new_round()
        game.current_round.round_score = None

        with caplog.at_level("WARNING", logger="contrai_engine.recording"):
            view.on_round_complete(game.current_round, game.scores)
        view.close_record()

        _, events = _only_record(tmp_path)
        assert not _of(events, RoundScored)
        assert "without a score" in caplog.text

    def test_finish_recording_closes_a_wrapper(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)

        finish_recording(view)

        _, events = _only_record(tmp_path)
        assert _of(events, GameEnded)[0].reason is EndReason.INTERRUPTED

    def test_finish_recording_ignores_a_plain_view(self):
        finish_recording(_Spy())  # must not raise


# ---------------------------------------------------------------------------
# The flag/knob request
# ---------------------------------------------------------------------------


class TestRecordRequest:
    @pytest.mark.parametrize(
        "request_, knob, expected",
        [
            (RecordRequest(), False, None),
            (RecordRequest(), True, "default"),
            (RecordRequest(directory=None), False, "default"),
            (RecordRequest(directory=Path("out")), False, Path("out")),
            (RecordRequest(directory=Path("out")), True, Path("out")),
            (RecordRequest(disabled=True), True, None),
        ],
    )
    def test_flag_beats_knob_beats_off(
        self, request_, knob, expected, contrai_home
    ):
        resolved = request_.resolve(TableAids(record=knob))

        if expected == "default":
            assert resolved == contrai_home / "records"
        else:
            assert resolved == expected

    def test_the_absent_flag_is_the_default(self):
        assert RecordRequest().directory is UNSET
        assert RecordRequest().disabled is False

    def test_a_string_directory_becomes_a_path(self, tmp_path):
        request = RecordRequest(directory=str(tmp_path))

        assert request.resolve(TableAids()) == tmp_path


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_a_recorded_game_loads_and_projects(self, recorded_game):
        game, path, _ = recorded_game
        record = load_game(path)

        assert record.complete
        assert not record.truncated
        assert record.preset == "tournament"
        assert record.ruleset == game.rules
        assert len(record.rounds) == game.round_number
        assert all(round_.complete for round_ in record.rounds)
        assert record.ended.reason is EndReason.TARGET_REACHED

    def test_the_projected_trick_winners_match_the_live_round(self, tmp_path):
        game = _stacked_game(BELOTE_HANDS)
        view = RecordingView(_Spy(), tmp_path)
        view.attach(game, target_score=game.rules.target_score)
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)
        view.close_record()

        path, _ = _only_record(tmp_path)
        (projected,) = load_game(path).rounds
        live = [
            winner.position
            for winner in game.current_round.play_state.trick_winners
        ]
        assert list(projected.trick_winners) == live

    def test_autoplay_with_record_writes_a_loadable_game(
        self, tmp_path, monkeypatch
    ):
        """``contrai --autoplay --seed N --record DIR`` -> one projectable file.

        The §5.6 gate: the real parser, the real ``RichView``, the real
        ``Game`` and the wrapper all compose, and what lands on disk is a
        record the projection folds back into rounds.
        """
        monkeypatch.setenv("CONTRAI_HOME", str(tmp_path / "home"))
        for var in (
            "CONTRAI_AUTOPLAY_PAUSE",
            "CONTRAI_AUTOPLAY_RECAP_PAUSE",
            "CONTRAI_AUTOPLAY_LANDING_PAUSE",
            "CONTRAI_AUTOPLAY_ENDGAME_PAUSE",
            "CONTRAI_AI_CARD_DELAY",
            "CONTRAI_AI_BID_DELAY",
        ):
            monkeypatch.setenv(var, "0")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "contrai",
                "--autoplay",
                "--seed",
                "7",
                "--record",
                str(tmp_path / "rec"),
            ],
        )

        main()

        (path,) = (tmp_path / "rec" / "games").glob("*.jsonl")
        record = load_game(path)
        assert record.complete
        assert record.ended.reason is EndReason.TARGET_REACHED
        assert record.rounds and all(r.complete for r in record.rounds)
        assert all(seat.kind is SeatKind.AI for seat in record.seats.values())

    def test_a_run_with_no_flags_records_nothing(self, tmp_path, monkeypatch):
        """The default is off: a plain run must leave the corpus alone."""
        home = tmp_path / "home"
        monkeypatch.setenv("CONTRAI_HOME", str(home))
        for var in (
            "CONTRAI_AUTOPLAY_PAUSE",
            "CONTRAI_AUTOPLAY_RECAP_PAUSE",
            "CONTRAI_AUTOPLAY_LANDING_PAUSE",
            "CONTRAI_AUTOPLAY_ENDGAME_PAUSE",
            "CONTRAI_AI_CARD_DELAY",
            "CONTRAI_AI_BID_DELAY",
        ):
            monkeypatch.setenv(var, "0")
        monkeypatch.setattr(
            sys, "argv", ["contrai", "--autoplay", "--seed", "7"]
        )

        main()

        assert not (home / "records").exists()


class TestOutcome:
    @pytest.mark.parametrize(
        "made, held, expected",
        [
            (None, 0, RoundOutcome.ALL_PASS),
            (True, 0, RoundOutcome.MADE),
            (False, 0, RoundOutcome.FAILED),
            (True, 161, RoundOutcome.HELD),
        ],
    )
    def test_a_score_translates_to_its_outcome(self, made, held, expected):
        from contrai_engine.model.round import RoundScore
        from contrai_engine.recording import _outcome

        score = RoundScore(
            scores={}, contract_made=made, unannounced_slam=None, marks={},
            belote_points={}, card_points={}, last_trick_side=None,
            multiplier=1, held=held,
        )
        assert _outcome(score) is expected
