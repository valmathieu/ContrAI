"""Tests for the :class:`PlayState` play-phase state machine.

Covers the parts of the state machine beyond the legality oracle: seeding
validation, turn rotation (within a trick and the winner-leads-next-trick
rule across a boundary), the derived trick views, immutability and card
identity through :meth:`PlayState.apply`, terminal behaviour, the
out-of-turn / card-not-in-hand rejections, the :meth:`PlayState.with_hands`
determinization fork, and the no-trump degrade to plain follow-suit.
"""

from __future__ import annotations

import pytest

from contrai_core import (
    BasePlayer,
    Card,
    Contract,
    IllegalPlayError,
    PlayRuleViolation,
    PlayState,
    Rank,
    RuleConfig,
    Suit,
    TeamSide,
    TrickRecord,
    TrumpVariant,
)
from contrai_core.bid import ContractBid
from contrai_core.play import Play

# Seat → suit assignment for the deterministic full deal: each player is
# dealt one whole suit, so who wins a trick is easy to reason about.
_SEAT_SUITS = {
    "N": Suit.SPADES,
    "E": Suit.HEARTS,
    "S": Suit.DIAMONDS,
    "W": Suit.CLUBS,
}
_ORDER = ("N", "E", "S", "W")


def _deal(
    players_dict: dict[str, BasePlayer], trump: Suit = Suit.HEARTS
) -> tuple[Contract, tuple[BasePlayer, ...], tuple[tuple[Card, ...], ...], dict[str, list[Card]]]:
    """Build a valid full deal: 4 players, one whole suit each, a contract.

    Args:
        players_dict: the ``players`` fixture mapping.
        trump: the contract's trump suit (``HEARTS`` by default, so the
            heart-holder East wins every trick it can ruff).

    Returns:
        ``(contract, players, hands, by_seat)`` where ``players`` and
        ``hands`` are the parallel tuples :meth:`PlayState.start` expects and
        ``by_seat`` keeps the per-seat card lists for building plays.
    """
    ranks = list(Rank)
    by_seat = {
        seat: [Card(suit, r) for r in ranks] for seat, suit in _SEAT_SUITS.items()
    }
    players = tuple(players_dict[s] for s in _ORDER)
    hands = tuple(tuple(by_seat[s]) for s in _ORDER)
    contract = Contract(ContractBid(players_dict["N"], 100, trump))
    return contract, players, hands, by_seat


def _full_plays(players_dict: dict[str, BasePlayer]) -> tuple[Play, ...]:
    """Return the 32 plays of a whole game in N/E/S/W-per-trick order."""
    ranks = list(Rank)
    plays: list[Play] = []
    for i in range(8):
        for seat in _ORDER:
            plays.append(
                Play(players_dict[seat], Card(_SEAT_SUITS[seat], ranks[i]))
            )
    return tuple(plays)


def _play_first_trick(players_dict: dict[str, BasePlayer]) -> PlayState:
    """Play out trick 0 legally and return the resulting state.

    North leads a spade; East (void in spades) ruffs with the trump heart
    and wins; South and West discard. Afterwards East is to act (the winner
    leads the next trick) and every seat has 7 cards left.
    """
    contract, players, hands, by_seat = _deal(players_dict)
    state = PlayState.start(contract, players, hands)
    for seat in _ORDER:
        state = state.apply(Play(players_dict[seat], by_seat[seat][0]))
    return state


# ---------------------------------------------------------------------------
# start() seeding validation
# ---------------------------------------------------------------------------


class TestStartValidation:
    def test_wrong_player_count_raises(self, players):
        contract, seating, hands, _ = _deal(players)
        with pytest.raises(ValueError):
            PlayState.start(contract, seating[:3], hands[:3])

    def test_wrong_hand_size_raises(self, players):
        contract, seating, hands, _ = _deal(players)
        # Drop one card from North's hand → a 7-card hand.
        bad_hands = (hands[0][:-1],) + hands[1:]
        with pytest.raises(ValueError):
            PlayState.start(contract, seating, bad_hands)

    def test_duplicate_cards_raise(self, players):
        contract, seating, hands, _ = _deal(players)
        # Replace West's first club with a card North already holds — 8 cards
        # per seat still, but only 31 distinct cards overall.
        dup = Card(Suit.SPADES, Rank.SEVEN)
        bad_west = (dup,) + hands[3][1:]
        bad_hands = hands[:3] + (bad_west,)
        with pytest.raises(ValueError):
            PlayState.start(contract, seating, bad_hands)

    def test_valid_seed_round_trips_fields(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        assert state.contract is contract
        assert state.players == seating
        assert state.hands == hands
        assert state.plays == ()


# ---------------------------------------------------------------------------
# to_act rotation
# ---------------------------------------------------------------------------


class TestToActRotation:
    def test_rotation_within_trick_and_winner_leads_next(self, players):
        contract, seating, hands, by_seat = _deal(players)
        north, east, south, west = seating
        state = PlayState.start(contract, seating, hands)

        assert state.to_act is north
        state = state.apply(Play(north, by_seat["N"][0]))
        assert state.to_act is east
        state = state.apply(Play(east, by_seat["E"][0]))
        assert state.to_act is south
        state = state.apply(Play(south, by_seat["S"][0]))
        assert state.to_act is west
        state = state.apply(Play(west, by_seat["W"][0]))
        # Trick 0 is complete; East ruffed and won, so East leads trick 1.
        assert state.trick_winners == (east,)
        assert state.to_act is east


# ---------------------------------------------------------------------------
# apply() immutability + card identity
# ---------------------------------------------------------------------------


class TestApplyImmutabilityAndIdentity:
    def test_original_unchanged_and_card_removed(self, players):
        contract, seating, hands, by_seat = _deal(players)
        north = seating[0]
        state = PlayState.start(contract, seating, hands)
        played = by_seat["N"][0]

        new_state = state.apply(Play(north, played))

        # Original is untouched.
        assert state.plays == ()
        assert len(state.hand_of(north)) == 8
        assert played in state.hand_of(north)
        # New state has the play appended and the card removed.
        assert new_state.plays == (Play(north, played),)
        assert len(new_state.hand_of(north)) == 7
        assert played not in new_state.hand_of(north)

    def test_card_identity_preserved(self, players):
        contract, seating, hands, by_seat = _deal(players)
        north = seating[0]
        state = PlayState.start(contract, seating, hands)

        # legal_actions hands back the exact objects seeded in.
        legal = state.legal_actions(north)
        for seeded, offered in zip(by_seat["N"], legal):
            assert offered is seeded

        played = by_seat["N"][0]
        new_state = state.apply(Play(north, played))
        # The remaining cards in the new state are the same objects, in order.
        for seeded, remaining in zip(by_seat["N"][1:], new_state.hand_of(north)):
            assert remaining is seeded


# ---------------------------------------------------------------------------
# Terminal behaviour
# ---------------------------------------------------------------------------


class TestTerminal:
    def test_terminal_state_blocks_further_play(self, players):
        contract, seating, _, _ = _deal(players)
        state = PlayState(
            contract=contract,
            players=seating,
            hands=((), (), (), ()),
            plays=_full_plays(players),
        )
        assert state.is_terminal()
        assert state.to_act is None

        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(seating[0], Card(Suit.SPADES, Rank.SEVEN)))
        assert excinfo.value.reason == PlayRuleViolation.OUT_OF_TURN


# ---------------------------------------------------------------------------
# Boundary derived properties
# ---------------------------------------------------------------------------


class TestDerivedPropertyBoundaries:
    def _state(self, players, n_plays):
        contract, seating, _, _ = _deal(players)
        all_plays = _full_plays(players)
        # Seats keep whatever cards are not yet played (irrelevant here — the
        # derived views only read ``plays``), so empty hands are fine.
        return PlayState(
            contract=contract,
            players=seating,
            hands=((), (), (), ()),
            plays=all_plays[:n_plays],
        )

    def test_at_start(self, players):
        state = self._state(players, 0)
        assert state.trick_number == 0
        assert state.current_trick == ()
        assert state.completed_tricks == ()
        assert state.trick_winners == ()

    def test_mid_trick(self, players):
        state = self._state(players, 2)
        assert state.trick_number == 0
        assert len(state.current_trick) == 2
        assert state.completed_tricks == ()
        assert state.trick_winners == ()

    def test_exactly_at_trick_boundary(self, players):
        state = self._state(players, 4)
        assert state.trick_number == 1
        assert state.current_trick == ()
        assert len(state.completed_tricks) == 1
        assert len(state.completed_tricks[0]) == 4
        # Completed tricks are typed TrickRecord values (still tuples).
        assert isinstance(state.completed_tricks[0], TrickRecord)
        assert len(state.trick_winners) == 1

    def test_terminal_boundary(self, players):
        state = self._state(players, 32)
        assert state.trick_number == 8
        assert state.current_trick == ()
        assert len(state.completed_tricks) == 8
        assert all(len(trick) == 4 for trick in state.completed_tricks)
        assert len(state.trick_winners) == 8


# ---------------------------------------------------------------------------
# Per-side captured pile: card points and trick counts
# ---------------------------------------------------------------------------


# A whole round laid out so both sides capture tricks, each trick played
# entirely in one suit (two tricks per suit) so the winner is decided by
# the in-suit ladder alone. Read as (seat, suit, rank) in play order; the
# trick winners under a ♥ contract are N, E, S, W, N, E, N, E.
_SPLIT_ROUND: tuple[tuple[str, Suit, Rank], ...] = (
    # ♠ — N takes the ace trick, E the ten trick (the 10 outranks the K
    # on the plain ladder).
    ("N", Suit.SPADES, Rank.ACE), ("E", Suit.SPADES, Rank.KING),
    ("S", Suit.SPADES, Rank.QUEEN), ("W", Suit.SPADES, Rank.JACK),
    ("E", Suit.SPADES, Rank.TEN), ("S", Suit.SPADES, Rank.NINE),
    ("W", Suit.SPADES, Rank.EIGHT), ("N", Suit.SPADES, Rank.SEVEN),
    # ♦ — same shape, one seat further round the table.
    ("S", Suit.DIAMONDS, Rank.ACE), ("W", Suit.DIAMONDS, Rank.KING),
    ("N", Suit.DIAMONDS, Rank.QUEEN), ("E", Suit.DIAMONDS, Rank.JACK),
    ("W", Suit.DIAMONDS, Rank.TEN), ("N", Suit.DIAMONDS, Rank.NINE),
    ("E", Suit.DIAMONDS, Rank.EIGHT), ("S", Suit.DIAMONDS, Rank.SEVEN),
    # ♣ — as for ♠.
    ("N", Suit.CLUBS, Rank.ACE), ("E", Suit.CLUBS, Rank.KING),
    ("S", Suit.CLUBS, Rank.QUEEN), ("W", Suit.CLUBS, Rank.JACK),
    ("E", Suit.CLUBS, Rank.TEN), ("S", Suit.CLUBS, Rank.NINE),
    ("W", Suit.CLUBS, Rank.EIGHT), ("N", Suit.CLUBS, Rank.SEVEN),
    # ♥ — the suit whose scale changes with the regime: under a ♥
    # contract the J and 9 lead the ladder and are worth 20 and 14;
    # under NO_TRUMP they fall back to 2 and 0 behind the ace.
    ("N", Suit.HEARTS, Rank.JACK), ("E", Suit.HEARTS, Rank.NINE),
    ("S", Suit.HEARTS, Rank.ACE), ("W", Suit.HEARTS, Rank.TEN),
    ("E", Suit.HEARTS, Rank.KING), ("S", Suit.HEARTS, Rank.QUEEN),
    ("W", Suit.HEARTS, Rank.EIGHT), ("N", Suit.HEARTS, Rank.SEVEN),
)


class TestCapturedPileDerivations:
    """``card_points_by_side`` / ``trick_counts_by_side``.

    Both credit each completed trick to the side of the seat that won
    it, so they are exercised together against the same states.
    """

    def _state(self, players, plays, trump=Suit.HEARTS, count=None):
        """A bare state over ``plays`` (hands are irrelevant to derived views)."""
        contract = Contract(ContractBid(players["N"], 100, trump))
        seating = tuple(players[s] for s in _ORDER)
        records = tuple(
            Play(players[seat], Card(suit, rank))
            for seat, suit, rank in plays[:count]
        )
        return PlayState(
            contract=contract, players=seating, hands=((), (), (), ()),
            plays=records,
        )

    def test_empty_state_has_every_side_at_zero(self, players):
        """No plays yet — both keys present, both zero, no ``.get`` needed."""
        state = self._state(players, ())
        assert state.card_points_by_side == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert state.trick_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_in_progress_trick_is_not_credited(self, players):
        """One completed ♠ trick to N-S, plus two plays nobody has won yet."""
        state = self._state(players, _SPLIT_ROUND, count=6)
        # A♠ 11 + K♠ 4 + Q♠ 3 + J♠ 2 = 20, all to North's side.
        assert state.card_points_by_side == {TeamSide.NS: 20, TeamSide.EW: 0}
        assert state.trick_counts_by_side == {TeamSide.NS: 1, TeamSide.EW: 0}

    def test_completed_round_splits_the_whole_deck(self, players):
        """The two sides' piles sum to the deck's 152 card points.

        152, not 162: the last-trick bonus is a contract-conversion
        rule the engine adds, not a fact about which cards were
        captured.
        """
        state = self._state(players, _SPLIT_ROUND)
        points = state.card_points_by_side
        # ♠A-trick 20 + ♦A-trick 20 + ♣A-trick 20 + ♥J-trick 55 = 115.
        assert points == {TeamSide.NS: 115, TeamSide.EW: 37}
        assert sum(points.values()) == 152
        counts = state.trick_counts_by_side
        assert counts == {TeamSide.NS: 4, TeamSide.EW: 4}
        assert sum(counts.values()) == state.trick_number == 8

    def test_no_trump_regime_rescales_the_pile(self, players):
        """The same 32 plays, scored on the no-trump scale.

        Two things move relative to a ♥ contract. The ♥ J and 9 lose
        their trump values (20 → 2, 14 → 0) and stop outranking the
        ace, so the ♥ ace trick is won by South rather than North —
        still N-S, but a different seat. And every ace is worth 19
        instead of 11, which is what keeps the deck at 152: four suits
        of 38 rather than four of 30.

        The split happens to land on the same 115 / 37 as the ♥
        contract above. That is arithmetic coincidence, not a weak
        assertion — every trick falls to the same *side* under both
        regimes, and both decks are worth 152 — so the winner check
        below is what actually pins the regime difference.
        """
        state = self._state(players, _SPLIT_ROUND, trump=TrumpVariant.NO_TRUMP)
        points = state.card_points_by_side
        # Three 28-point ace tricks (19+4+3+2) to N-S, three 10-point
        # ten tricks to E-W, the ♥ ace trick (2+0+19+10 = 31) to N-S
        # and the ♥ king trick (4+3 = 7) to E-W.
        assert points == {TeamSide.NS: 115, TeamSide.EW: 37}
        assert sum(points.values()) == 152
        # The trick split is unchanged; only the scale and one winner moved.
        assert state.trick_counts_by_side == {TeamSide.NS: 4, TeamSide.EW: 4}
        # The ♥ ace trick (index 6) goes to South here, to North under a
        # ♥ contract — the ordering difference the regime actually makes.
        assert state.trick_winners[6] is players["S"]

    def test_one_sided_sweep(self, players):
        """East ruffs every trick: E-W takes all 8 and the whole 152."""
        contract, seating, _, _ = _deal(players)
        state = PlayState(
            contract=contract, players=seating, hands=((), (), (), ()),
            plays=_full_plays(players),
        )
        assert state.card_points_by_side == {TeamSide.NS: 0, TeamSide.EW: 152}
        assert state.trick_counts_by_side == {TeamSide.NS: 0, TeamSide.EW: 8}

    @pytest.mark.parametrize("trump", [*Suit, TrumpVariant.NO_TRUMP])
    def test_every_regime_splits_the_same_152(self, players, trump):
        """A played-out round distributes 152 card points in every mode.

        The scales differ — a suit contract has one 62-point trump suit
        and three 30-point plain ones, no trump four of 38 — but the deck
        total is the invariant that lets one bidding ladder mean the same
        thing under every contract (contree-domain.md §3.5). A regime
        whose table misses 152 puts the upper contract values out of
        reach, which is exactly how the no-trump defect surfaced.

        Parametrized over every *implemented* ``ContractSuit``:
        ``ContractBid.VALID_SUITS`` excludes all-trump, so the list here
        is the four suits plus no trump.
        """
        state = self._state(players, _SPLIT_ROUND, trump=trump)
        assert state.trick_number == 8
        assert sum(state.card_points_by_side.values()) == 152


# ---------------------------------------------------------------------------
# Out-of-turn and card-not-in-hand rejections
# ---------------------------------------------------------------------------


class TestRejections:
    def test_out_of_turn_mid_trick(self, players):
        contract, seating, hands, by_seat = _deal(players)
        _, east, _, _ = seating
        state = PlayState.start(contract, seating, hands)
        # North leads; East acting first is out of turn.
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(east, by_seat["E"][0]))
        assert excinfo.value.reason == PlayRuleViolation.OUT_OF_TURN

    def test_card_not_in_hand(self, players):
        contract, seating, hands, _ = _deal(players)
        north = seating[0]
        state = PlayState.start(contract, seating, hands)
        # North holds only spades; a heart is not in hand.
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(north, Card(Suit.HEARTS, Rank.ACE)))
        assert excinfo.value.reason == PlayRuleViolation.CARD_NOT_IN_HAND

    def test_already_played_card_not_in_hand(self, players):
        state = _play_first_trick(players)
        east = state.to_act  # winner of trick 0 leads trick 1
        # East already played its ♥7 in trick 0 — it is gone from the hand.
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(east, Card(Suit.HEARTS, Rank.SEVEN)))
        assert excinfo.value.reason == PlayRuleViolation.CARD_NOT_IN_HAND

    def test_errors_name_the_offending_seat(self, players):
        """Every ``apply`` rejection carries a context naming the seat.

        The three raise sites (out of turn, card not held, obligation
        violation) each attach ``"<position> card play"`` so diagnostics
        immediately say who misplayed, and the message is prefixed with it.
        """
        contract, seating, hands, by_seat = _deal(players)
        north, east = seating[0], seating[1]
        state = PlayState.start(contract, seating, hands)

        # Out of turn: East acts while North is to act.
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(east, by_seat["E"][0]))
        assert excinfo.value.context == "East card play"
        assert str(excinfo.value).startswith("East card play: ")

        # Card not in hand: North holds only spades, tries a heart.
        with pytest.raises(IllegalPlayError) as excinfo:
            state.apply(Play(north, Card(Suit.HEARTS, Rank.ACE)))
        assert excinfo.value.context == "North card play"

        # Obligation violation: East holds the led suit but tries to ruff.
        # Bare-constructor mini-state — hearts are trump (from ``_deal``).
        mini = PlayState(
            contract,
            seating,
            (
                (Card(Suit.SPADES, Rank.ACE),),
                (Card(Suit.SPADES, Rank.KING), Card(Suit.HEARTS, Rank.ACE)),
                (Card(Suit.CLUBS, Rank.ACE),),
                (Card(Suit.CLUBS, Rank.KING),),
            ),
        )
        led = mini.apply(Play(north, mini.hand_of(north)[0]))
        with pytest.raises(IllegalPlayError) as excinfo:
            led.apply(Play(east, led.hand_of(east)[1]))
        assert excinfo.value.reason == PlayRuleViolation.MUST_FOLLOW_SUIT
        assert excinfo.value.context == "East card play"


# ---------------------------------------------------------------------------
# with_hands determinization fork
# ---------------------------------------------------------------------------


class TestWithHands:
    def test_valid_fork_preserves_contract_and_plays(self, players):
        state = _play_first_trick(players)
        # Swap North's and East's remaining hands (7 cards each, none of
        # which appears in the play history) — a legal determinization.
        old = state.hands
        swapped = (old[1], old[0], old[2], old[3])
        forked = state.with_hands(swapped)

        assert forked.contract is state.contract
        assert forked.plays == state.plays
        assert forked.hands == swapped
        # The source state is untouched.
        assert state.hands == old

    def test_size_mismatch_raises(self, players):
        state = _play_first_trick(players)
        old = state.hands
        # North gets a 6-card hand where 7 are expected.
        bad = (old[0][:-1],) + old[1:]
        with pytest.raises(ValueError):
            state.with_hands(bad)

    def test_history_duplicate_raises(self, players):
        state = _play_first_trick(players)
        old = state.hands
        # Slot a card that was already played back into North's hand.
        played_card = state.plays[0].card
        bad_north = (played_card,) + old[0][1:]
        bad = (bad_north,) + old[1:]
        with pytest.raises(ValueError):
            state.with_hands(bad)


# ---------------------------------------------------------------------------
# NO_TRUMP contracts degrade to plain follow-suit
# ---------------------------------------------------------------------------


class TestNoTrumpDegrade:
    def _no_trump_state(self, players, south_hand, plays):
        contract = Contract(ContractBid(players["N"], 100, TrumpVariant.NO_TRUMP))
        seating = tuple(players[s] for s in _ORDER)
        hands = tuple(
            tuple(south_hand) if s == "S" else () for s in _ORDER
        )
        play_tuples = tuple(Play(players[s], card) for s, card in plays)
        return PlayState(
            contract=contract, players=seating, hands=hands, plays=play_tuples
        )

    def test_void_player_discards_freely(self, players):
        """Lead ♥K; South is void in hearts. Under NO_TRUMP there is no trump
        obligation, so every card is a legal discard — even the spades that a
        suit contract would force South to ruff with."""
        south_hand = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
        ]
        state = self._no_trump_state(
            players, south_hand, [("N", Card(Suit.HEARTS, Rank.KING))]
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == set(south_hand)

    def test_follow_suit_still_enforced(self, players):
        """Lead ♥K; South holds hearts. NO_TRUMP still requires following the
        led suit — only the hearts are legal."""
        south_hand = [
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.CLUBS, Rank.ACE),
        ]
        state = self._no_trump_state(
            players, south_hand, [("N", Card(Suit.HEARTS, Rank.KING))]
        )
        legal = state.legal_actions(players["S"])
        assert set(legal) == {
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
        }


# ---------------------------------------------------------------------------
# The table ruleset rides along on the state
# ---------------------------------------------------------------------------


class TestRulesField:
    def test_default_is_the_classic_config(self, players):
        contract, seating, hands, _ = _deal(players)
        assert PlayState.start(contract, seating, hands).rules == RuleConfig()
        assert PlayState(contract, seating, hands).rules == RuleConfig()

    def test_start_records_an_explicit_config(self, players):
        contract, seating, hands, _ = _deal(players)
        rules = RuleConfig(target_score=1000)
        assert PlayState.start(contract, seating, hands, rules=rules).rules is rules

    def test_apply_and_with_hands_propagate_rules(self, players):
        contract, seating, hands, by_seat = _deal(players)
        rules = RuleConfig(target_score=1000)
        state = PlayState.start(contract, seating, hands, rules=rules)
        after = state.apply(Play(seating[0], by_seat["N"][0]))
        assert after.rules is rules
        assert after.with_hands(after.hands).rules is rules

    def test_rules_do_not_change_legality_yet(self, players):
        # Step-1 contract: carried, not consulted.
        contract, seating, hands, _ = _deal(players)
        plain = PlayState.start(contract, seating, hands)
        other = PlayState.start(contract, seating, hands,
                                rules=RuleConfig(under_trump_exemption=False))
        for p in seating:
            assert plain.legal_actions(p) == other.legal_actions(p)
