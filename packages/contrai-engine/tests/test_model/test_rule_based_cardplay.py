"""Unit tests for the rule-based AI card-play strategy.

The strategy is handed a single frozen ``PlayObservation`` and derives
its own card tracking (fallen cards, inferred trump voids) from the
observation's public trick history — there is no mutable per-round state
to seed. Every scenario below is therefore expressed by building a real
observation (own hand, contract, and completed / in-progress tricks made
of genuine ``Play`` records), never by poking attributes on the strategy.
"""

import pytest

from contrai_core import (
    Card,
    Contract,
    ContractBid,
    Play,
    PlayObservation,
    PlayState,
)
from contrai_core.types import Suit, Rank


def _contract(player, value, suit):
    """Build a real :class:`Contract` (the type the engine threads in)."""
    return Contract(ContractBid(player, value, suit))


def _obs(
    observer,
    hand,
    contract,
    *,
    current_trick=(),
    completed_tricks=(),
    legal_cards=None,
    bids=(),
):
    """Assemble a :class:`PlayObservation` for ``observer``.

    Args:
        observer: The seat the observation is from the point of view of.
        hand: The observer's own remaining cards.
        contract: The established :class:`Contract` (supplies trump).
        current_trick: Plays made so far in the in-progress trick, a
            sequence of :class:`Play`.
        completed_tricks: Sequence of completed tricks, each a sequence of
            four :class:`Play`.
        legal_cards: The observer's legal plays; defaults to the whole hand
            (the observer is leading / everything is legal).
        bids: The auction history to attach.
    """
    hand = tuple(hand)
    return PlayObservation(
        player=observer,
        hand=hand,
        contract=contract,
        bids=tuple(bids),
        completed_tricks=tuple(tuple(trick) for trick in completed_tricks),
        current_trick=tuple(current_trick),
        legal_cards=tuple(hand if legal_cards is None else legal_cards),
    )


# Shorthand card constructors keep the scenario tables readable.
def _c(suit, rank):
    return Card(suit, rank)


# ---------------------------------------------------------------------------
# Opening and leading leads
# ---------------------------------------------------------------------------


class TestOpeningLead:
    """The very first card of the round (empty trick, trick 0)."""

    def test_own_contract_plays_strongest_trump(self, players):
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        obs = _obs(north, hand, _contract(north, 80, Suit.SPADES))
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.JACK)

    def test_opponents_contract_plays_ace_from_shortest_suit(self, players):
        north, east = players["N"], players["E"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.HEARTS, Rank.TEN),
            _c(Suit.DIAMONDS, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.DIAMONDS, Rank.SEVEN),
            _c(Suit.CLUBS, Rank.QUEEN),
        ]
        # Opponent East declares; aces are ♠A (2-card suit) and ♦A (3-card).
        obs = _obs(north, hand, _contract(east, 100, Suit.HEARTS))
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.ACE)


class TestSubsequentLead:
    """Leading a later trick (empty trick, trick_number > 0)."""

    def _prior_trick(self, players):
        """A clean completed trick — four followed hearts, nobody void."""
        return (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )

    def test_leads_strongest_trump_when_opponents_may_hold_trump(self, players):
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.NINE),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        # No trumps fell in the prior trick and nobody is void, so the
        # opponents may still hold trump — pull it.
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[self._prior_trick(players)],
        )
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.JACK)

    def test_leads_ace_when_both_opponents_known_void(self, players):
        north = players["N"]
        # A prior trick where East and West were both compelled to discard
        # off-suit (no trump), proving both void — the pull must stop.
        both_void = (
            Play(north, _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),
        )
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.NINE),
            _c(Suit.DIAMONDS, Rank.ACE),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[both_void],
        )
        result = north.cardplay.choose_card(obs)
        # Not a trump — the pull stopped; the ace goes out instead.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)


# ---------------------------------------------------------------------------
# Following: team currently winning
# ---------------------------------------------------------------------------


class TestFollowingTeamWinning:
    """Partner is the led-suit master, so the seat adds value cheaply."""

    def _partner_master_spade_lead(self, players):
        """Partner (South) leads ♠A and stands as led-suit master."""
        return (
            Play(players["S"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["W"], _c(Suit.SPADES, Rank.SEVEN)),
        )

    def test_does_not_waste_trump_behind_master_partner(self, players):
        """Cannot follow the led suit but partner is master — discard a
        non-trump rather than burn a trump, even a points-rich one."""
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),   # trump — must NOT play
            _c(Suit.HEARTS, Rank.NINE),   # trump — must NOT play
            _c(Suit.DIAMONDS, Rank.KING),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
        )
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.KING)

    def test_dumps_highest_points_non_trump_non_master(self, players):
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.NINE),   # trump — must NOT play
            _c(Suit.DIAMONDS, Rank.TEN),  # 10 points, non-master (♦A still out)
            _c(Suit.CLUBS, Rank.EIGHT),   # 0 points, non-master
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
        )
        result = north.cardplay.choose_card(obs)
        # Highest-points non-trump non-master → ♦10.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.TEN)

    def test_prefers_non_master_over_master_in_discard(self, players):
        north = players["N"]
        hand = [
            _c(Suit.CLUBS, Rank.ACE),      # master (no higher club)
            _c(Suit.DIAMONDS, Rank.KING),  # non-master (♦A still out)
            _c(Suit.HEARTS, Rank.NINE),    # trump
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
        )
        result = north.cardplay.choose_card(obs)
        # ♣A preserved (master), ♥9 preserved (trump), ♦K dumped.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.KING)

    def test_falls_back_to_lowest_trump_when_only_trumps_left(self, players):
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),   # top trump
            _c(Suit.HEARTS, Rank.NINE),   # 2nd-best
            _c(Suit.HEARTS, Rank.SEVEN),  # lowest
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
        )
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.SEVEN)

    def test_follows_suit_with_highest_points(self, players):
        north = players["N"]
        # Partner (South) leads ♥Q and is led-suit master; ♠ is trump.
        current = (
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.SEVEN)),
        )
        hand = [
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.HEARTS, Rank.TEN),
            _c(Suit.HEARTS, Rank.EIGHT),
            _c(Suit.SPADES, Rank.ACE),
        ]
        playable = [
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.HEARTS, Rank.TEN),
            _c(Suit.HEARTS, Rank.EIGHT),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs)
        # ♥10 is worth the most points among the followable hearts.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.TEN)


# ---------------------------------------------------------------------------
# Following: team currently losing
# ---------------------------------------------------------------------------


class TestFollowingTeamLosing:
    """An opponent holds the led-suit master — try to take the trick."""

    def test_follows_suit_and_beats_when_able(self, players):
        north = players["N"]
        # West (opponent) leads ♥K; North is 2nd to act.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.KING)),)
        hand = [
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.HEARTS, Rank.EIGHT),
            _c(Suit.SPADES, Rank.JACK),
        ]
        playable = [_c(Suit.HEARTS, Rank.ACE), _c(Suit.HEARTS, Rank.EIGHT)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs)
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.ACE)

    def test_follows_suit_low_when_cannot_beat(self, players):
        north = players["N"]
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),)
        hand = [
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.HEARTS, Rank.EIGHT),
            _c(Suit.SPADES, Rank.JACK),
        ]
        playable = [_c(Suit.HEARTS, Rank.JACK), _c(Suit.HEARTS, Rank.EIGHT)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs)
        # Cannot beat ♥A — throw the lowest heart by points.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.EIGHT)

    def test_trumps_with_lowest_winning_trump(self, players):
        north = players["N"]
        # West leads ♥K; North is void in hearts and can ruff.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.KING)),)
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.NINE),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
        )
        result = north.cardplay.choose_card(obs)
        # Both trumps beat a bare heart; the lowest winning trump goes in.
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.NINE)

    def test_discards_lowest_short_suit_when_cannot_follow_or_trump(
        self, players
    ):
        north = players["N"]
        # West leads ♥K; ♠ trump; North holds neither hearts nor spades.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.KING)),)
        # ♣A would be master — a real ♦7 (shortest suit, non-master) goes.
        prior = (
            Play(players["N"], _c(Suit.DIAMONDS, Rank.ACE)),
            Play(players["E"], _c(Suit.DIAMONDS, Rank.KING)),
            Play(players["S"], _c(Suit.DIAMONDS, Rank.TEN)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.QUEEN)),
        )
        hand = [
            _c(Suit.DIAMONDS, Rank.SEVEN),
            _c(Suit.CLUBS, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.JACK),
            _c(Suit.CLUBS, Rank.TEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            completed_tricks=[prior],
        )
        result = north.cardplay.choose_card(obs)
        # Diamonds is the shortest suit; ♦7 is its lowest non-master card.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.SEVEN)


# ---------------------------------------------------------------------------
# Routing: opening vs leading vs following
# ---------------------------------------------------------------------------


class TestRouting:
    """``choose_card`` routes purely on the trick shape, not on tracking."""

    def _hand(self):
        # Aces sit in a 1-card suit (♠) and a 2-card suit (♦): opening picks
        # the shortest-suit ace, leading picks the longest-suit ace.
        return [
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]

    def test_empty_trick_zero_takes_the_opening_path(self, players):
        north, east = players["N"], players["E"]
        obs = _obs(north, self._hand(), _contract(east, 100, Suit.HEARTS))
        assert obs.trick_number == 0
        result = north.cardplay.choose_card(obs)
        # Opening → ace from the shortest suit (♠A).
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.ACE)

    def test_empty_trick_after_history_takes_the_leading_path(self, players):
        north, east = players["N"], players["E"]
        prior = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        obs = _obs(
            north,
            self._hand(),
            _contract(east, 100, Suit.HEARTS),
            completed_tricks=[prior],
        )
        assert obs.trick_number == 1
        result = north.cardplay.choose_card(obs)
        # Leading → ace from the longest suit (♦A).
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def test_non_empty_trick_takes_the_following_path(self, players):
        north = players["N"]
        # Opponent West leads ♥A; North can only follow low.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),)
        hand = [_c(Suit.HEARTS, Rank.SEVEN), _c(Suit.HEARTS, Rank.EIGHT)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
        )
        result = north.cardplay.choose_card(obs)
        # Following a losing trick it cannot beat → lowest heart.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.SEVEN)


# ---------------------------------------------------------------------------
# Parity suite: _derive_tracking rebuilds fallen cards and trump voids
# ---------------------------------------------------------------------------


class TestDeriveTracking:
    """The replay of the public history must reconstruct exactly the
    fallen-card map and trump-void set a per-card tracker would accumulate.
    """

    def test_fallen_counts_every_card_including_own_plays(self, players):
        north = players["N"]
        completed = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.EIGHT)),
            Play(players["W"], _c(Suit.HEARTS, Rank.NINE)),
        )
        current = (Play(players["N"], _c(Suit.CLUBS, Rank.ACE)),)
        obs = _obs(
            north,
            [_c(Suit.CLUBS, Rank.ACE)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
            current_trick=current,
        )
        fallen, voids = north.cardplay._derive_tracking(obs)
        assert fallen[Suit.HEARTS] == {Rank.KING, Rank.SEVEN, Rank.EIGHT, Rank.NINE}
        # North's own club is counted just like everyone else's cards.
        assert fallen[Suit.CLUBS] == {Rank.ACE}
        assert fallen[Suit.SPADES] == set()
        assert fallen[Suit.DIAMONDS] == set()
        assert voids == set()

    def test_partner_master_discard_proves_no_void(self, players):
        """East discards behind its master partner West — proves nothing."""
        north = players["N"]
        # West leads ♥A (master); East discards a club while West is master.
        completed = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        obs = _obs(
            north,
            [_c(Suit.SPADES, Rank.JACK)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
        )
        fallen, voids = north.cardplay._derive_tracking(obs)
        assert Rank.SEVEN in fallen[Suit.CLUBS]
        assert players["E"] not in voids  # partner-master exemption

    def test_trump_lead_off_suit_always_proves_void(self, players):
        """On a trump lead the partner-master exemption does not apply."""
        north = players["N"]
        # East leads ♠A (trump); West (East's partner) is master, yet East's
        # partner-master status must NOT excuse a later off-trump card. Here
        # South discards off-trump on the trump lead → South is void.
        completed = (
            Play(players["E"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["S"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.SEVEN)),
            Play(players["N"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        obs = _obs(
            north,
            [_c(Suit.CLUBS, Rank.JACK)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
        )
        _, voids = north.cardplay._derive_tracking(obs)
        assert players["S"] in voids

    def test_pre_play_winner_is_read_before_the_play_lands(self, players):
        """The off-by-one case: a seat compelled to discard is void even
        when its partner becomes master only through a LATER play."""
        north = players["N"]
        # ♠ trump, ♥ led by North.
        #   0 N ♥K   (leads)
        #   1 E ♣7   → prior master is North (opponent of East) → E void
        #   2 S ♥Q   (follows)
        #   3 W ♠7   → West (East's partner) ruffs and becomes master
        # Judged post-hoc, East's partner ends the trick master and the void
        # would vanish; judged pre-play (correctly), East was compelled.
        completed = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.SEVEN)),
        )
        obs = _obs(
            north,
            [_c(Suit.DIAMONDS, Rank.ACE)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
        )
        fallen, voids = north.cardplay._derive_tracking(obs)
        assert players["E"] in voids
        assert fallen[Suit.CLUBS] == {Rank.SEVEN}
        assert fallen[Suit.SPADES] == {Rank.SEVEN}

    def test_derivation_matches_a_real_play_state_projection(self, players):
        """A history-seeded observation and one projected from a bare
        ``PlayState`` derive the same tracking — the two construction
        routes agree."""
        north = players["N"]
        order = (players["N"], players["E"], players["S"], players["W"])
        # A completed trick with a compelled off-suit discard by East.
        plays = (
            Play(players["N"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        contract = _contract(north, 100, Suit.SPADES)
        # Remaining hands are irrelevant to derivation; give North a spare.
        hands = (
            (_c(Suit.SPADES, Rank.JACK),),
            (_c(Suit.SPADES, Rank.NINE),),
            (_c(Suit.SPADES, Rank.SEVEN),),
            (_c(Suit.SPADES, Rank.EIGHT),),
        )
        state = PlayState(contract, order, hands, plays)
        projected = state.observe(north, bids=())

        seeded = _obs(
            north,
            [_c(Suit.SPADES, Rank.JACK)],
            contract,
            completed_tricks=[plays],
        )

        assert north.cardplay._derive_tracking(projected) == (
            north.cardplay._derive_tracking(seeded)
        )


# ---------------------------------------------------------------------------
# Parity suite: the trump-pull inference reads derived voids / fallen counts
# ---------------------------------------------------------------------------


class TestTrumpPullInference:
    """``_opponents_might_have_trump`` fed the tracking the replay derives.

    Each case seeds the inference through real completed-trick history and
    asserts the pull decision, never by poking a void set onto the strategy.
    """

    def _derive(self, players, completed, hand):
        north = players["N"]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=completed,
        )
        return north.cardplay, north.cardplay._derive_tracking(obs), obs.hand

    def test_both_opponents_void_stops_the_pull(self, players):
        north = players["N"]
        both_void = (
            Play(north, _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),   # East void
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),  # West void
        )
        hand = [_c(Suit.SPADES, Rank.JACK)]
        strat, (fallen, voids), obs_hand = self._derive(
            players, [both_void], hand
        )
        assert {players["E"], players["W"]} <= voids
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs_hand
        ) is False

    def test_one_opponent_void_keeps_the_pull(self, players):
        north = players["N"]
        one_void = (
            Play(north, _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),  # East void
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.HEARTS, Rank.SEVEN)),  # West follows
        )
        hand = [_c(Suit.SPADES, Rank.JACK)]
        strat, (fallen, voids), obs_hand = self._derive(
            players, [one_void], hand
        )
        assert players["E"] in voids and players["W"] not in voids
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs_hand
        ) is True

    def test_partner_void_does_not_stop_the_pull(self, players):
        north = players["N"]
        # East leads ♥A; South (North's partner) is compelled void; the two
        # opponents are not — a void partner says nothing about them.
        partner_void = (
            Play(players["E"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["S"], _c(Suit.CLUBS, Rank.SEVEN)),  # South void
            Play(players["W"], _c(Suit.HEARTS, Rank.KING)),
            Play(north, _c(Suit.HEARTS, Rank.SEVEN)),
        )
        hand = [_c(Suit.SPADES, Rank.JACK)]
        strat, (fallen, voids), obs_hand = self._derive(
            players, [partner_void], hand
        )
        assert players["S"] in voids
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs_hand
        ) is True

    def test_counting_alone_stops_the_pull(self, players):
        north = players["N"]
        # Six spades fall across two tricks; North holds the other two, so
        # no unseen trump remains even without any void knowledge.
        trick_a = (
            Play(players["N"], _c(Suit.SPADES, Rank.KING)),
            Play(players["E"], _c(Suit.SPADES, Rank.QUEEN)),
            Play(players["S"], _c(Suit.SPADES, Rank.TEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        trick_b = (
            Play(players["N"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["E"], _c(Suit.SPADES, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        hand = [_c(Suit.SPADES, Rank.JACK), _c(Suit.SPADES, Rank.NINE)]
        strat, (fallen, voids), obs_hand = self._derive(
            players, [trick_a, trick_b], hand
        )
        assert len(fallen[Suit.SPADES]) == 6
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs_hand
        ) is False


# ---------------------------------------------------------------------------
# Pure helper unit tests (fallen / plays threaded explicitly)
# ---------------------------------------------------------------------------


class TestPureHelpers:
    """The trick-reading helpers now take their tracking / plays as
    arguments and are exercised directly."""

    @pytest.fixture
    def strat(self, players):
        """North's card-play strategy, host of the helpers under test."""
        return players["N"].cardplay

    def _fallen(self, suit=None, ranks=()):
        base = {s: set() for s in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)}
        if suit is not None:
            base[suit] = set(ranks)
        return base

    def test_is_master_card_reads_the_fallen_map(self, strat):
        fallen = self._fallen(Suit.HEARTS, {Rank.ACE, Rank.QUEEN, Rank.EIGHT})
        # ♥10's only higher card (♥A) has fallen → master.
        assert strat._is_master_card(_c(Suit.HEARTS, Rank.TEN), Suit.SPADES, fallen) is True
        # ♥K still has ♥10 out → not master.
        assert strat._is_master_card(_c(Suit.HEARTS, Rank.KING), Suit.SPADES, fallen) is False

    def test_higher_ranks_respect_trump_vs_normal_order(self, strat):
        normal = strat._get_higher_ranks(Rank.NINE, Suit.HEARTS, Suit.SPADES)
        assert Rank.JACK in normal and Rank.ACE in normal
        trump = strat._get_higher_ranks(Rank.NINE, Suit.SPADES, Suit.SPADES)
        assert Rank.JACK in trump and Rank.ACE not in trump  # 9 outranks A in trump

    def test_team_winning_reads_the_led_suit_master(self, strat, players):
        partner_master = (
            Play(players["S"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["W"], _c(Suit.HEARTS, Rank.KING)),
        )
        assert strat._is_team_winning_trick(partner_master) is True
        opponent_master = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
        )
        assert strat._is_team_winning_trick(opponent_master) is False

    def test_strongest_card_with_trump_on_the_table(self, strat, players):
        plays = (
            Play(players["N"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["S"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        best = strat._get_strongest_card_in_trick(plays, Suit.SPADES)
        assert (best.suit, best.rank) == (Suit.SPADES, Rank.EIGHT)

    def test_strongest_card_without_trump(self, strat, players):
        plays = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["S"], _c(Suit.DIAMONDS, Rank.ACE)),
        )
        best = strat._get_strongest_card_in_trick(plays, Suit.SPADES)
        assert (best.suit, best.rank) == (Suit.HEARTS, Rank.ACE)

    def test_can_trump_win_reads_the_plays(self, strat, players):
        plays = (
            Play(players["N"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        assert strat._can_trump_win(_c(Suit.SPADES, Rank.JACK), plays, Suit.SPADES) is True
        assert strat._can_trump_win(_c(Suit.SPADES, Rank.SEVEN), plays, Suit.SPADES) is False

    def test_is_stronger_card_comparison(self, strat):
        # Trump beats non-trump.
        assert strat._is_stronger_card(
            _c(Suit.SPADES, Rank.SEVEN), _c(Suit.HEARTS, Rank.ACE), Suit.SPADES
        ) is True
        # Higher of the same suit.
        assert strat._is_stronger_card(
            _c(Suit.HEARTS, Rank.ACE), _c(Suit.HEARTS, Rank.KING), Suit.SPADES
        ) is True
        # Higher trump beats lower trump.
        assert strat._is_stronger_card(
            _c(Suit.SPADES, Rank.JACK), _c(Suit.SPADES, Rank.NINE), Suit.SPADES
        ) is True
