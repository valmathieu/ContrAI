"""Tests for the TrumpRules seam.

Pins the four per-card tables against literals and locks the structural
guarantees the rest of the domain leans on: singleton identity, the
sealed hierarchy, and the all-trump firewall living in
:func:`rules_for`.
"""

import pytest

from contrai_core import (
    Card,
    NoTrumpRules,
    Rank,
    SingleSuitRules,
    Suit,
    TrumpRules,
    TrumpVariant,
    rules_for,
)

#: All 32 physical cards.
ALL_CARDS = [Card(suit, rank) for suit in Suit for rank in Rank]

#: The trump options the seam implements today (all-trump raises).
IMPLEMENTED_TRUMPS = [*Suit, TrumpVariant.NO_TRUMP, None]

# The expected tables, written out as literals so a slip in the source
# tables cannot silently agree with itself.
PLAIN_POINTS = {
    Rank.SEVEN: 0, Rank.EIGHT: 0, Rank.NINE: 0, Rank.JACK: 2,
    Rank.QUEEN: 3, Rank.KING: 4, Rank.TEN: 10, Rank.ACE: 11,
}
TRUMP_POINTS = {
    Rank.SEVEN: 0, Rank.EIGHT: 0, Rank.NINE: 14, Rank.JACK: 20,
    Rank.QUEEN: 3, Rank.KING: 4, Rank.TEN: 10, Rank.ACE: 11,
}
PLAIN_ORDER = {
    Rank.SEVEN: 0, Rank.EIGHT: 1, Rank.NINE: 2, Rank.JACK: 3,
    Rank.QUEEN: 4, Rank.KING: 5, Rank.TEN: 6, Rank.ACE: 7,
}
TRUMP_ORDER = {
    Rank.SEVEN: 0, Rank.EIGHT: 1, Rank.QUEEN: 2, Rank.KING: 3,
    Rank.TEN: 4, Rank.ACE: 5, Rank.NINE: 6, Rank.JACK: 7,
}


class TestRulesFor:
    """Resolution: seven keys, five singletons, one raise."""

    @pytest.mark.parametrize("suit", Suit)
    def test_suit_contract_resolves_to_its_singleton(self, suit):
        rules = rules_for(suit)
        assert isinstance(rules, SingleSuitRules)
        assert rules.suit is suit
        # Same key, same object — the instances are shared, not minted.
        assert rules_for(suit) is rules

    def test_no_trump_and_none_share_one_singleton(self):
        # No contract yet and an explicit no-trump contract play by the
        # same card rules, so they must resolve to the very same object.
        assert isinstance(rules_for(None), NoTrumpRules)
        assert rules_for(None) is rules_for(TrumpVariant.NO_TRUMP)

    def test_all_trump_raises(self):
        # The firewall: all-trump must never quietly play as no-trump.
        with pytest.raises(NotImplementedError, match="All-trump"):
            rules_for(TrumpVariant.ALL_TRUMP)

    def test_distinct_suits_get_distinct_rules(self):
        assert rules_for(Suit.SPADES) is not rules_for(Suit.HEARTS)


class TestSealing:
    def test_out_of_module_subclass_is_rejected(self):
        with pytest.raises(TypeError, match="sealed"):
            class Rogue(TrumpRules):  # noqa: F811 — the point is the raise
                pass

    def test_leaves_are_frozen(self):
        with pytest.raises(AttributeError):
            rules_for(Suit.SPADES).suit = Suit.HEARTS


class TestTables:
    """Literal pins of the four tables through the public API."""

    @pytest.mark.parametrize("rank", Rank)
    def test_plain_points_and_order(self, rank):
        rules = rules_for(None)
        card = Card(Suit.HEARTS, rank)
        assert rules.points(card) == PLAIN_POINTS[rank]
        assert rules.rank_in_suit(card) == PLAIN_ORDER[rank]

    @pytest.mark.parametrize("rank", Rank)
    def test_trump_points_and_order(self, rank):
        rules = rules_for(Suit.HEARTS)
        card = Card(Suit.HEARTS, rank)
        assert rules.points(card) == TRUMP_POINTS[rank]
        assert rules.rank_in_suit(card) == TRUMP_ORDER[rank]

    @pytest.mark.parametrize("rank", Rank)
    def test_off_trump_suit_uses_the_plain_tables(self, rank):
        rules = rules_for(Suit.HEARTS)
        card = Card(Suit.CLUBS, rank)
        assert rules.points(card) == PLAIN_POINTS[rank]
        assert rules.rank_in_suit(card) == PLAIN_ORDER[rank]


class TestFullDeckCoverage:
    """Every implemented regime answers for all 32 physical cards."""

    @pytest.mark.parametrize("trump", IMPLEMENTED_TRUMPS)
    def test_every_card_has_points_and_rank(self, trump):
        rules = rules_for(trump)
        for card in ALL_CARDS:
            assert isinstance(rules.points(card), int)
            assert isinstance(rules.rank_in_suit(card), int)
            assert isinstance(rules.is_trump(card.suit), bool)


class TestTrickRank:
    """The led-suit-aware competition rank."""

    def test_trump_card_competes_on_the_trump_scale(self):
        rules = rules_for(Suit.SPADES)
        rank = rules.trick_rank(Card(Suit.SPADES, Rank.JACK), Suit.HEARTS)
        assert rank == (1, TRUMP_ORDER[Rank.JACK])

    def test_led_suit_card_competes_on_the_plain_scale(self):
        rules = rules_for(Suit.SPADES)
        rank = rules.trick_rank(Card(Suit.HEARTS, Rank.ACE), Suit.HEARTS)
        assert rank == (0, PLAIN_ORDER[Rank.ACE])

    def test_off_suit_non_trump_cannot_take_the_trick(self):
        rules = rules_for(Suit.SPADES)
        assert rules.trick_rank(Card(Suit.CLUBS, Rank.ACE), Suit.HEARTS) is None

    def test_any_trump_outranks_every_led_suit_card(self):
        # The lexicographic encoding: the lowest trump still beats the
        # highest led-suit card.
        rules = rules_for(Suit.SPADES)
        low_trump = rules.trick_rank(Card(Suit.SPADES, Rank.SEVEN), Suit.HEARTS)
        top_led = rules.trick_rank(Card(Suit.HEARTS, Rank.ACE), Suit.HEARTS)
        assert low_trump > top_led

    def test_trump_led_trick_ranks_trumps_on_the_trump_scale(self):
        rules = rules_for(Suit.SPADES)
        nine = rules.trick_rank(Card(Suit.SPADES, Rank.NINE), Suit.SPADES)
        ace = rules.trick_rank(Card(Suit.SPADES, Rank.ACE), Suit.SPADES)
        assert nine > ace  # 9 outranks Ace on the trump scale

    def test_no_trump_only_the_led_suit_competes(self):
        rules = rules_for(TrumpVariant.NO_TRUMP)
        led = rules.trick_rank(Card(Suit.HEARTS, Rank.SEVEN), Suit.HEARTS)
        off = rules.trick_rank(Card(Suit.SPADES, Rank.ACE), Suit.HEARTS)
        assert led == (0, PLAIN_ORDER[Rank.SEVEN])
        assert off is None


class TestBeloteSuits:
    @pytest.mark.parametrize("suit", Suit)
    def test_suit_contract_scores_belote_in_its_trump(self, suit):
        assert rules_for(suit).belote_suits == (suit,)

    def test_no_trump_has_no_belote(self):
        assert rules_for(TrumpVariant.NO_TRUMP).belote_suits == ()
        assert rules_for(None).belote_suits == ()


class TestHigherRanks:
    def test_plain_ladder(self):
        rules = rules_for(None)
        assert rules.higher_ranks(Rank.KING, Suit.HEARTS) == (
            Rank.TEN,
            Rank.ACE,
        )

    def test_trump_ladder(self):
        rules = rules_for(Suit.HEARTS)
        assert rules.higher_ranks(Rank.ACE, Suit.HEARTS) == (
            Rank.NINE,
            Rank.JACK,
        )

    def test_off_trump_suit_uses_the_plain_ladder(self):
        rules = rules_for(Suit.HEARTS)
        assert rules.higher_ranks(Rank.ACE, Suit.CLUBS) == ()

    def test_top_of_ladder_has_nothing_above(self):
        assert rules_for(Suit.HEARTS).higher_ranks(Rank.JACK, Suit.HEARTS) == ()
        assert rules_for(None).higher_ranks(Rank.ACE, Suit.CLUBS) == ()

    @pytest.mark.parametrize("rank", Rank)
    def test_ladders_agree_with_rank_in_suit(self, rank):
        # The ladder and the order table are two spellings of one scale;
        # a rank's betters are exactly the ranks with a higher order index.
        rules = rules_for(Suit.HEARTS)
        for suit in (Suit.HEARTS, Suit.CLUBS):
            expected = tuple(
                sorted(
                    (
                        other
                        for other in Rank
                        if rules.rank_in_suit(Card(suit, other))
                        > rules.rank_in_suit(Card(suit, rank))
                    ),
                    key=lambda r: rules.rank_in_suit(Card(suit, r)),
                )
            )
            assert rules.higher_ranks(rank, suit) == expected
