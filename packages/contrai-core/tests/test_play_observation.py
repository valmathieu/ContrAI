"""Tests for :class:`PlayObservation`, the play-phase imperfect-information view.

``PlayState.observe`` projects the full state — which holds every seat's
hand — down to what a single player is allowed to see. These tests pin:
the field set (own hand only, nothing leaking another seat's cards), that
``legal_cards`` matches :meth:`PlayState.legal_actions` by object identity,
that ``bids`` arrives complete but sealed onto seats, the five derived
properties, that the observation is immutable, and that every person the
observation names is named by ``Position`` — the trick records as
``ObservedPlay`` ``(position, card)`` pairs, the contract as an
``ObservedContract``.

``TestNothingLiveIsReachable`` states the whole guarantee as one
property: nothing reachable from an observation by *any* object path is
a live ``BasePlayer``, ``Team`` or ``Hand``. That is the seal itself,
independent of which fields happen to exist today.

The shared ``players`` fixture lives in ``conftest.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from contrai_core import (
    AllTrumpBelote,
    BasePlayer,
    Card,
    Contract,
    Hand,
    ObservedContract,
    PlayState,
    Position,
    Rank,
    RuleConfig,
    Suit,
    Team,
    TrickRecord,
    TrumpVariant,
)
from contrai_core.bid import ContractBid, DoubleBid, PassBid, RedoubleBid
from contrai_core.play import ObservedPlay, Play, PlayObservation

# Seat → suit assignment for a deterministic full deal: each player holds
# one whole suit, so cross-seat leakage is trivial to detect and trick
# winners are easy to reason about.
_SEAT_SUITS = {
    "N": Suit.SPADES,
    "E": Suit.HEARTS,
    "S": Suit.DIAMONDS,
    "W": Suit.CLUBS,
}
_ORDER = ("N", "E", "S", "W")

_EXPECTED_FIELDS = {
    "position",
    "hand",
    "contract",
    "bids",
    "completed_tricks",
    "current_trick",
    "legal_cards",
    "rules",
}


def _deal(
    players_dict: dict[str, BasePlayer], trump: Suit = Suit.HEARTS
) -> tuple[
    Contract, tuple[BasePlayer, ...], tuple[tuple[Card, ...], ...], dict[str, list[Card]]
]:
    """Build a valid full deal: 4 players, one whole suit each, a contract."""
    ranks = list(Rank)
    by_seat = {
        seat: [Card(suit, r) for r in ranks] for seat, suit in _SEAT_SUITS.items()
    }
    seating = tuple(players_dict[s] for s in _ORDER)
    hands = tuple(tuple(by_seat[s]) for s in _ORDER)
    contract = Contract(ContractBid(players_dict["N"], 100, trump))
    return contract, seating, hands, by_seat


def _play_first_trick(players_dict: dict[str, BasePlayer]) -> PlayState:
    """Play out trick 0 legally: North leads a spade, East ruffs and wins.

    East is void in spades, so its trump heart wins the trick — a
    trump-beats-lead scenario for the ``current_winner`` tests.
    """
    contract, seating, hands, by_seat = _deal(players_dict)
    state = PlayState.start(contract, seating, hands)
    for seat in _ORDER:
        state = state.apply(Play(players_dict[seat], by_seat[seat][0]))
    return state


# ---------------------------------------------------------------------------
# Own-hand-only
# ---------------------------------------------------------------------------


class TestOwnHandOnly:
    def test_field_set_is_exactly_the_eight_specified(self):
        assert set(PlayObservation.__dataclass_fields__) == _EXPECTED_FIELDS

    def test_observer_is_named_by_seat(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)

        for seat in _ORDER:
            obs = state.observe(players[seat])
            assert obs.position is players[seat].position

    def test_hand_matches_hand_of_and_leaks_no_other_seat(self, players):
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)

        for seat in _ORDER:
            player = players[seat]
            obs = state.observe(player)
            assert obs.hand == state.hand_of(player)

            other_cards: set[Card] = set()
            for other_seat in _ORDER:
                if other_seat != seat:
                    other_cards.update(by_seat[other_seat])
            assert not (set(obs.hand) & other_cards)


# ---------------------------------------------------------------------------
# legal_cards parity
# ---------------------------------------------------------------------------


class TestLegalCardsParity:
    def test_matches_legal_actions_by_identity_in_forced_follow(self, players):
        """North leads a heart; South holds two hearts and a club — South
        must follow suit, so only the two hearts are legal."""
        contract = Contract(ContractBid(players["N"], 100, Suit.SPADES))
        hand = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.CLUBS, Rank.KING),
        ]
        state = PlayState(
            contract=contract,
            players=tuple(players[s] for s in _ORDER),
            hands=tuple(tuple(hand) if s == "S" else () for s in _ORDER),
            plays=(Play(players["N"], Card(Suit.HEARTS, Rank.KING)),),
        )

        expected = state.legal_actions(players["S"])
        obs = state.observe(players["S"])

        assert obs.legal_cards == expected
        assert len(obs.legal_cards) == 2
        for offered, wanted in zip(obs.legal_cards, expected):
            assert offered is wanted


# ---------------------------------------------------------------------------
# bids passthrough
# ---------------------------------------------------------------------------


class TestBidsPassthrough:
    """The auction arrives complete, but sealed onto seats.

    A bid's actor is excluded from its equality, so the sealed history
    still compares equal to the live one it was built from — the
    passthrough assertions read exactly as they did before the seal.
    """

    def test_default_is_empty_tuple(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.bids == ()

    def test_sequence_arrives_as_equal_tuple(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        bids = [PassBid(players["E"]), ContractBid(players["N"], 100, Suit.HEARTS)]

        obs = state.observe(players["N"], bids=bids)

        assert obs.bids == tuple(bids)
        assert isinstance(obs.bids, tuple)

    def test_variants_and_payloads_survive_the_seal(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        bids = [PassBid(players["E"]), ContractBid(players["N"], 100, Suit.HEARTS)]

        obs = state.observe(players["N"], bids=bids)

        assert isinstance(obs.bids[0], PassBid)
        assert isinstance(obs.bids[1], ContractBid)
        assert obs.bids[1].value == 100
        assert obs.bids[1].suit is Suit.HEARTS

    def test_bidders_arrive_as_positions(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        bids = [PassBid(players["E"]), ContractBid(players["N"], 100, Suit.HEARTS)]

        obs = state.observe(players["N"], bids=bids)

        assert [bid.player for bid in obs.bids] == [
            Position.EAST,
            Position.NORTH,
        ]


# ---------------------------------------------------------------------------
# rules passthrough
# ---------------------------------------------------------------------------


class TestRulesPassthrough:
    """The table ruleset reaches every seat's view.

    House rules are public information — the table agreed them before the
    first deal — so passing them through widens nothing. It is what lets a
    strategy reason about the regime it plays under instead of inferring
    table policy from the shape of its legal set.
    """

    def test_every_seat_sees_the_states_own_ruleset(self, players):
        contract, seating, hands, _ = _deal(players)
        rules = RuleConfig(
            all_trump_belote=AllTrumpBelote.FOUR, under_trump_exemption=False
        )
        state = PlayState.start(contract, seating, hands, rules=rules)

        for seat in _ORDER:
            # ``RuleConfig`` is frozen, so the projection may share it —
            # identity is the strongest statement of "the same ruleset".
            assert state.observe(players[seat]).rules is rules

    def test_default_state_hands_out_the_catalogue_defaults(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        assert state.observe(players["N"]).rules == RuleConfig()

    def test_directly_built_observation_defaults_to_the_catalogue(self, players):
        obs = PlayObservation(
            position=Position.NORTH,
            hand=(),
            contract=None,
            bids=(),
            completed_tricks=(),
            current_trick=(),
            legal_cards=(),
        )
        assert obs.rules == RuleConfig()


# ---------------------------------------------------------------------------
# Derived properties
# ---------------------------------------------------------------------------


class TestDerivedProperties:
    def test_trick_number_matches_play_state_semantics(self, players):
        state = _play_first_trick(players)
        obs = state.observe(state.to_act)
        assert obs.trick_number == state.trick_number == 1

    def test_trick_number_at_start_is_zero(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trick_number == 0

    def test_trump_suit_reflects_contract_suit(self, players):
        contract, seating, hands, _ = _deal(players, trump=Suit.CLUBS)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trump_suit == Suit.CLUBS

    def test_trump_suit_no_trump_contract(self, players):
        contract, seating, hands, _ = _deal(players, trump=TrumpVariant.NO_TRUMP)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.trump_suit == TrumpVariant.NO_TRUMP

    def test_led_suit_none_when_trick_empty(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.led_suit is None

    def test_led_suit_is_first_card_suit(self, players):
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)
        state = state.apply(Play(players["N"], by_seat["N"][0]))
        obs = state.observe(players["E"])
        assert obs.led_suit == Suit.SPADES

    def test_played_cards_is_chronological_flatten(self, players):
        state = _play_first_trick(players)
        # Advance one more card into trick 1 so both completed_tricks and
        # current_trick are non-empty.
        winner = state.to_act
        winner_seat = next(s for s in _ORDER if players[s] is winner)
        second_card = _deal(players)[3][winner_seat][1]
        state = state.apply(Play(winner, second_card))

        obs = state.observe(winner)

        expected = tuple(play.card for trick in obs.completed_tricks for play in trick)
        expected += tuple(play.card for play in obs.current_trick)
        assert obs.played_cards == expected
        assert len(obs.played_cards) == 5

    def test_current_winner_none_when_trick_empty(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])
        assert obs.current_winner is None

    def test_current_winner_trump_beats_lead(self, players):
        """North leads a spade; East (void, holding only hearts — the
        trump suit) ruffs and becomes the current master mid-trick.

        The winner is reported as the seat's :class:`Position`, not the
        live :class:`BasePlayer` — the observation's trick records are
        sealed to seat identifiers."""
        contract, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)
        state = state.apply(Play(players["N"], by_seat["N"][0]))
        state = state.apply(Play(players["E"], by_seat["E"][0]))

        obs = state.observe(players["S"])

        assert obs.current_winner is Position.EAST


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_assigning_any_field_raises(self, players):
        contract, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        obs = state.observe(players["N"])

        for field_name in _EXPECTED_FIELDS:
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(obs, field_name, None)


# ---------------------------------------------------------------------------
# Sealed trick records
# ---------------------------------------------------------------------------


class TestSealedTrickRecords:
    """The observation's trick history must carry opaque seat positions.

    ``PlayState.plays`` records hold live ``BasePlayer`` references; the
    projection must not hand those over — otherwise a strategy could reach
    through ``play.player.hand`` and read another seat's cards.
    """

    def _observed_mid_trick(self, players) -> PlayObservation:
        """One completed trick plus one play into trick 1."""
        state = _play_first_trick(players)
        winner = state.to_act
        winner_seat = next(s for s in _ORDER if players[s] is winner)
        second_card = _deal(players)[3][winner_seat][1]
        state = state.apply(Play(winner, second_card))
        return state.observe(winner)

    def test_trick_records_are_observed_plays_carrying_positions(self, players):
        obs = self._observed_mid_trick(players)
        every_play = [
            play for trick in obs.completed_tricks for play in trick
        ] + list(obs.current_trick)

        assert every_play, "the scenario must produce plays to inspect"
        for play in every_play:
            assert isinstance(play, ObservedPlay)
            assert isinstance(play.position, Position)
        # Each completed trick arrives typed as a TrickRecord of sealed
        # records (still a plain tuple to every existing consumer).
        for trick in obs.completed_tricks:
            assert isinstance(trick, TrickRecord)

    def test_positions_match_the_players_who_played(self, players):
        obs = self._observed_mid_trick(players)

        # Trick 0 was played in seating order N, E, S, W.
        recorded = tuple(play.position for play in obs.completed_tricks[0])
        expected = tuple(players[s].position for s in _ORDER)
        assert recorded == expected

    def test_observed_plays_unpack_as_position_card_pairs(self, players):
        obs = self._observed_mid_trick(players)

        for position, card in obs.current_trick:
            assert isinstance(position, Position)
            assert isinstance(card, Card)

    def test_no_base_player_reachable_through_trick_records(self, players):
        obs = self._observed_mid_trick(players)
        every_play = [
            play for trick in obs.completed_tricks for play in trick
        ] + list(obs.current_trick)

        for play in every_play:
            assert not hasattr(play, "player")
            assert not any(isinstance(item, BasePlayer) for item in play)


# ---------------------------------------------------------------------------
# Sealed contract
# ---------------------------------------------------------------------------


class TestSealedContract:
    """The contract must name its people by seat, not by reference.

    A live ``Contract`` holds the declaring player, the declaring
    ``Team`` (whose ``players`` list reaches both members' hands), and
    the doubler / redoubler.
    """

    def _doubled_observation(self, players) -> PlayObservation:
        """An observation whose contract is North's, doubled by East."""
        contract = Contract(
            ContractBid(players["N"], 120, Suit.HEARTS),
            double_player=players["E"],
        )
        _, seating, hands, _ = _deal(players)
        state = PlayState.start(contract, seating, hands)
        return state.observe(players["S"])

    def test_contract_is_an_observed_contract(self, players):
        obs = self._doubled_observation(players)
        assert isinstance(obs.contract, ObservedContract)

    def test_declarer_and_doubler_are_positions(self, players):
        obs = self._doubled_observation(players)
        assert obs.contract.declarer is Position.NORTH
        assert obs.contract.doubled_by is Position.EAST
        assert obs.contract.redoubled_by is None

    def test_terms_survive_the_projection(self, players):
        obs = self._doubled_observation(players)
        assert obs.contract.value == 120
        assert obs.contract.suit is Suit.HEARTS
        assert obs.contract.double is True
        assert obs.contract.get_multiplier() == 2

    def test_no_declaring_player_or_team_is_reachable(self, players):
        obs = self._doubled_observation(players)
        for gone in ("player", "team", "double_player", "redouble_player"):
            assert not hasattr(obs.contract, gone)

    def test_declaring_side_is_derivable_from_the_seat_alone(self, players):
        # The reason ObservedContract carries no Team: the observer can
        # answer "did my side declare this?" from the seats it has.
        obs = self._doubled_observation(players)
        assert obs.position.is_teammate(obs.contract.declarer)
        assert not Position.WEST.is_teammate(obs.contract.declarer)

    def test_trump_suit_still_reads_through_the_sealed_contract(self, players):
        obs = self._doubled_observation(players)
        assert obs.trump_suit is Suit.HEARTS


# ---------------------------------------------------------------------------
# Reachability — the acceptance criterion, checked rather than reviewed
# ---------------------------------------------------------------------------


def _slot_names(cls) -> list[str]:
    """Every slot declared anywhere in ``cls``'s MRO.

    Walking only ``type(obj).__slots__`` is not enough for a slotted
    class hierarchy: a ``ContractBid`` declares ``('value', 'suit')``
    while the ``player`` slot it inherits is declared on ``Bid``. Missing
    it would let the reachability walk below pass over exactly the field
    the seal exists to protect.
    """
    return [
        name
        for klass in cls.__mro__
        for name in getattr(klass, "__slots__", ()) or ()
    ]


def _reachable(root, _seen=None):
    """Walk every object reachable from ``root`` by attribute or element.

    Traverses dataclasses, named tuples, plain tuples/lists/dicts and
    ordinary objects' ``__dict__`` / ``__slots__`` (the whole MRO's), so
    a leak buried behind several hops
    (``contract.team.players[0].hand``) is found the same way a strategy
    would find it. Cycles are cut by identity.

    Args:
        root: The object to start from.
        _seen: Recursion bookkeeping; callers leave it unset.

    Yields:
        Every distinct object reachable from ``root``, ``root`` included.
    """
    if _seen is None:
        _seen = {}
    if id(root) in _seen:
        return
    _seen[id(root)] = root
    yield root

    children = []
    if isinstance(root, (str, bytes, int, float, bool, type(None))):
        return
    if isinstance(root, dict):
        children = list(root.keys()) + list(root.values())
    elif isinstance(root, (tuple, list, set, frozenset)):
        children = list(root)
    else:
        for name in _slot_names(type(root)):
            if hasattr(root, name):
                children.append(getattr(root, name))
        children.extend(vars(root).values() if hasattr(root, "__dict__") else ())
        # Hand is not a sequence subclass — it wraps its cards rather
        # than exposing them as attributes, so iterate it explicitly.
        if isinstance(root, Hand):
            children.extend(list(root))

    for child in children:
        yield from _reachable(child, _seen)


class TestNothingLiveIsReachable:
    """No object path from an observation may reach hidden state.

    This is issue #6's acceptance criterion stated as a property rather
    than a field-by-field review: whatever shape the observation grows
    into, a live ``BasePlayer`` (and through it ``.hand``, and through
    ``.team.players`` the partner's hand) must never be reachable from
    it. The walk below is deliberately structural, so a leak reintroduced
    three hops down a future field still fails this test.
    """

    def _fully_populated(self, players) -> PlayObservation:
        """Every field non-trivially filled: doubled contract, full
        four-seat auction, a completed trick and a partial one."""
        contract = Contract(
            ContractBid(players["N"], 120, Suit.HEARTS),
            double_player=players["E"],
            redouble_player=players["S"],
        )
        _, seating, hands, by_seat = _deal(players)
        state = PlayState.start(contract, seating, hands)
        for seat in _ORDER:
            state = state.apply(Play(players[seat], by_seat[seat][0]))
        winner = state.to_act
        winner_seat = next(s for s in _ORDER if players[s] is winner)
        state = state.apply(Play(winner, by_seat[winner_seat][1]))

        bids = [
            PassBid(players["W"]),
            ContractBid(players["N"], 120, Suit.HEARTS),
            DoubleBid(players["E"]),
            RedoubleBid(players["S"]),
        ]
        return state.observe(winner, bids=bids)

    def test_the_scenario_actually_fills_every_field(self, players):
        obs = self._fully_populated(players)
        assert obs.hand and obs.legal_cards
        assert obs.contract is not None and obs.contract.redoubled_by
        assert len(obs.bids) == 4
        assert obs.completed_tricks and obs.current_trick

    def test_no_base_player_is_reachable(self, players):
        obs = self._fully_populated(players)
        leaks = [o for o in _reachable(obs) if isinstance(o, BasePlayer)]
        assert not leaks, f"live players reachable from the observation: {leaks}"

    def test_no_team_is_reachable(self, players):
        # Team.players is a two-element list of live players — reaching a
        # Team is reaching both its members' hands.
        obs = self._fully_populated(players)
        assert not [o for o in _reachable(obs) if isinstance(o, Team)]

    def test_no_hand_object_is_reachable(self, players):
        obs = self._fully_populated(players)
        assert not [o for o in _reachable(obs) if isinstance(o, Hand)]

    def test_only_the_observers_own_and_public_cards_are_reachable(self, players):
        obs = self._fully_populated(players)
        allowed = set(obs.hand) | set(obs.played_cards)
        reachable_cards = {o for o in _reachable(obs) if isinstance(o, Card)}
        assert reachable_cards <= allowed

    def test_the_walk_finds_a_leak_when_one_exists(self, players):
        # Guard against the reachability walk silently passing because it
        # traverses nothing: given a live Contract it must find the
        # declarer, the team, and every seat's Hand.
        contract = Contract(ContractBid(players["N"], 120, Suit.HEARTS))
        found = list(_reachable(contract))
        assert any(isinstance(o, BasePlayer) for o in found)
        assert any(isinstance(o, Team) for o in found)
        assert any(isinstance(o, Hand) for o in found)

    def test_the_walk_finds_a_leak_planted_in_a_sealed_field(self, players):
        # The sharper negative control: plant an unsealed bid in an
        # otherwise-sealed observation and confirm the walk reaches the
        # player through it. A ContractBid inherits its ``player`` slot
        # from Bid rather than declaring it, so a walk that read only
        # ``type(obj).__slots__`` would pass this observation as clean.
        obs = self._fully_populated(players)
        leaky = dataclasses.replace(
            obs, bids=(ContractBid(players["W"], 120, Suit.HEARTS),)
        )
        found = [o for o in _reachable(leaky) if isinstance(o, BasePlayer)]
        assert players["W"] in found
