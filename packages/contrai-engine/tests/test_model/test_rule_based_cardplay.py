"""Unit tests for the rule-based AI card-play strategy.

The strategy is handed a single frozen ``PlayObservation`` and derives
its own card tracking (fallen cards, per-seat proven-void suits, keyed
by ``Position``) from the observation's public trick history — there is
no mutable per-round state to seed. Every scenario below is therefore
expressed by building a real observation — own hand, contract, and
completed / in-progress tricks written in omniscient terms and sealed
onto seats by the ``_obs`` helper exactly as ``PlayState.observe`` seals
them — never by poking attributes on the strategy.
"""

import pytest

from contrai_core import (
    Card,
    Contract,
    ContractBid,
    ObservedPlay,
    Play,
    PlayObservation,
    PlayState,
    Position,
    seal_bid,
)
from contrai_core.rules import rules_for
from contrai_core.types import Suit, Rank, TrumpVariant


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

    Scenarios are written in omniscient terms — :class:`Play` records
    and a live :class:`Contract`, the same objects the engine applies to
    its play state — and this helper seals them exactly as
    ``PlayState.observe`` does: plays to :class:`ObservedPlay`
    ``(position, card)`` pairs, the contract to an
    :class:`ObservedContract`, each bid via :func:`seal_bid`. The
    strategy under test therefore receives the same sealed surface
    production hands it, from scenario tables that stay readable.

    Args:
        observer: The seat the observation is from the point of view of.
        hand: The observer's own remaining cards.
        contract: The established :class:`Contract` (supplies trump), or
            ``None``.
        current_trick: Plays made so far in the in-progress trick, a
            sequence of :class:`Play`.
        completed_tricks: Sequence of completed tricks, each a sequence of
            four :class:`Play`.
        legal_cards: The observer's legal plays; defaults to the whole hand
            (the observer is leading / everything is legal).
        bids: The auction history to attach.
    """
    hand = tuple(hand)

    def seal(plays):
        return tuple(
            ObservedPlay(play.player.position, play.card) for play in plays
        )

    return PlayObservation(
        position=observer.position,
        hand=hand,
        contract=contract.observed() if contract is not None else None,
        bids=tuple(seal_bid(bid) for bid in bids),
        completed_tricks=tuple(seal(trick) for trick in completed_tricks),
        current_trick=seal(current_trick),
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.ACE)

    def test_aceless_lead_takes_the_cheapest_from_the_longest_suit(
        self, players
    ):
        """No ace to open with — the fallback sheds from the long suit.

        Both 8s are free, so the tie falls to suit length and the
        three-card suit gives one up.
        """
        north, east = players["N"], players["E"]
        hand = [
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.DIAMONDS, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.EIGHT),
            _c(Suit.CLUBS, Rank.KING),
            _c(Suit.CLUBS, Rank.QUEEN),
        ]
        obs = _obs(north, hand, _contract(east, 100, Suit.HEARTS))
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.CLUBS, Rank.EIGHT)


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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
        # Not a trump — the pull stopped; the ace goes out instead.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def test_lead_without_ace_or_master_sheds_from_the_longest_suit(
        self, players
    ):
        """Nothing to cash — the fallback sheds from the long suit.

        Both 8s are free, so the tie falls to suit length.
        """
        north = players["N"]
        # Both opponents proved void in trump, so the pull stops; no ace
        # and no master remains, which lands on the lowest-value rule.
        both_void = (
            Play(north, _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),
        )
        hand = [
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.DIAMONDS, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.EIGHT),
            _c(Suit.CLUBS, Rank.KING),
            _c(Suit.CLUBS, Rank.QUEEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[both_void],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.CLUBS, Rank.EIGHT)


class TestSubsequentLeadSparesTrump:
    """Trump is held back once both opponents are proven trump-void.

    A plain-suit winner is only safe because nobody can cut it, while a
    trump held against void opponents wins whenever it is finally led.
    So the ace / master search that picks the lead must run on plain
    cards only in that position — otherwise the seat cashes a trump ace
    where a plain ace does the same job, and hands the trump length back.

    Every hand below is three spades (trump) and two diamonds, so the
    "ace from the longest suit" tie-break points at the *trump* ace: the
    suit-length rule alone would pick it, and only the trump-void filter
    keeps it back.
    """

    def _both_opponents_void(self, players):
        """A completed trick proving East and West hold no trump.

        Both were compelled to discard off the hearts lead with no
        partner already master, which proves a void in the led suit and
        in trump at once.
        """
        return (
            Play(players["N"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),
        )

    def _clean_trick(self, players):
        """A completed trick nobody was compelled to discard into."""
        return (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )

    def _two_ace_hand(self):
        """Three trump (spades) holding the ace, two diamonds holding one."""
        return [
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.SPADES, Rank.KING),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.DIAMONDS, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.SEVEN),
        ]

    def test_declaring_side_cashes_the_plain_ace_not_the_trump_ace(
        self, players
    ):
        """Our contract, opponents out of trump: the plain ace goes out.

        The trump pull stops (nothing left to pull), and the ace that
        follows must come from a plain suit even though trump is the
        longer holding.
        """
        north = players["N"]
        obs = _obs(
            north,
            self._two_ace_hand(),
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[self._both_opponents_void(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def test_defender_cashes_the_plain_ace_not_the_trump_ace(self, players):
        """The same restraint applies when the opponents declared.

        Holding trump back against void opponents is worth just as much
        on defence — only the pull branch above asks who declared.
        """
        north, east = players["N"], players["E"]
        obs = _obs(
            north,
            self._two_ace_hand(),
            _contract(east, 100, Suit.SPADES),
            completed_tricks=[self._both_opponents_void(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def _trump_jack_hand(self):
        """Three trump (spades) topped by the Jack, two diamonds by the ace."""
        return [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.KING),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.DIAMONDS, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.SEVEN),
        ]

    def test_trump_winner_still_leads_while_an_opponent_may_ruff(
        self, players
    ):
        """Opponents may still hold trump — the unrestricted search stands.

        Read as a defender so the trump-pull branch is out of the way and
        the winner choice is the only thing under test. The trump Jack
        tops its own ladder, so with a ruff still possible it is back in
        the running and the longest suit wins the tie as before.
        """
        north, east = players["N"], players["E"]
        obs = _obs(
            north,
            self._trump_jack_hand(),
            _contract(east, 100, Suit.SPADES),
            completed_tricks=[self._clean_trick(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.JACK)

    def test_the_trump_ace_is_not_a_winner_a_jack_and_9_still_beat(
        self, players
    ):
        """A trump ace is the *third* card of the trump ladder.

        The suit-length tie-break used to point straight at it, which
        cashed a card the trump Jack and 9 both take. Asking the ladder
        instead of naming ``Rank.ACE`` keeps it back and cashes the
        genuinely unbeatable plain ace.
        """
        north, east = players["N"], players["E"]
        obs = _obs(
            north,
            self._two_ace_hand(),
            _contract(east, 100, Suit.SPADES),
            completed_tricks=[self._clean_trick(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def test_trump_is_held_back_even_when_it_tops_its_ladder(self, players):
        """The void filter still outranks the ladder: ♦A over the ♠J.

        With both opponents proven out of trump the Jack cannot be taken
        off us later, so cashing it now buys nothing the plain ace does
        not already buy.
        """
        north, east = players["N"], players["E"]
        obs = _obs(
            north,
            self._trump_jack_hand(),
            _contract(east, 100, Suit.SPADES),
            completed_tricks=[self._both_opponents_void(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)

    def test_all_trump_hand_leads_the_cheapest_trump(self, players):
        """Nothing plain left: spend the cheapest trump, not the ace.

        With the opponents void, every trump in hand takes the trick, so
        the seat has no reason to lead the ace. The plain-only filter
        deliberately leaves no candidate here rather than falling back to
        the full set — the cheap-trump default below it is the better
        lead, and it is what must fire.
        """
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.SPADES, Rank.KING),
            _c(Suit.SPADES, Rank.SEVEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[self._both_opponents_void(players)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.SEVEN)


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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
        # ♥10 is worth the most points among the followable hearts.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.TEN)

    def test_follow_suit_keeps_new_master_gives_next_highest(self, players):
        """Partner's ♠A makes the seat's ♠10 master — keep it, give ♠K."""
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.TEN),   # new master once ♠A falls — keep
            _c(Suit.SPADES, Rank.KING),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [
            _c(Suit.SPADES, Rank.TEN),
            _c(Suit.SPADES, Rank.KING),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        # ♠10 preserved to win a later spade trick; ♠K piles 4 points.
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.KING)

    def test_follow_suit_preserves_master_even_at_zero_points(self, players):
        """The master is kept even when the only alternative adds nothing."""
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.TEN),    # new master once ♠A falls — keep
            _c(Suit.SPADES, Rank.SEVEN),  # 0 points — given anyway
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [
            _c(Suit.SPADES, Rank.TEN),
            _c(Suit.SPADES, Rank.SEVEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.SEVEN)

    def test_follow_suit_forced_master_when_only_suit_card(self, players):
        """Sole card of the led suit is the master — forced fallback plays it."""
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.TEN),
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=self._partner_master_spade_lead(players),
            legal_cards=[_c(Suit.SPADES, Rank.TEN)],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.TEN)

    def test_trump_led_follow_preserves_new_trump_master(self, players):
        """Partner's trump Jack makes the seat's 9 master — pile the Ace."""
        north = players["N"]
        # Partner (South) leads the trump Jack; West follows low.
        current = (
            Play(players["S"], _c(Suit.HEARTS, Rank.JACK)),
            Play(players["W"], _c(Suit.HEARTS, Rank.SEVEN)),
        )
        hand = [
            _c(Suit.HEARTS, Rank.NINE),  # new trump master once ♥J falls — keep
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [
            _c(Suit.HEARTS, Rank.NINE),
            _c(Suit.HEARTS, Rank.ACE),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.HEARTS),
            current_trick=current,
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        # ♥9 (14 trump points) outscores ♥A (11) but is the new master —
        # the Ace goes onto partner's locked trick instead.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.ACE)

    def test_follows_low_when_opponent_cut_expected(self, players):
        """Partner holds the trick but East (void in the led suit, maybe
        holding trump) still has to play — the trick is presumed lost, so
        stop piling points on it and follow with the cheapest card."""
        north = players["N"]
        # Prior trick: East discards a club behind its master partner West
        # on a heart lead — proven void in ♥, trump holding still unknown.
        prior = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )
        # Partner South leads ♥K and stands master; East plays after us.
        current = (
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        hand = [
            _c(Suit.HEARTS, Rank.QUEEN),
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [_c(Suit.HEARTS, Rank.QUEEN), _c(Suit.HEARTS, Rank.JACK)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            completed_tricks=[prior],
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        # Without the anticipation rule the ♥Q (3 points) would pile on;
        # with East expected to ruff, the ♥J (2 points) is conceded instead.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.JACK)

    def test_discards_low_when_opponent_cut_expected(self, players):
        """Same predicted ruff, but the seat cannot follow — the discard
        turns cheap instead of feeding the cutter the fattest side card."""
        north = players["N"]
        prior = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )
        current = (
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        hand = [
            _c(Suit.DIAMONDS, Rank.TEN),  # non-master (♦A still out)
            _c(Suit.CLUBS, Rank.EIGHT),   # non-master, 0 points
            _c(Suit.SPADES, Rank.NINE),   # trump — still never dumped here
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            completed_tricks=[prior],
        )
        result = north.cardplay.choose_card(obs).card
        # Without the rule the ♦10 (10 points) would be piled on; with the
        # ruff expected the worthless ♣8 goes instead.
        assert (result.suit, result.rank) == (Suit.CLUBS, Rank.EIGHT)


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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
        # ♦7 is the cheapest non-master card in hand.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.SEVEN)

    def test_discard_prefers_cheapest_over_shortest_suit(self, players):
        """A free 7 beats a short suit's honour.

        Ordering by suit length first walks a shortening suit down to its
        10 while a 0-point card sits untouched in a longer suit; points
        come first, so the 7 goes.
        """
        north = players["N"]
        # West leads ♥A; ♥ trump; North holds no heart and cannot ruff.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),)
        hand = [
            # Diamonds is the shorter suit — and every card in it costs.
            _c(Suit.DIAMONDS, Rank.KING),
            _c(Suit.DIAMONDS, Rank.TEN),
            _c(Suit.SPADES, Rank.SEVEN),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.SPADES, Rank.KING),
        ]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, Suit.HEARTS),
            current_trick=current,
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.SEVEN)

    def test_discard_breaks_point_ties_from_the_longest_suit(self, players):
        """Equal-points candidates: the longest suit gives one up.

        Both 8s cost nothing, so the choice falls to suit length —
        pitching from the longer suit keeps the short one's shape.
        """
        north = players["N"]
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),)
        hand = [
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.DIAMONDS, Rank.QUEEN),
            _c(Suit.SPADES, Rank.EIGHT),
            _c(Suit.SPADES, Rank.KING),
            _c(Suit.SPADES, Rank.QUEEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, Suit.HEARTS),
            current_trick=current,
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.EIGHT)

    def test_discard_picks_randomly_when_points_and_length_tie(self, players):
        """Same points and same suit length — the pick is random.

        Both 8s sit in two-card suits and cost nothing, so nothing
        separates them; over many draws each must come up.
        """
        north = players["N"]
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),)
        hand = [
            _c(Suit.DIAMONDS, Rank.EIGHT),
            _c(Suit.DIAMONDS, Rank.QUEEN),
            _c(Suit.SPADES, Rank.EIGHT),
            _c(Suit.SPADES, Rank.QUEEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, Suit.HEARTS),
            current_trick=current,
        )
        seen = {
            north.cardplay.choose_card(obs).card.suit for _ in range(50)
        }
        assert seen == {Suit.DIAMONDS, Suit.SPADES}

    def test_beats_with_smallest_winning_card_when_cut_expected(self, players):
        """East (void in the led suit, trump holding unknown) plays after
        us — invest the cheapest card that still beats the current best,
        not the fattest, since a ruff would capture whatever we spend."""
        north = players["N"]
        # Prior trick: East discards a club behind its master partner West
        # on a heart lead — proven void in ♥ but not in trump.
        prior = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )
        # West leads ♥8 and is winning; East and partner play after us.
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),)
        hand = [
            _c(Suit.HEARTS, Rank.QUEEN),
            _c(Suit.HEARTS, Rank.TEN),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [_c(Suit.HEARTS, Rank.QUEEN), _c(Suit.HEARTS, Rank.TEN)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            completed_tricks=[prior],
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        # Both hearts beat the ♥8; without the rule the ♥10 (10 points)
        # would go in — with the ruff expected the ♥Q hedges instead.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.QUEEN)

    def test_beats_with_highest_points_when_cutter_known_trumpless(self, players):
        """The same shape stops firing once East is proven void in trump
        too — no ruff can come, so the fat winner goes in as usual."""
        north = players["N"]
        hearts_void = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )
        # Trump (♠) lead on which East shows out — East proven trumpless.
        trump_void = (
            Play(players["N"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["E"], _c(Suit.DIAMONDS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.SPADES, Rank.SEVEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        current = (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),)
        hand = [
            _c(Suit.HEARTS, Rank.QUEEN),
            _c(Suit.HEARTS, Rank.TEN),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        playable = [_c(Suit.HEARTS, Rank.QUEEN), _c(Suit.HEARTS, Rank.TEN)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            current_trick=current,
            completed_tricks=[hearts_void, trump_void],
            legal_cards=playable,
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.TEN)


class TestConcedeSparesTrumpUnderTheExemption:
    """The concede ladder must not spend a trump it was excused from.

    With ``under_trump_exemption`` on (the §9.5 default), a seat that is
    void in the led suit and holds only losing trump may discard instead
    of under-trumping — so the legal set is the whole hand and a losing
    trump sits beside every plain card. Conceding the trump there wastes
    exactly what the exemption exists to preserve.
    """

    #: West leads ♥A, North follows ♥7, East cuts ♠J — every South trump
    #: is below the ♠J, so the exemption opens South's whole hand.
    @staticmethod
    def _out_trumped_trick(players):
        return (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.SPADES, Rank.JACK)),
        )

    def test_discards_the_plain_card_instead_of_a_losing_trump(self, players):
        south = players["S"]
        hand = [
            _c(Suit.SPADES, Rank.SEVEN),
            _c(Suit.SPADES, Rank.EIGHT),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(
            south,
            hand,
            _contract(players["W"], 100, Suit.SPADES),
            current_trick=self._out_trumped_trick(players),
            # The exemption makes the whole hand legal.
            legal_cards=hand,
        )
        result = south.cardplay.choose_card(obs).card
        # ♠7 and ♠8 are as cheap as the ♣7 and sit in a longer suit, so the
        # length tie-break used to pick one of them. Trump is not spendable.
        assert (result.suit, result.rank) == (Suit.CLUBS, Rank.SEVEN)

    def test_pure_trump_hand_falls_back_to_the_cheapest_trump(self, players):
        """Nothing plain to spare — the full legal set is the candidate set."""
        south = players["S"]
        hand = [
            _c(Suit.SPADES, Rank.SEVEN),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.SPADES, Rank.KING),
        ]
        obs = _obs(
            south,
            hand,
            _contract(players["W"], 100, Suit.SPADES),
            current_trick=self._out_trumped_trick(players),
            legal_cards=hand,
        )
        result = south.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.SEVEN)

    def test_all_trump_is_unchanged_because_nothing_is_spendable(self, players):
        """Every card is trump at all trump, so the filter is a no-op.

        East discards a club on the heart lead — there is no cross-suit
        cutting at all trump (§6.4), so West's ♥A still holds the trick and
        South, void in hearts, is free to throw anything.
        """
        south = players["S"]
        current = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.JACK)),
        )
        hand = [
            _c(Suit.SPADES, Rank.SEVEN),
            _c(Suit.SPADES, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.KING),
        ]
        obs = _obs(
            south,
            hand,
            _contract(players["W"], 100, TrumpVariant.ALL_TRUMP),
            current_trick=current,
            legal_cards=hand,
        )
        result = south.cardplay.choose_card(obs).card
        # All-trump points: ♠7 = 0, ♠Q = 1, ♣K = 3.
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.SEVEN)

    def test_all_masters_gives_up_the_cheapest_rather_than_the_first(
        self, players
    ):
        """No non-master to shed — the ladder still ranks what is left.

        Both remaining cards top their own suit, so the old ``playable[0]``
        fallback conceded whichever happened to sit first in hand order.
        """
        south = players["S"]
        # ♥ and ♦ walked down far enough that South's ♥K and ♦Q are masters.
        hearts = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.HEARTS, Rank.TEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        diamonds = (
            Play(players["W"], _c(Suit.DIAMONDS, Rank.ACE)),
            Play(players["N"], _c(Suit.DIAMONDS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.DIAMONDS, Rank.KING)),
            Play(players["S"], _c(Suit.DIAMONDS, Rank.TEN)),
        )
        hand = [_c(Suit.HEARTS, Rank.KING), _c(Suit.DIAMONDS, Rank.QUEEN)]
        obs = _obs(
            south,
            hand,
            _contract(players["W"], 100, Suit.SPADES),
            current_trick=(Play(players["W"], _c(Suit.CLUBS, Rank.ACE)),),
            completed_tricks=[hearts, diamonds],
            legal_cards=hand,
        )
        result = south.cardplay.choose_card(obs).card
        # ♦Q costs 3, ♥K costs 4 — the cheaper master goes.
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.QUEEN)


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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
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
        result = north.cardplay.choose_card(obs).card
        # Following a losing trick it cannot beat → lowest heart.
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.SEVEN)


# ---------------------------------------------------------------------------
# Parity suite: _derive_tracking rebuilds fallen cards and trump voids
# ---------------------------------------------------------------------------


class TestDeriveTracking:
    """The replay of the public history must reconstruct exactly the
    fallen-card map and per-player void-suit map a per-card tracker
    would accumulate.
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
        assert voids == {}

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
        # Partner-master exemption: the heart void is proven, trump isn't.
        assert Suit.SPADES not in voids[Position.EAST]
        assert Suit.HEARTS in voids[Position.EAST]

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
        assert Suit.SPADES in voids[Position.SOUTH]

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
        assert Suit.SPADES in voids[Position.EAST]
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
# Suit-void tracking and the anticipated-ruff predicate
# ---------------------------------------------------------------------------


class TestSuitVoidTracking:
    """Any non-follow proves a led-suit void — the voids map records, per
    player, every suit that player has been seen unable to follow. The
    trump entry keeps its partner-master-exemption inference; led-suit
    entries need no exemption because following suit is never optional.
    """

    def test_discard_records_led_suit_void_without_trump_void(self, players):
        """East discards behind its master partner: the heart void is
        proven, the trump holding stays unknown (exemption applies)."""
        north = players["N"]
        completed = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )
        obs = _obs(
            north,
            [_c(Suit.SPADES, Rank.JACK)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
        )
        _, voids = north.cardplay._derive_tracking(obs)
        assert Suit.HEARTS in voids[Position.EAST]
        assert Suit.SPADES not in voids[Position.EAST]

    def test_ruff_records_led_suit_void(self, players):
        """A ruff is a non-follow too: West cutting a heart lead proves
        the heart void (and obviously proves nothing against trump)."""
        north = players["N"]
        completed = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.SEVEN)),
        )
        obs = _obs(
            north,
            [_c(Suit.DIAMONDS, Rank.ACE)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[completed],
        )
        _, voids = north.cardplay._derive_tracking(obs)
        assert Suit.HEARTS in voids[Position.WEST]
        assert Suit.SPADES not in voids[Position.WEST]


class TestOpponentCutExpected:
    """``_opponent_cut_expected`` — will an opponent still to play in this
    trick ruff it? True only when that opponent is proven void in the led
    suit, is not proven void in trump, and a trump is still unseen.
    """

    def _cut_expected(self, players, completed, current, hand):
        """Derive tracking from a built observation and ask the predicate."""
        north = players["N"]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=completed,
            current_trick=current,
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        return strat._opponent_cut_expected(obs, fallen, voids)

    # A prior trick proving East void in hearts but not in trump: East
    # discards a club behind its master partner West.
    def _east_hearts_void(self, players):
        return (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
        )

    def test_fires_for_suit_void_opponent_yet_to_play(self, players):
        assert self._cut_expected(
            players,
            [self._east_hearts_void(players)],
            (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),),
            [_c(Suit.HEARTS, Rank.QUEEN)],
        ) is True

    def test_silent_when_the_void_opponent_already_played(self, players):
        """West is the void seat here and has already discarded into the
        current trick — nobody left behind us can ruff."""
        # West discards behind its master partner East → ♥ void only.
        prior = (
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["S"], _c(Suit.HEARTS, Rank.NINE)),
            Play(players["W"], _c(Suit.CLUBS, Rank.SEVEN)),
        )
        current = (
            Play(players["E"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["S"], _c(Suit.HEARTS, Rank.EIGHT)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),
        )
        assert self._cut_expected(
            players, [prior], current, [_c(Suit.HEARTS, Rank.QUEEN)]
        ) is False

    def test_silent_when_the_cutter_is_proven_trumpless(self, players):
        trump_void = (
            Play(players["N"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["E"], _c(Suit.DIAMONDS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.SPADES, Rank.SEVEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.EIGHT)),
        )
        assert self._cut_expected(
            players,
            [self._east_hearts_void(players), trump_void],
            (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),),
            [_c(Suit.HEARTS, Rank.QUEEN)],
        ) is False

    def test_silent_when_no_trump_is_unseen(self, players):
        """Counting kills the prediction: four trumps fell and the seat
        holds the other four, so East cannot be sitting on one."""
        spades_round = (
            Play(players["N"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["E"], _c(Suit.SPADES, Rank.KING)),
            Play(players["S"], _c(Suit.SPADES, Rank.QUEEN)),
            Play(players["W"], _c(Suit.SPADES, Rank.TEN)),
        )
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.NINE),
            _c(Suit.SPADES, Rank.EIGHT),
            _c(Suit.SPADES, Rank.SEVEN),
            _c(Suit.HEARTS, Rank.QUEEN),
        ]
        assert self._cut_expected(
            players,
            [self._east_hearts_void(players), spades_round],
            (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),),
            hand,
        ) is False

    def test_silent_on_a_trump_lead(self, players):
        """No ruff exists when trump itself is led."""
        assert self._cut_expected(
            players,
            [self._east_hearts_void(players)],
            (Play(players["W"], _c(Suit.SPADES, Rank.EIGHT)),),
            [_c(Suit.SPADES, Rank.QUEEN)],
        ) is False

    def test_silent_when_only_the_partner_is_void(self, players):
        """A void partner ruffing is good news, not a threat."""
        partner_void = (
            Play(players["W"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["N"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["E"], _c(Suit.HEARTS, Rank.NINE)),
        )
        assert self._cut_expected(
            players,
            [partner_void],
            (Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),),
            [_c(Suit.HEARTS, Rank.QUEEN)],
        ) is False


# ---------------------------------------------------------------------------
# A no-trump round has no trump suit to track
# ---------------------------------------------------------------------------


class TestNoTrumpContract:
    """Nothing is trump under a ``NO_TRUMP`` contract, so every piece of
    trump reasoning must come back empty rather than treat ``NO_TRUMP``
    itself as a suit a seat could be void in or hold cards of.
    """

    #: A trick where East neither follows hearts nor holds a master partner
    #: — under a suit contract this is exactly what proves a trump void.
    @staticmethod
    def _east_compelled_discard(players):
        return (
            Play(players["N"], _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )

    def test_derive_tracking_records_no_trump_void(self, players):
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.CLUBS, Rank.ACE)],
            _contract(north, 100, TrumpVariant.NO_TRUMP),
            completed_tricks=[self._east_compelled_discard(players)],
        )
        _, voids = north.cardplay._derive_tracking(obs)
        # The led-suit void is real and still recorded.
        assert Suit.HEARTS in voids[Position.EAST]
        # voids maps seats to sets of *card* suits. NO_TRUMP is not one, so
        # no entry for it may appear — the whole set must be card suits.
        assert TrumpVariant.NO_TRUMP not in voids[Position.EAST]
        assert all(
            suit in (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)
            for void_suits in voids.values()
            for suit in void_suits
        )

    def test_the_same_history_does_prove_a_trump_void_under_a_suit_contract(
        self, players
    ):
        """The control: only the contract differs from the test above."""
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.CLUBS, Rank.ACE)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[self._east_compelled_discard(players)],
        )
        _, voids = north.cardplay._derive_tracking(obs)
        assert Suit.SPADES in voids[Position.EAST]

    def test_no_cut_is_ever_expected(self, players):
        """No trump exists, so no opponent can ruff anything."""
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.QUEEN)],
            _contract(north, 100, TrumpVariant.NO_TRUMP),
            completed_tricks=[self._east_compelled_discard(players)],
            current_trick=(Play(players["W"], _c(Suit.HEARTS, Rank.NINE)),),
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponent_cut_expected(obs, fallen, voids) is False

    def test_opponents_never_might_have_trump(self, players):
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.QUEEN)],
            _contract(north, 100, TrumpVariant.NO_TRUMP),
            completed_tricks=[self._east_compelled_discard(players)],
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            TrumpVariant.NO_TRUMP, fallen, voids, obs.hand
        ) is False


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
        assert Suit.SPADES in voids[Position.EAST]
        assert Suit.SPADES in voids[Position.WEST]
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
        assert Suit.SPADES in voids[Position.EAST]
        assert Position.WEST not in voids
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
        assert Suit.SPADES in voids[Position.SOUTH]
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
        spades = rules_for(Suit.SPADES)
        # ♥10's only higher card (♥A) has fallen → master.
        assert strat._is_master_card(_c(Suit.HEARTS, Rank.TEN), spades, fallen) is True
        # ♥K still has ♥10 out → not master.
        assert strat._is_master_card(_c(Suit.HEARTS, Rank.KING), spades, fallen) is False

    def test_master_check_respects_trump_vs_normal_order(self, strat):
        # The ladders live on the TrumpRules seam now; the master check
        # must still rank a trump 9 above the Ace and a plain 9 below it.
        # It is handed the round's rules object, so the two regimes are
        # two different ladders rather than one suit comparison.
        fallen = self._fallen(Suit.HEARTS, {Rank.JACK})
        # ♥ trump: once the Jack falls, the ♥9 is master (A is beneath it).
        assert strat._is_master_card(
            _c(Suit.HEARTS, Rank.NINE), rules_for(Suit.HEARTS), fallen
        ) is True
        # Plain ♥ (spades trump): J and A still out → the ♥9 is nowhere
        # near master on the plain ladder.
        assert strat._is_master_card(
            _c(Suit.HEARTS, Rank.NINE), rules_for(Suit.SPADES), fallen
        ) is False

    def test_team_winning_reads_the_led_suit_master(self, strat):
        # The helpers receive sealed observation records in production,
        # so they are exercised with (position, card) pairs directly.
        partner_master = (
            ObservedPlay(Position.SOUTH, _c(Suit.HEARTS, Rank.ACE)),
            ObservedPlay(Position.WEST, _c(Suit.HEARTS, Rank.KING)),
        )
        assert strat._is_team_winning_trick(partner_master) is True
        opponent_master = (
            ObservedPlay(Position.WEST, _c(Suit.HEARTS, Rank.ACE)),
            ObservedPlay(Position.SOUTH, _c(Suit.HEARTS, Rank.KING)),
        )
        assert strat._is_team_winning_trick(opponent_master) is False

    def test_strongest_card_with_trump_on_the_table(self, strat):
        plays = (
            ObservedPlay(Position.NORTH, _c(Suit.HEARTS, Rank.ACE)),
            ObservedPlay(Position.EAST, _c(Suit.HEARTS, Rank.KING)),
            ObservedPlay(Position.SOUTH, _c(Suit.SPADES, Rank.EIGHT)),
        )
        best = strat._get_strongest_card_in_trick(plays, Suit.SPADES)
        assert (best.suit, best.rank) == (Suit.SPADES, Rank.EIGHT)

    def test_strongest_card_without_trump(self, strat):
        plays = (
            ObservedPlay(Position.NORTH, _c(Suit.HEARTS, Rank.KING)),
            ObservedPlay(Position.EAST, _c(Suit.HEARTS, Rank.ACE)),
            ObservedPlay(Position.SOUTH, _c(Suit.DIAMONDS, Rank.ACE)),
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


# ---------------------------------------------------------------------------
# Per-mode play: one regime-neutral rule instead of three code paths
# ---------------------------------------------------------------------------


class TestRuffInferencePerMode:
    """``_opponents_might_have_trump`` answers the question the callers ask.

    The callers want to know "can what I play be cut?". At a suit contract
    that is a counting question about one suit. At all trump the answer is
    a flat ``False`` — there is no cross-suit cutting (§6.4) — and reading
    it off spades as a stand-in for "the trump suit" is nonsense there.
    """

    @staticmethod
    def _spades_all_fallen(players):
        """Two tricks that put every spade on the table."""
        first = (
            Play(players["N"], _c(Suit.SPADES, Rank.JACK)),
            Play(players["E"], _c(Suit.SPADES, Rank.NINE)),
            Play(players["S"], _c(Suit.SPADES, Rank.ACE)),
            Play(players["W"], _c(Suit.SPADES, Rank.TEN)),
        )
        second = (
            Play(players["S"], _c(Suit.SPADES, Rank.KING)),
            Play(players["W"], _c(Suit.SPADES, Rank.QUEEN)),
            Play(players["N"], _c(Suit.SPADES, Rank.EIGHT)),
            Play(players["E"], _c(Suit.SPADES, Rank.SEVEN)),
        )
        return [first, second]

    def test_all_trump_never_fears_a_cut(self, players):
        """Nothing can be cut at all trump, whatever has fallen."""
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.JACK)],
            _contract(north, 100, TrumpVariant.ALL_TRUMP),
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            TrumpVariant.ALL_TRUMP, fallen, voids, obs.hand
        ) is False

    def test_all_trump_answer_does_not_depend_on_the_spades_count(
        self, players
    ):
        """The spades proxy is retired: draining spades changes nothing."""
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.JACK)],
            _contract(north, 100, TrumpVariant.ALL_TRUMP),
            completed_tricks=self._spades_all_fallen(players),
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            TrumpVariant.ALL_TRUMP, fallen, voids, obs.hand
        ) is False

    def test_a_suit_contract_still_counts_its_trump(self, players):
        """The control: at a suit contract the counting is unchanged."""
        north = players["N"]
        clean = (
            Play(players["N"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["E"], _c(Suit.HEARTS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.HEARTS, Rank.EIGHT)),
        )
        obs = _obs(
            north,
            [_c(Suit.SPADES, Rank.JACK)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[clean],
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs.hand
        ) is True

    def test_a_suit_contract_stops_once_every_trump_has_fallen(self, players):
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.KING)],
            _contract(north, 100, Suit.SPADES),
            completed_tricks=self._spades_all_fallen(players),
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            Suit.SPADES, fallen, voids, obs.hand
        ) is False

    def test_no_trump_has_nothing_to_hold(self, players):
        north = players["N"]
        obs = _obs(
            north,
            [_c(Suit.HEARTS, Rank.ACE)],
            _contract(north, 100, TrumpVariant.NO_TRUMP),
        )
        strat = north.cardplay
        fallen, voids = strat._derive_tracking(obs)
        assert strat._opponents_might_have_trump(
            TrumpVariant.NO_TRUMP, fallen, voids, obs.hand
        ) is False


class TestLeadTopsItsOwnLadder:
    """A lead cashes the top of the *ladder*, never a hardcoded ace."""

    @staticmethod
    def _spades_all_fallen(players):
        return TestRuffInferencePerMode._spades_all_fallen(players)

    def test_all_trump_declarer_leads_the_unbeatable_jack(self, players):
        """The Context reproduction: spades drained, Jack + 7 of hearts.

        With the spades proxy in place the declarer read the table as
        "no opponent holds trump", emptied its winner search with the
        ``not is_trump`` filter — every card is trump at all trump — and
        conceded the 7 while holding the master Jack.
        """
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.HEARTS, Rank.SEVEN),
            _c(Suit.CLUBS, Rank.EIGHT),
        ]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, TrumpVariant.ALL_TRUMP),
            completed_tricks=self._spades_all_fallen(players),
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.JACK)

    def test_all_trump_lead_prefers_the_jack_over_an_ace(self, players):
        """At all trump an ace is only the third card of its ladder."""
        north = players["N"]
        clean = (
            Play(players["N"], _c(Suit.CLUBS, Rank.KING)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.CLUBS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.CLUBS, Rank.EIGHT)),
        )
        hand = [_c(Suit.SPADES, Rank.ACE), _c(Suit.HEARTS, Rank.JACK)]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, TrumpVariant.ALL_TRUMP),
            completed_tricks=[clean],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.JACK)

    def test_no_trump_lead_prefers_the_ace_over_a_jack(self, players):
        """The mirror: at no trump the ace tops every ladder."""
        north = players["N"]
        clean = (
            Play(players["N"], _c(Suit.CLUBS, Rank.KING)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.CLUBS, Rank.QUEEN)),
            Play(players["W"], _c(Suit.CLUBS, Rank.EIGHT)),
        )
        hand = [_c(Suit.SPADES, Rank.ACE), _c(Suit.HEARTS, Rank.JACK)]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, TrumpVariant.NO_TRUMP),
            completed_tricks=[clean],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.ACE)

    def test_a_suit_contract_lead_is_unchanged(self, players):
        """The control: a plain ace still tops a plain suit."""
        north = players["N"]
        both_void = (
            Play(north, _c(Suit.HEARTS, Rank.ACE)),
            Play(players["E"], _c(Suit.CLUBS, Rank.SEVEN)),
            Play(players["S"], _c(Suit.HEARTS, Rank.KING)),
            Play(players["W"], _c(Suit.DIAMONDS, Rank.SEVEN)),
        )
        hand = [_c(Suit.DIAMONDS, Rank.ACE), _c(Suit.CLUBS, Rank.JACK)]
        obs = _obs(
            north,
            hand,
            _contract(north, 100, Suit.SPADES),
            completed_tricks=[both_void],
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.DIAMONDS, Rank.ACE)


class TestOpeningLeadPerMode:
    """The opening lead reads the regime, not the presence of trump cards."""

    def test_no_trump_declarer_cashes_the_ace_of_its_longest_suit(
        self, players
    ):
        """No trump to lead is not a reason to concede trick 1.

        Nothing is trump at no trump, so the declaring branch's
        ``if trump_cards:`` never fired and the ladder fell through to
        the cheapest card — handing the opponents the opening trick.
        """
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.HEARTS, Rank.QUEEN),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(
            north, hand, _contract(north, 100, TrumpVariant.NO_TRUMP)
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.ACE)

    def test_all_trump_declarer_leads_the_top_of_its_longest_suit(
        self, players
    ):
        """Length gives the lead continuation; the ladder names the card.

        A suit-blind global maximum on ``rank_in_suit`` would pick either
        Jack — every suit ranks alike at all trump. The longest suit is
        what has cards behind the lead.
        """
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.HEARTS, Rank.NINE),
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.CLUBS, Rank.JACK),
        ]
        obs = _obs(
            north, hand, _contract(north, 100, TrumpVariant.ALL_TRUMP)
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.HEARTS, Rank.JACK)

    def test_all_trump_defender_leads_the_top_of_its_shortest_suit(
        self, players
    ):
        """The defender branch generalises the same way — top, not ace."""
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.HEARTS, Rank.QUEEN),
        ]
        obs = _obs(
            north,
            hand,
            _contract(players["E"], 100, TrumpVariant.ALL_TRUMP),
        )
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.JACK)

    def test_a_suit_contract_opening_is_unchanged(self, players):
        """The control: the declarer still opens on its strongest trump."""
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.SPADES, Rank.ACE),
            _c(Suit.HEARTS, Rank.KING),
            _c(Suit.DIAMONDS, Rank.EIGHT),
        ]
        obs = _obs(north, hand, _contract(north, 80, Suit.SPADES))
        result = north.cardplay.choose_card(obs).card
        assert (result.suit, result.rank) == (Suit.SPADES, Rank.JACK)


class TestPerModeRulesCiteTheirRegime:
    """A rule that fired *because* of the regime names the §9.2 knob."""

    @pytest.mark.parametrize(
        "mode", [TrumpVariant.NO_TRUMP, TrumpVariant.ALL_TRUMP]
    )
    def test_a_suitless_opening_cites_extended_trump_choices(
        self, players, mode
    ):
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(north, hand, _contract(north, 100, mode))
        citations = north.cardplay.choose_card(obs).rationale.citations
        assert [c.knob for c in citations] == ["extended_trump_choices"]

    def test_a_suit_contract_needs_no_permission_and_cites_nothing(
        self, players
    ):
        north = players["N"]
        hand = [
            _c(Suit.SPADES, Rank.JACK),
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(north, hand, _contract(north, 100, Suit.SPADES))
        assert north.cardplay.choose_card(obs).rationale.citations == ()

    def test_the_detail_names_the_regime_it_played_under(self, players):
        north = players["N"]
        hand = [
            _c(Suit.HEARTS, Rank.JACK),
            _c(Suit.HEARTS, Rank.ACE),
            _c(Suit.CLUBS, Rank.SEVEN),
        ]
        obs = _obs(
            north, hand, _contract(north, 100, TrumpVariant.ALL_TRUMP)
        )
        detail = north.cardplay.choose_card(obs).rationale.detail
        assert detail.startswith("all trump:")
