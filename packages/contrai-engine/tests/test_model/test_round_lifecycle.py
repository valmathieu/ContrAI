"""End-to-end coverage of the full ``Round`` lifecycle.

``deal_cards`` -> ``manage_bidding`` -> ``play_all_tricks`` ->
``calculate_round_scores`` (or ``handle_failed_contract`` on an all-pass)
is exercised only in fragments elsewhere (``test_round.py`` drives the
play-state loop from hand-built mid-round state; ``test_round_scoring.py``
feeds ``score_round`` synthesised tricks directly). This file drives the
whole path from a *dealt* deck through to a scored round, so the pieces are
proven to actually fit together.

Every scenario is a **fully stacked deck** — no shuffling, no RNG — built
backwards from the hands each seat needs to hold, via :func:`_stack_deck`.
Four :class:`AiPlayer` seats and no view, so every view-dependent branch in
``Round`` takes its ``None`` path, and the bidding/card-play come from
:class:`RuleBasedBiddingStrategy` / :class:`RuleBasedCardPlayStrategy`
(``AiPlayer``'s defaults) end to end.

The happy-path and belote scenarios pin the resulting score with a mix of
a rule-derived invariant (the exact total ``score_round`` guarantees for a
*made*, un-doubled, non-sweep numeric contract: ``contract_value + 162``,
plus 20 when a team holds the belote) and a regression pin on the concrete
per-team split. The bidding table's outcome is derived by hand in the
docstrings below and asserted exactly; the card-by-card play is a function
of the stacked hands plus the seeded RNG the strategy breaks ties with (see
``rule_based/card_play.py``'s ``RuleBasedCardPlayStrategy``, and the
``pinned_rng`` fixture in ``conftest.py`` that pins the draw), but tracing
that decision tree by hand across 8 tricks is impractical — the concrete
numbers are pinned from an actual reproducible run and guarded by the
invariant, so a scoring-rule regression still fails loudly even if the
exact split ever drifts.
"""

from __future__ import annotations

from contrai_core.card import Card
from contrai_core.deck import Deck
from contrai_core.team_side import TeamSide
from contrai_core.types import Rank, Suit

from contrai_engine.model.round import Round


def _stack_deck(hands: dict[str, list[Card]]) -> Deck:
    """Build a ``Deck`` whose ``deal()`` reproduces exactly ``hands``.

    ``Deck.deal`` hands out cards in a fixed 3-2-3 pattern per seat, each
    seat's three batches read from three *disjoint* slices of
    ``deck.cards`` (see ``Deck.deal``): for seat index ``i`` (0=N, 1=E,
    2=S, 3=W, matching the N/E/S/W seating order the tests below use)
    the batches are ``cards[i*3:i*3+3]``, ``cards[i*2+12:i*2+14]``, and
    ``cards[i*3+20:i*3+23]``. This helper inverts that layout: given the
    8-card hand each seat should end up holding (in the desired final
    order), it writes each hand's cards into the matching three slots so
    ``deck.deal([N, E, S, W])`` reconstructs those exact hands.

    Args:
        hands: Mapping of seat letter ("N"/"E"/"S"/"W") to the 8-card
            hand that seat should be dealt, in the exact order the dealt
            hand should hold them.

    Returns:
        A fresh ``Deck`` stacked for the inverse deal. ``deal()`` still
        performs its own 32-card / 4-player validation, so a malformed
        ``hands`` mapping fails there if it slips past the assertions
        below.
    """

    seats = ("N", "E", "S", "W")
    deck_cards: list[Card | None] = [None] * 32
    for i, seat in enumerate(seats):
        hand = hands[seat]
        assert len(hand) == 8, f"seat {seat} needs exactly 8 cards, got {len(hand)}"
        batch1, batch2, batch3 = hand[0:3], hand[3:5], hand[5:8]
        deck_cards[i * 3 : i * 3 + 3] = batch1
        deck_cards[i * 2 + 12 : i * 2 + 14] = batch2
        deck_cards[i * 3 + 20 : i * 3 + 23] = batch3

    assert all(card is not None for card in deck_cards), "every slot must be filled"
    assert len(set(deck_cards)) == 32, "a stacked deck must hold 32 distinct cards"

    deck = Deck()
    deck.cards = deck_cards
    return deck


# ---------------------------------------------------------------------------
# Happy path: one stacked deal, a numeric contract reached and made.
# ---------------------------------------------------------------------------
#
# Hand design (worked by hand against ``RuleBasedBiddingStrategy``'s
# ``BIDDING_TABLE`` in ``rule_based/bidding.py``):
#
#   N: Spade J, 9, 7 (3 trumps, both J and 9 - the two best trumps);
#      Hearts A, 10 and Diamonds A, 10 (2 external aces, each with a
#      suit-mate so the "ten with support" bonus fires); Clubs 7 filler.
#      -> trump strength = 2 (jack_and_nine) + (3-3+0) = 2.
#      -> estimated tricks = 2 (trump) + 1+1 (Hearts A, 10) + 1+1
#         (Diamonds A, 10) = 6.
#      Walking the table: 80/90 clear (aces>=1, tricks>=4); 100/110 clear
#      (aces>=2, tricks>=5); 120/130 need aces>=3 - N only has 2 -> stop.
#      Best reachable = 110 Spades. No other suit has >=3 cards, so every
#      other suit evaluates to 0 and can't compete.
#   S (N's partner): Spade 8, 10, A (3 more trumps, but neither the J nor
#      the 9 - S's own spade evaluation never clears row 80's
#      ``jack_or_nine`` gate) plus Clubs 8/9, Hearts 7, Diamonds 7/8 - no
#      ace outside spades anywhere in S's hand. ``_support_partner_bid``'s
#      contribution (external ace +10, trump J/9 complement +10) is
#      therefore 0, so S passes instead of raising N's bid.
#   E, W: hold no spades at all (N + S account for all 8 trumps between
#      them) and few enough aces/tens that both
#      ``_should_double`` (needs estimated trump tricks * 20 > 162-110 =
#      52, i.e. >=3 estimated tricks) and their own opening bid stay
#      under threshold. Verified by the deal-order bidding assertions
#      below; a stacking mistake big enough to change this would flip
#      ``contract`` away from N/110/Spades and fail loudly there.
#
# N/S hold 6 of the 8 trumps (missing only the Queen and King, the two
# lowest-ranked trumps on the trump ladder - see
# ``rules_for(trump).rank_in_suit``), so the contract is
# expected to be comfortably made; the exact card-by-card play (and hence
# the precise score split) is pinned from an actual run - see the module
# docstring.


class TestFullRoundLifecycleHappyPath:
    """Deal -> bid -> play -> score, all the way through, contract made."""

    HANDS = {
        "N": [
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.SPADES, Rank.NINE),
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.TEN),
            Card(Suit.DIAMONDS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.TEN),
            Card(Suit.CLUBS, Rank.SEVEN),
        ],
        "E": [
            Card(Suit.SPADES, Rank.KING),
            Card(Suit.HEARTS, Rank.EIGHT),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.NINE),
            Card(Suit.CLUBS, Rank.TEN),
            Card(Suit.CLUBS, Rank.JACK),
        ],
        "S": [
            Card(Suit.SPADES, Rank.EIGHT),
            Card(Suit.SPADES, Rank.TEN),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.CLUBS, Rank.EIGHT),
            Card(Suit.CLUBS, Rank.NINE),
            Card(Suit.HEARTS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.SEVEN),
            Card(Suit.DIAMONDS, Rank.EIGHT),
        ],
        "W": [
            Card(Suit.SPADES, Rank.QUEEN),
            Card(Suit.HEARTS, Rank.KING),
            Card(Suit.DIAMONDS, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.CLUBS, Rank.QUEEN),
            Card(Suit.CLUBS, Rank.KING),
            Card(Suit.CLUBS, Rank.ACE),
        ],
    }

    def test_full_lifecycle_reaches_a_made_contract(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["W"], deck=_stack_deck(self.HANDS), round_number=1
        )

        # --- Deal ---------------------------------------------------
        round_.deal_cards()
        # RED guard: a mis-stacked deck fails loudly right here, before
        # bidding or play ever runs on the wrong hands.
        for seat, expected in self.HANDS.items():
            assert list(players[seat].hand) == expected, f"seat {seat} mis-dealt"
        assert round_.deck.cards == []  # the deck hands out every card

        # --- Bidding --------------------------------------------------
        contract = round_.manage_bidding(None)

        assert contract is not None, "stacked hand failed to open the bidding"
        assert contract.player is players["N"]
        assert contract.suit == Suit.SPADES
        assert contract.value == 110
        assert contract.double is False
        # No single seat holds both King and Queen of trump in this deal
        # (King -> E, Queen -> W) - the happy path is deliberately
        # belote-free so its score reduces to the plain numeric formula.
        assert round_.belote_holder is None

        # --- Play -------------------------------------------------------
        round_.play_all_tricks(None)

        assert round_.play_state.is_terminal()
        assert len(round_.play_state.completed_tricks) == 8
        # Every completed trick has a winner, and each winner has a side.
        assert len(round_.play_state.trick_winners) == 8
        assert sum(round_.play_state.trick_counts_by_side.values()) == 8
        for seat in ("N", "E", "S", "W"):
            assert len(players[seat].hand) == 0
        # The trick-return ritual: every played card lands back in the
        # deck as each trick completes (see ``Round.play_trick``), so by
        # the time all 8 tricks are done the deck is whole again.
        assert len(round_.deck.cards) == 32
        assert len(set(round_.deck.cards)) == 32

        # --- Scoring ------------------------------------------------
        scores = round_.calculate_round_scores()

        assert round_.contract_made is True
        assert round_.unannounced_slam is None
        # Rule-derived invariant (scoring.py): a made, un-doubled numeric
        # contract with no unannounced Slam always has both scores sum to
        # contract_value + 162 (152 card points + the 10-point
        # last-trick bonus) - the two teams simply split the one pile,
        # and no belote
        # is in play here.
        assert sum(scores.values()) == contract.value + 162
        assert scores[TeamSide.NS] > scores[TeamSide.EW]
        # Regression pin: the concrete split observed from this exact
        # stacked deal, under the RNG the ``pinned_rng`` fixture seeds -
        # the card-play strategy draws from it to break ties nothing else
        # separates. Re-run twice to confirm before trusting a change to
        # these numbers reflects a real scoring-rule change and not a
        # stacking edit.
        assert scores == {TeamSide.NS: 258, TeamSide.EW: 14}


# ---------------------------------------------------------------------------
# Belote: King + Queen of trump concentrated in one hand.
# ---------------------------------------------------------------------------
#
# Same shape as the happy path (N opens 110 Spades, S support-passes, E/W
# pass), but N's hand now also holds the Queen and King of trump, so N/S
# hold every trump (8 of 8) and the Belote/Rebelote pair besides:
#
#   N: Spade J, Q, K, 9, 7 (5 trumps, both J and 9, plus the pair) +
#      Hearts A + Diamonds A + Clubs 7.
#      -> trump strength = 2 (jack_and_nine) + (5-3+0) = 4.
#      -> estimated tricks = 4 (trump) + 1 (Hearts A) + 1 (Diamonds A)
#         = 6.
#      Table walk: 80/90 clear; 100/110 clear (aces>=2, tricks>=5);
#      120/130 need aces>=3 (N has 2) -> stop; 140/150/160 additionally
#      need aces>=3 too, so ``has_belote`` alone doesn't unlock them.
#      Best reachable = 110 Spades, same value as the happy path but for
#      a different reason (extra trump length standing in for external
#      aces on the ceiling rows).
#   S: Spade 8, 10, A - the 3 remaining trumps - plus Hearts 7/8,
#      Diamonds 7/8, Clubs 8: no external ace, so (as in the happy path)
#      the support contribution is 0 and S passes.
#   E, W: no spades at all; kept below both the double threshold (needs
#      estimated trump tricks * 20 > 52) and their own opening threshold,
#      verified below.


class TestFullRoundLifecycleBelote:
    """A second stacked deal: the King + Queen of trump share a hand."""

    HANDS = {
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

    def test_full_lifecycle_tracks_belote_holder_and_bonus(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["W"], deck=_stack_deck(self.HANDS), round_number=1
        )

        # --- Deal ---------------------------------------------------
        round_.deal_cards()
        for seat, expected in self.HANDS.items():
            assert list(players[seat].hand) == expected, f"seat {seat} mis-dealt"

        # --- Bidding --------------------------------------------------
        contract = round_.manage_bidding(None)

        assert contract is not None, "stacked hand failed to open the bidding"
        assert contract.player is players["N"]
        assert contract.suit == Suit.SPADES
        assert contract.value == 110
        assert contract.double is False

        # N holds both King and Queen of trump at deal time - the belote
        # holder is snapshotted as soon as the contract is established.
        assert round_.belote_holder is players["N"]
        # Nothing has been played yet, so neither leg of the
        # belote/rebelote announcement has fired.
        assert round_.belote_state == {}

        # --- Play -------------------------------------------------------
        round_.play_all_tricks(None)

        assert round_.play_state.is_terminal()
        assert len(round_.play_state.completed_tricks) == 8
        for seat in ("N", "E", "S", "W"):
            assert len(players[seat].hand) == 0

        # By the end of the round N has necessarily played every card,
        # King and Queen of trump included, so the belote state machine
        # must have advanced all the way to "rebelote" for N.
        assert round_.belote_state == {players["N"]: "rebelote"}

        # --- Scoring ------------------------------------------------
        scores = round_.calculate_round_scores()

        assert round_.contract_made is True
        # Rule-derived invariant: made, un-doubled, non-sweep numeric
        # contract sums to contract_value + 162, *plus* the 20-point
        # belote bonus layered on top of the pile split (scoring.py
        # credits it to the holder's team independent of who wins the
        # round or which cards capture the K/Q).
        assert sum(scores.values()) == contract.value + 162 + 20
        assert scores[TeamSide.NS] > scores[TeamSide.EW]
        # Regression pin: the concrete split observed from this exact
        # stacked deal, under the RNG the ``pinned_rng`` fixture seeds.
        assert scores == {TeamSide.NS: 278, TeamSide.EW: 14}


# ---------------------------------------------------------------------------
# All-pass: every seat too weak to open, no contract, redeal-ready state.
# ---------------------------------------------------------------------------
#
# Every seat holds exactly two cards of each suit. ``BIDDING_TABLE``'s most
# lenient row (80) already requires ``trump_min=3``, so a suit no seat ever
# holds more than 2 of can never clear a single row - every seat's
# ``_evaluate_suit_as_trump`` returns ``contract=0`` for all four suits
# regardless of rank quality, and ``_choose_open_bid`` falls through to
# ``PassBid``. The four Aces, Jacks, Kings, and Nines are additionally
# spread one per seat per suit (no seat holds a suit's Ace together with
# its Ten, and no seat holds a suit's Jack together with its Nine) so no
# seat's estimated-tricks total can accidentally reach the ``tricks_min=8``
# Slam/Solo-Slam gate either (whose ``trump_min=0`` is the one row this
# deal's low trump counts don't rule out on their own).


class TestFullRoundLifecycleAllPass:
    """A third stacked deal: four hands too weak for anyone to open."""

    HANDS = {
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

    def test_all_pass_leaves_the_round_redeal_ready(self, players):
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["W"], deck=_stack_deck(self.HANDS), round_number=1
        )

        # --- Deal ---------------------------------------------------
        round_.deal_cards()
        for seat, expected in self.HANDS.items():
            assert list(players[seat].hand) == expected, f"seat {seat} mis-dealt"
        assert round_.deck.cards == []

        # --- Bidding --------------------------------------------------
        contract = round_.manage_bidding(None)

        assert contract is None, "a weak stacked hand still opened the bidding"
        assert round_.contract is None
        assert round_.belote_holder is None

        # --- Redeal-ready state -----------------------------------------
        scores = round_.handle_failed_contract()

        # Every card returns to the deck (8 per seat x 4 seats).
        assert len(round_.deck.cards) == 32
        assert len(set(round_.deck.cards)) == 32
        for seat in ("N", "E", "S", "W"):
            assert len(players[seat].hand) == 0

        # Zero scores for both teams - no contract means nothing was at
        # stake, not merely "no score attribute published".
        assert scores == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert round_.round_scores == scores
        # calculate_round_scores was never called on this path - the
        # made/failed and unannounced-Slam signals stay at their
        # pre-round default.
        assert round_.contract_made is None
        assert round_.unannounced_slam is None
