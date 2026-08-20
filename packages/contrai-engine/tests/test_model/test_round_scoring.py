"""Tests for round scoring — ``Round.calculate_round_scores`` and the
underlying pure :func:`contrai_engine.model.round.scoring.score_round`.

The scoring rules come from ``contree-domain.md`` §6.6, §7: the numeric
(80-180) share-the-pile path, the unannounced-Slam 250 / 500 substitute,
the doubled/redoubled winner-takes-all path, and the symmetric Slam /
Solo Slam grid — with the Belote (+20 per pair) bonus layered onto every
shape for the team *holding* K + Q, up to four pairs under all trump.

These build a ``Round`` directly and seed its authoritative
``play_state`` with synthesised four-play tricks via the bare
(unvalidated) :class:`contrai_core.PlayState` constructor — the scoring
path reads ``contract`` / ``play_state`` / ``belote_pairs`` and nothing
else — then assert on the published result attributes (``round_scores``
/ ``contract_made`` / ``unannounced_slam``). Each fixture self-asserts
the play state's derived ``trick_winners`` before any scoring assertion,
so a mis-stacked trick fails loudly at construction rather than skewing
a total. The shared ``players`` fixture lives in ``conftest.py``.
"""

from __future__ import annotations

import pytest

from contrai_core.bid import ContractBid, SlamLevel
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.deck import Deck
from contrai_core.play import Play, PlayState
from contrai_core.rule_config import RuleConfig
from contrai_core.rules import rules_for
from contrai_core.team_side import TeamSide
from contrai_core.types import Rank, Suit

from contrai_engine.model.round import Round, UnannouncedSlam
from contrai_engine.model.round.components import Mark, marked_total
from contrai_engine.model.round.scoring import (
    RoundScore,
    score_round,
    sweep_substitute,
)

_ORDER = ("N", "E", "S", "W")


def _contract(player, value, suit):
    return Contract(ContractBid(player, value, suit))


def _stack_trick(players_dict, winner_seat, trump):
    """Four zero-point plays handing the trick to ``winner_seat``.

    The winner leads the trump seven — the only trump in the trick, so
    it wins under any suit contract — while the other three seats
    discard 0-point cards of the two remaining off suits. Every card is
    a 7 or an 8, worth 0 on both scales, so a stacked trick never moves
    a card-point total.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        winner_seat: seat letter that must win the trick.
        trump: the contract's trump suit (a real card suit).

    Returns:
        A tuple of four :class:`Play` records, winner leading.
    """
    winner = players_dict[winner_seat]
    others = [players_dict[s] for s in _ORDER if s != winner_seat]
    off_suits = [s for s in Suit if s is not trump]
    return (
        Play(winner, Card(trump, Rank.SEVEN)),
        Play(others[0], Card(off_suits[0], Rank.SEVEN)),
        Play(others[1], Card(off_suits[0], Rank.EIGHT)),
        Play(others[2], Card(off_suits[1], Rank.SEVEN)),
    )


def _seed_play_state(round_, players_dict, contract, plays):
    """Attach a bare PlayState carrying ``plays`` to ``round_``.

    The bare constructor performs no validation, so the synthesised
    mid-round history (empty hands, stacked tricks, repeated filler
    cards) is injectable directly — exactly the seam the core provides
    for tests and search forks.

    The state is seeded with ``round_.rules``, mirroring what the real
    ``play_all_tricks`` / ``play_trick`` do: a play state carrying a
    different ruleset from its own round is a state the engine cannot
    produce, and letting the double build one would hide a §9.6
    regression the moment the scoring knobs land.
    """
    order = tuple(players_dict[s] for s in _ORDER)
    round_.play_state = PlayState(
        contract=contract,
        players=order,
        hands=((), (), (), ()),
        plays=tuple(plays),
        rules=round_.rules,
    )


# ---------------------------------------------------------------------------
# Slam / Solo Slam scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# Tests below build a Round directly and seed the minimal state the
# scoring path reads:
#   - ``self.contract``    — drives base / multiplier / family check.
#   - ``self.play_state``  — completed tricks (card points) and their
#     winners (team trick counts, Solo Slam's personal tally, the
#     last-trick bonus).
#
# The stacked tricks are all zero-point, so Slam-family assertions read
# the grid amounts alone.


def _slam_round(
    players_dict,
    *,
    contract,
    trick_winners,
    rules=None,
):
    """Build a Round whose play state yields the given trick winners.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a Contract bound to one of the players.
        trick_winners: ordered list of seat letters — one per completed
            trick. Each entry is the player who wins that trick (each
            stacked trick is zero-point filler).
        rules: optional table ruleset. Scoring reads it off the round, so
            a case that varies a §9.6 knob varies it here rather than at
            the ``score_round`` call — which keeps the play state and the
            scorer on the same ruleset by construction.

    Returns:
        Round with ``contract`` and ``play_state`` populated.
    """
    order = [players_dict[s] for s in _ORDER]
    round_ = Round(
        order, dealer=players_dict["N"], deck=None, round_number=1, rules=rules
    )
    round_.contract = contract

    plays = []
    for seat in trick_winners:
        plays.extend(_stack_trick(players_dict, seat, contract.suit))
    _seed_play_state(round_, players_dict, contract, plays)

    # Self-check: the stacked piles decide exactly the winners intended.
    assert list(round_.play_state.trick_winners) == [
        players_dict[s] for s in trick_winners
    ]
    return round_


class TestScoreRoundResult:
    """The pure ``score_round`` returns a ``RoundScore`` without mutating
    the round — ``calculate_round_scores`` is the thin publishing wrapper."""

    def test_score_round_returns_result_without_mutating(self, players):
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        result = score_round(round_)
        assert isinstance(result, RoundScore)
        assert result.scores[TeamSide.NS] == 500
        assert result.contract_made is True
        assert result.unannounced_slam is None
        # Pure: the round's result attributes are untouched until the
        # wrapper publishes them.
        assert round_.round_scores == {}
        assert round_.contract_made is None

    def test_wrapper_publishes_result_onto_the_round(self, players):
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores is round_.round_scores
        assert round_.contract_made is True

    def test_scoring_reads_the_ruleset_off_the_round(self, players):
        """The scorer takes no ``rules`` argument — a round is only ever
        scored under the ruleset it was played under. No §9.6 knob is
        consulted yet, so an unusual ruleset still scores the classic grid."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        configured = _slam_round(
            players,
            contract=contract,
            trick_winners=["N"] * 8,
            rules=RuleConfig(target_score=1000),
        )
        default = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )

        assert configured.rules == RuleConfig(target_score=1000)
        assert configured.play_state.rules is configured.rules
        assert score_round(configured) == score_round(default)

    def test_scorer_rejects_a_ruleset_argument(self, players):
        """Pins the deletion: the ruleset is not a per-call choice, so a
        caller cannot score a round against a table it never played at."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )

        with pytest.raises(TypeError):
            score_round(round_, rules=RuleConfig())  # type: ignore[call-arg]


class TestRoundScoreComponents:
    """The result carries §7.2's two components, not just the totals."""

    def test_a_made_numeric_round_reports_both_components(self, players):
        round_ = _split_round(players, 80, attack=101, defense=61)
        score = score_round(round_)
        att = round_.contract.player.position.team_side
        dfn = TeamSide.EW if att is TeamSide.NS else TeamSide.NS
        assert score.marks[att].announced == round_.contract.value
        assert score.marks[att].made == score.card_points[att]
        assert score.marks[dfn].announced == 0
        assert score.multiplier == 1
        assert score.belote_points == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_the_components_and_belote_reconstruct_every_score(self, players):
        round_ = _split_round(
            players, 80, attack=101, defense=61, belote={TeamSide.NS: 1}
        )
        score = score_round(round_)
        for side, total in score.scores.items():
            assert total == (
                marked_total(score.marks[side], score.multiplier, round_.rules)
                + score.belote_points[side]
            )

    def test_card_points_include_the_last_trick_bonus(self, players):
        round_ = _split_round(players, 80, attack=101, defense=61)
        score = score_round(round_)
        assert sum(score.card_points.values()) == 162
        assert score.last_trick_side is TeamSide.NS

    def test_an_all_passed_round_reports_empty_components(self, players):
        round_ = _all_pass_round(players)
        score = score_round(round_)
        assert score.contract_made is None
        assert all(m == Mark(0, 0) for m in score.marks.values())
        assert score.belote_points == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert score.last_trick_side is None
        assert score.multiplier == 1


class TestRoundPublishesTheScore:
    """``Round`` holds one result object; the old trio reads off it."""

    def test_the_round_holds_the_whole_result(self, players):
        round_ = _split_round(players, 80, attack=101, defense=61)
        assert round_.round_score is None
        assert round_.round_scores == {}
        round_.calculate_round_scores()
        assert round_.round_score is not None
        assert round_.round_scores is round_.round_score.scores
        assert round_.contract_made is round_.round_score.contract_made

    def test_an_all_pass_publishes_a_contractless_result(self, players):
        round_ = _all_pass_round(players)
        round_.handle_failed_contract()
        assert round_.round_score.contract_made is None
        assert round_.round_scores == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert round_.unannounced_slam is None


class TestSlamScoring:
    """Symmetric grid: 500 / 750 / 1250 to the winning side.

    Only the announced half takes the multiplier — the flat substitute
    that replaces the pile does not — so the grid is ``250 + 250 × M``
    rather than ``500 × M``.
    """

    def test_slam_made_normal_attacker_scores_500(self, players):
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 500
        assert scores[TeamSide.EW] == 0

    def test_slam_failed_normal_defender_scores_500(self, players):
        # Attacker (N) takes only 7 tricks; W steals one → contract fails.
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 500

    def test_slam_made_doubled_attacker_scores_750(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 750  # 250 + 250*2
        assert scores[TeamSide.EW] == 0

    def test_slam_failed_doubled_defender_scores_750(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        winners = ["N"] * 6 + ["E", "W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 750  # 250 + 250*2

    def test_slam_made_redoubled_attacker_scores_1250(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 1250  # 250 + 250*4
        assert scores[TeamSide.EW] == 0

    def test_slam_failed_redoubled_defender_scores_1250(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 1250  # 250 + 250*4

    def test_slam_team_partner_wins_a_trick_still_makes(self, players):
        """Plain Slam only cares about the TEAM winning all 8. The
        partner taking some tricks is fine — that's the Solo Slam
        rule, not Slam."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        # N takes 5, partner S takes 3 → team owns all 8 → contract made.
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 500
        assert scores[TeamSide.EW] == 0


class TestSoloSlamScoring:
    """Bidder-personally rule + 1000 / 1500 / 2500 symmetric grid.

    Same shape as the Slam grid — ``500 + 500 × M``, the substitute
    staying flat while the announced half takes the multiplier.
    """

    def test_solo_slam_made_bidder_takes_all_8(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 1000
        assert scores[TeamSide.EW] == 0

    def test_solo_slam_failed_when_partner_takes_a_trick(self, players):
        """Key Solo Slam invariant: team owning all 8 tricks is NOT
        enough — the bidder personally must win them all."""
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["S"]  # partner wins the last trick
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        # Team took all 8 tricks, but partner won one → Solo Slam fails.
        # Defenders score the at-risk amount.
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 1000

    def test_solo_slam_failed_when_opponent_takes_a_trick(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 1000

    def test_solo_slam_made_doubled_scores_1500(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 1500  # 500 + 500*2
        assert scores[TeamSide.EW] == 0

    def test_solo_slam_made_redoubled_scores_2500(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 2500  # 500 + 500*4
        assert scores[TeamSide.EW] == 0

    def test_solo_slam_failed_redoubled_defender_scores_2500(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["S"]  # partner steals one → Solo Slam fails
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 2500  # 500 + 500*4


class TestSlamFamilyBeloteLayering:
    """Belote (+20) applies on top of the Slam grid for whichever team
    *holds* the K + Q of trump, independent of who wins the contract."""

    def test_slam_made_belote_to_attacker(self, players):
        """Slam made, attacker holds belote → 500 + 20 to attacker."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        round_.belote_pairs = {players["N"]: (Suit.HEARTS,)}  # N-S holds it
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 520  # 500 + 20
        assert scores[TeamSide.EW] == 0

    def test_slam_failed_belote_to_defender(self, players):
        """Slam failed, defender holds belote → 500 + 20 to defender."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_pairs = {players["W"]: (Suit.HEARTS,)}  # E-W holds it
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 520  # 500 + 20

    def test_slam_failed_belote_to_attacker_independent_of_contract(
        self, players
    ):
        """Belote is independent of contract outcome: attacker can hold
        belote even when they lost the contract → defender scores 500,
        attacker still scores +20."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_pairs = {players["N"]: (Suit.HEARTS,)}  # attacker
        scores = round_.calculate_round_scores()
        # Attacker still gets +20 from belote even though the contract failed.
        assert scores[TeamSide.NS] == 20
        assert scores[TeamSide.EW] == 500


class TestNumericContractScoringRegression:
    """Confirms numeric (80–180) contracts are *not* affected by the
    Slam-family branch added during this refactor."""

    @staticmethod
    def _jack_trick(players_dict, seat):
        """A trick ``seat`` wins with the clubs-trump Jack — 20 points."""
        winner = players_dict[seat]
        others = [players_dict[s] for s in _ORDER if s != seat]
        return (
            Play(winner, Card(Suit.CLUBS, Rank.JACK)),
            Play(others[0], Card(Suit.HEARTS, Rank.SEVEN)),
            Play(others[1], Card(Suit.HEARTS, Rank.EIGHT)),
            Play(others[2], Card(Suit.DIAMONDS, Rank.SEVEN)),
        )

    def test_numeric_made_normal_uses_base_plus_card_points(self, players):
        """80 made by N-S without double, and *not* a sweep: attacker =
        80 + card points, defender = its own card points. Trump = clubs;
        the bidder wins seven tricks with the trump Jack (20 pts each)
        while E-W steal one 0-point trick — so the plain made formula,
        not the unannounced-Slam substitute, is the path under test."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        order = [players[s] for s in _ORDER]
        round_ = Round(
            order, dealer=players["N"], deck=None, round_number=1
        )
        round_.contract = contract
        # Six Jack tricks to N, one 0-point steal to E-W, then a final
        # Jack trick to N so the last-trick bonus lands with N-S. (Card
        # identity is fine — the bare play state is unvalidated and
        # scoring has no unique-per-instance invariants.)
        plays = []
        for _ in range(6):
            plays.extend(self._jack_trick(players, "N"))
        plays.extend(_stack_trick(players, "E", Suit.CLUBS))
        plays.extend(self._jack_trick(players, "N"))
        _seed_play_state(round_, players, contract, plays)
        assert list(round_.play_state.trick_winners) == (
            [players["N"]] * 6 + [players["E"]] + [players["N"]]
        )
        scores = round_.calculate_round_scores()
        # Card points = 20*7 = 140; last-trick bonus = +10 → 150 card pts.
        # Contract made (150 >= 80) → attacker score = 80 + 150 = 230.
        assert round_.unannounced_slam is None
        assert scores[TeamSide.NS] == 230
        # E-W captured a single 0-point trick → 0 card points.
        assert scores[TeamSide.EW] == 0

    def test_numeric_failed_normal_defender_gets_160_plus_base(self, players):
        """Failed 80 contract by N-S: defender gets (160 + 80) * 1 = 240."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        # 0 tricks to N — contract fails immediately on points (0 < 80).
        round_ = _slam_round(
            players, contract=contract, trick_winners=["E"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 240


# ---------------------------------------------------------------------------
# Numeric scoring — belote attribution & doubled (winner-takes-all)
# ---------------------------------------------------------------------------
#
# These build a Round directly and seed its ``play_state`` with
# synthesised four-play tricks. Scoring credits each completed trick's
# whole pile to its winner's team, so a team's point-carrying cards are
# packed into tricks played (and therefore won) entirely by one of its
# seats, padded to four plays with 0-point filler. Trump = hearts
# throughout, where the trump-aware values are J=20, 9=14, A=11, 10=10,
# K=4, Q=3, 8=7=0.

#: Zero-point padding cards (7s and 8s are 0 on both scales).
_FILLERS = (
    Card(Suit.DIAMONDS, Rank.SEVEN),
    Card(Suit.DIAMONDS, Rank.EIGHT),
    Card(Suit.SPADES, Rank.EIGHT),
)


def _numeric_round(
    players_dict,
    *,
    contract,
    team_cards,
    last_trick_winner=None,
    belote_pairs=None,
    rules=None,
):
    """Build a numeric-contract Round with a synthesised play state.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a numeric Contract bound to one of the players.
        team_cards: mapping team side → list of ``(seat, Card)`` plays.
            Each team's cards are chunked into four-play tricks padded
            with 0-point filler, every play made by that team's seats —
            so the trick's winner (whoever it is) credits the whole pile
            to that team.
        last_trick_winner: seat letter credited with the last-trick
            bonus, or None. Realised as a final zero-point stacked trick
            won by that seat.
        belote_pairs: mapping of seat letter → the suits that seat
            holds a K + Q pair in, or None for a belote-free round. A
            seat can pair in more than one suit under all trump.
        rules: optional table ruleset, handed to ``Round`` and from
            there to the seeded play state — so a case that varies a
            §9.6 knob varies it on the round rather than at the
            ``score_round`` call.

    Returns:
        Round with ``contract``, ``play_state`` and ``belote_pairs``
        populated.
    """
    order = [players_dict[s] for s in _ORDER]
    round_ = Round(
        order, dealer=players_dict["N"], deck=None, round_number=1, rules=rules
    )
    round_.contract = contract

    plays = []
    expected_sides = []
    for side, seat_cards in team_cards.items():
        # A trick holds exactly four plays — chunk the team's cards and
        # pad the tail with 0-point filler from the chunk's first seat,
        # keeping every play (hence the winner) inside the team.
        for start in range(0, len(seat_cards), 4):
            chunk = list(seat_cards[start:start + 4])
            pad_seat = chunk[0][0]
            while len(chunk) < 4:
                chunk.append((pad_seat, _FILLERS[len(chunk) - 1]))
            plays.extend(
                Play(players_dict[seat], card) for seat, card in chunk
            )
            expected_sides.append(side)
    if last_trick_winner is not None:
        plays.extend(
            _stack_trick(players_dict, last_trick_winner, contract.suit)
        )
        expected_sides.append(
            players_dict[last_trick_winner].position.team_side
        )
    _seed_play_state(round_, players_dict, contract, plays)

    # Self-check: every synthesised trick is won inside the team its
    # cards were meant for, and the last-trick bonus lands as intended.
    assert [
        winner.position.team_side
        for winner in round_.play_state.trick_winners
    ] == expected_sides
    if last_trick_winner is not None:
        assert (
            round_.play_state.trick_winners[-1]
            is players_dict[last_trick_winner]
        )

    if belote_pairs is not None:
        round_.belote_pairs = {
            players_dict[seat]: suits for seat, suits in belote_pairs.items()
        }
    return round_


# ---------------------------------------------------------------------------
# Whole-pile rounds — the 162 split exactly as the scorer sees it
# ---------------------------------------------------------------------------
#
# ``_numeric_round`` above only deals the cards a test names, so its piles
# are partial. The §7.2 / §7.4 / §7.5 rules are all about how the *whole*
# 162 is divided, so those cases need a round where every card is dealt
# and the two piles add up. ``_split_round`` builds exactly that: it
# solves for a subset of the pack worth the attack's share and hands the
# rest to the defense.

#: The trump suit every ``_split_round`` contract is played in. Hearts
#: has no special status — it is fixed so the card values below are
#: unambiguous (trump J = 20, 9 = 14, then A/10/K/Q = 11/10/4/3).
_SPLIT_TRUMP = Suit.HEARTS

#: The whole pack with its trump-aware value, hearts trump: 62 in trump
#: plus 30 in each of the three plain suits = 152, and the last-trick
#: bonus brings the round to 162.
_PACK = tuple(
    (Card(suit, rank), rules_for(_SPLIT_TRUMP).points(Card(suit, rank)))
    for suit in Suit
    for rank in Rank
)


def _cards_worth(target: int) -> list[Card]:
    """Pick a subset of the pack worth exactly ``target`` points.

    An exact 0/1 subset-sum over the 32 card values. The pack carries
    2s, 3s and 4s, so every total a test is likely to ask for is
    reachable — an unreachable one raises rather than silently
    approximating, which would skew the very pile the test is about.

    Args:
        target: The trump-aware card-point total wanted, 0 to 152.

    Returns:
        The chosen cards.

    Raises:
        AssertionError: If no subset of the pack sums to ``target``.
    """
    reachable: dict[int, list[Card]] = {0: []}
    for card, value in sorted(_PACK, key=lambda pair: -pair[1]):
        if value == 0:
            continue
        for total, chosen in list(reachable.items()):
            nxt = total + value
            if nxt <= 152 and nxt not in reachable:
                reachable[nxt] = chosen + [card]
    assert target in reachable, f"no subset of the pack is worth {target}"
    return reachable[target]


def _split_round(
    players_dict,
    value,
    *,
    attack,
    defense,
    belote=None,
    rules=None,
    declarer="N",
):
    """Build a numeric round whose two piles are exactly ``attack``/``defense``.

    Both figures are *card points including the last-trick bonus* — the
    numbers §7.2 works from — so they must add up to 162. The last trick
    always goes to the declaring side, so the attack's cards are worth
    ``attack - 10``.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        value: the numeric contract value.
        attack: the declaring side's pile, last-trick bonus included.
        defense: the defending side's pile.
        belote: mapping team side → how many K + Q pairs that side
            holds, or None for a belote-free round.
        rules: optional table ruleset, handed to the round (and through
            it to the play state).
        declarer: seat letter that bids the contract.

    Returns:
        Round with ``contract``, ``play_state`` and ``belote_pairs``
        populated.
    """
    assert attack + defense == 162, (
        f"a round is worth 162, not {attack + defense}"
    )
    attack_side = players_dict[declarer].position.team_side
    defense_side = next(s for s in TeamSide if s is not attack_side)
    attack_seat = declarer
    defense_seat = next(
        s for s in _ORDER
        if players_dict[s].position.team_side is defense_side
    )

    attack_cards = _cards_worth(attack - 10)
    taken = list(attack_cards)
    defense_cards = []
    for card, _ in _PACK:
        if card in taken:
            taken.remove(card)
        else:
            defense_cards.append(card)

    belote_pairs = None
    if belote:
        #: Give each side's pairs to one seat, in fixed suit order — the
        #: scorer only ever counts pairs per side.
        suits = tuple(Suit)
        belote_pairs = {
            (attack_seat if side is attack_side else defense_seat): suits[:count]
            for side, count in belote.items()
            if count
        }

    round_ = _numeric_round(
        players_dict,
        contract=_contract(players_dict[declarer], value, _SPLIT_TRUMP),
        team_cards={
            attack_side: [(attack_seat, card) for card in attack_cards],
            defense_side: [(defense_seat, card) for card in defense_cards],
        },
        last_trick_winner=attack_seat,
        belote_pairs=belote_pairs,
        rules=rules,
    )
    # Self-check: the synthesised deal really does split 162 the way the
    # test asked, so a wrong expectation can never come from the fixture.
    piles = round_.play_state.card_points_by_side
    assert piles[attack_side] + 10 == attack
    assert piles[defense_side] == defense
    return round_


def _all_pass_round(players_dict, *, rules=None):
    """A contractless round — everybody passed, the deal is redealt."""
    order = [players_dict[s] for s in _ORDER]
    return Round(
        order,
        dealer=players_dict["N"],
        deck=Deck(),
        round_number=1,
        rules=rules,
    )


class TestNumericBeloteByHolder:
    """Belote follows the *holder* of K + Q of trump, never the team that
    merely captures those cards in a trick. This is the Problem-1
    regression: a phantom capture-based +20 used to flip a failed
    contract into a spurious "made"."""

    # All eight hearts = 62 trump-aware points, including both K and Q.
    _HEART_RANKS = (
        Rank.JACK, Rank.NINE, Rank.ACE, Rank.TEN,
        Rank.KING, Rank.QUEEN, Rank.EIGHT, Rank.SEVEN,
    )

    def _all_hearts_for(self, seat):
        return [(seat, Card(Suit.HEARTS, r)) for r in self._HEART_RANKS]

    def test_captured_kq_without_holder_does_not_make_contract(self, players):
        """E-W capture all hearts (incl. K+Q, 62 pts) but no single
        player *holds* the pair → no belote. Bare 62 < 80 → the contract
        FAILS. Under the old capture-based rule the phantom +20 would
        have lifted 62→82 and "made" the 80 contract — the bug behind
        the impossible recap."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.EW: self._all_hearts_for("E"),
                TeamSide.NS: [],
            },
            last_trick_winner="N",  # last-trick bonus to N-S, not the declarer
            belote_pairs=None,      # pair is split — nobody holds it
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores[TeamSide.EW] == 0
        assert scores[TeamSide.NS] == 240  # 160 + 80

    def test_belote_credited_to_holder_even_if_opponent_captures(self, players):
        """E-W capture the K+Q in their tricks, but S (N-S) *held* the
        pair → the +20 belote is credited to N-S, the holder, not E-W."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.EW: self._all_hearts_for("E"),
                TeamSide.NS: [],
            },
            last_trick_winner="N",
            belote_pairs={"S": (Suit.HEARTS,)},  # N-S holds the pair
        )
        scores = round_.calculate_round_scores()
        # Declarer E-W realized 62 < 80 → failed → 0.
        assert scores[TeamSide.EW] == 0
        # Defender N-S: 160 + 80 (winner-takes-all, M=1) + 20 belote.
        assert scores[TeamSide.NS] == 260

    def test_failed_declarer_keeps_only_its_belote(self, players):
        """A failed declarer keeps its belote bonus (always preserved)
        and nothing else."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.EW: [
                    ("E", Card(Suit.HEARTS, Rank.KING)),
                    ("E", Card(Suit.HEARTS, Rank.QUEEN)),
                ],
                TeamSide.NS: [],
            },
            last_trick_winner="N",
            belote_pairs={"E": (Suit.HEARTS,)},  # declarer holds the pair
        )
        scores = round_.calculate_round_scores()
        # E-W realized = 7 cards + 20 belote = 27 < 80 → failed.
        assert round_.contract_made is False
        assert scores[TeamSide.EW] == 20    # belote only
        assert scores[TeamSide.NS] == 240  # 160 + 80


class TestNumericDoubledScoring:
    """Doubled / redoubled numeric contracts: winner-takes-all, the loser
    scores 0 except its belote. The winner amount is 160 + C×M whether it
    is the made declarer or the winning defense."""

    @staticmethod
    def _ns_big_pile():
        """76 trump-aware points for N-S — clears an 80 contract once the
        last-trick bonus is added."""
        return [
            ("N", Card(Suit.HEARTS, Rank.JACK)),  # 20
            ("N", Card(Suit.HEARTS, Rank.NINE)),  # 14
            ("N", Card(Suit.HEARTS, Rank.ACE)),   # 11
            ("N", Card(Suit.HEARTS, Rank.TEN)),   # 10
            ("S", Card(Suit.SPADES, Rank.ACE)),   # 11
            ("S", Card(Suit.SPADES, Rank.TEN)),   # 10
        ]

    def test_doubled_made_defender_scores_zero(self, players):
        """Doubled contract made: the defending side scores 0 even though
        it captured point-carrying cards (Problem 2)."""
        contract = Contract(
            ContractBid(players["N"], 80, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.NS: self._ns_big_pile(),
                # E-W win a fat trick — under the old rule they'd keep
                # these 14 points; winner-takes-all zeroes them.
                TeamSide.EW: [
                    ("E", Card(Suit.DIAMONDS, Rank.TEN)),  # 10
                    ("E", Card(Suit.CLUBS, Rank.KING)),    # 4
                ],
            },
            last_trick_winner="N",  # +10 bonus → N-S realized 86 ≥ 80
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores[TeamSide.NS] == 320  # 160 + 80*2
        assert scores[TeamSide.EW] == 0

    def test_doubled_made_defender_keeps_only_belote(self, players):
        """The lone exception: the losing defender keeps its belote."""
        contract = Contract(
            ContractBid(players["N"], 80, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.NS: self._ns_big_pile(),
                TeamSide.EW: [("E", Card(Suit.CLUBS, Rank.KING))],
            },
            last_trick_winner="N",
            belote_pairs={"E": (Suit.SPADES,)},  # E-W defender holds it
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 320  # 160 + 80*2
        assert scores[TeamSide.EW] == 20     # belote only

    def test_doubled_failed_winner_takes_160_plus_cm(self, players):
        """Doubled contract failed: the defense takes 160 + C×M, declarer 0."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.HEARTS),
            double_player=players["E"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.NS: [("N", Card(Suit.DIAMONDS, Rank.TEN))],  # 10 < 100
                TeamSide.EW: [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 360  # 160 + 100*2

    def test_redoubled_failed_winner_takes_160_plus_c_times_four(self, players):
        """Redoubled failed: the defense takes 160 + C×4 — the same shape
        as a made redoubled declarer (symmetric stake)."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.HEARTS),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                TeamSide.NS: [("N", Card(Suit.DIAMONDS, Rank.TEN))],
                TeamSide.EW: [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 0
        assert scores[TeamSide.EW] == 560  # 160 + 100*4


# ---------------------------------------------------------------------------
# Unannounced Slam scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# When the declaring team wins all 8 tricks on an *un-doubled* numeric
# contract without having bid a Slam, the 162-point pile (152 cards + 10
# last-trick bonus) is replaced by a flat substitute: the declarer scores
# contract value + substitute (+ belote), the defence scores nothing, and the
# contract is necessarily made. The round is flagged UnannouncedSlam.GRAND_SLAM
# when the contracting player personally won all 8 tricks — worth the 500 of
# the Solo Slam they could have announced — else UnannouncedSlam.SLAM and the
# team's 250. A doubled/redoubled sweep keeps the winner-takes-all
# 160 + C×M shape, and a defence sweep is unaffected (declaring team only).


class TestUnannouncedSlamEnum:
    """The UnannouncedSlam member value is its display label."""

    def test_member_labels_via_str(self):
        assert str(UnannouncedSlam.SLAM) == "Slam"
        assert str(UnannouncedSlam.GRAND_SLAM) == "Grand Slam"


class TestUnannouncedSlamSubstitute:
    """The tag → flat-substitute mapping shared by scorer and recap."""

    def test_team_sweep_substitutes_250(self):
        assert sweep_substitute(UnannouncedSlam.SLAM) == 250

    def test_declarer_sweep_substitutes_500(self):
        assert sweep_substitute(UnannouncedSlam.GRAND_SLAM) == 500

    def test_no_sweep_substitutes_nothing(self):
        # An ordinary round has no tag, so nothing replaces its pile.
        assert sweep_substitute(None) == 0


class TestUnannouncedSlamScoring:
    """Un-doubled numeric sweep by the declaring team → contract + 250."""

    def test_team_sweep_scores_contract_plus_250(self, players):
        """N takes 5, partner S takes 3 → the *team* swept (but no single
        player did) → UnannouncedSlam.SLAM, scored 100 + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert round_.unannounced_slam is UnannouncedSlam.SLAM
        assert round_.contract_made is True
        assert scores[TeamSide.NS] == 350  # 100 + 250
        assert scores[TeamSide.EW] == 0

    def test_bidder_personal_sweep_scores_the_500_substitute(self, players):
        """N wins all 8 personally → UnannouncedSlam.GRAND_SLAM, and the
        substitute is the 500 of the Solo Slam the declarer could have
        announced — not the 250 a split team sweep earns."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_slam is UnannouncedSlam.GRAND_SLAM
        assert scores[TeamSide.NS] == 600  # 100 + 500
        assert scores[TeamSide.EW] == 0

    def test_unannounced_slam_forces_made_below_threshold(self, players):
        """The filler tricks carry 0 card points, so a 180 contract could
        never clear its threshold on cards — but sweeping every trick
        makes it outright. N sweeps personally → 180 + 500 = 680."""
        contract = _contract(players["N"], 180, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores[TeamSide.NS] == 680  # 180 + 500
        assert scores[TeamSide.EW] == 0

    def test_unannounced_slam_layers_belote_on_top(self, players):
        """Belote (+20) still credits the holder on top of contract + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_pairs = {players["N"]: (Suit.HEARTS,)}  # N-S holds it
        scores = round_.calculate_round_scores()
        assert scores[TeamSide.NS] == 370  # 100 + 250 + 20
        assert scores[TeamSide.EW] == 0

    def test_doubled_sweep_keeps_winner_takes_all_and_is_unflagged(self, players):
        """A doubled contract swept by the declarer keeps the
        winner-takes-all 160 + C×M shape — no 250 substitute, no flag."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.SPADES),
            double_player=players["E"],
        )
        order = [players[s] for s in _ORDER]
        round_ = Round(order, dealer=players["N"], deck=None, round_number=1)
        round_.contract = contract
        # N sweeps all 8 with the trump Jack (20 pts each → 160 card
        # points, clearing the 100 threshold). Card identity is
        # irrelevant to scoring, so the same Card may recur.
        winner = players["N"]
        others = [players[s] for s in _ORDER if s != "N"]
        plays = []
        for _ in range(8):
            plays.extend(
                (
                    Play(winner, Card(Suit.SPADES, Rank.JACK)),
                    Play(others[0], Card(Suit.HEARTS, Rank.SEVEN)),
                    Play(others[1], Card(Suit.HEARTS, Rank.EIGHT)),
                    Play(others[2], Card(Suit.DIAMONDS, Rank.SEVEN)),
                )
            )
        _seed_play_state(round_, players, contract, plays)
        assert list(round_.play_state.trick_winners) == [winner] * 8
        scores = round_.calculate_round_scores()
        assert round_.unannounced_slam is None
        assert round_.contract_made is True
        assert scores[TeamSide.NS] == 360  # 160 + 100*2
        assert scores[TeamSide.EW] == 0

    def test_defense_sweep_is_not_an_unannounced_slam(self, players):
        """Declaring team only: when the *defence* sweeps, the declarer
        simply fails (160 + C to the defence) — no 250, not flagged."""
        contract = _contract(players["E"], 100, Suit.SPADES)  # E-W declares
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_slam is None
        assert round_.contract_made is False
        assert scores[TeamSide.EW] == 0
        assert scores[TeamSide.NS] == 260  # 160 + 100 (normal failed)


# ---------------------------------------------------------------------------
# Belote per pair — the all-trump `four` regime can mark up to four
# ---------------------------------------------------------------------------


class TestBelotePerPair:
    """§6.6 / §7.2 — 20 per pair, up to four of them at all trump.

    The scorer reads ``round_.belote_counts_by_side`` and multiplies; the
    regime that decides those counts is the Round's business and is
    covered in ``test_round.py``. What is pinned here is that the
    multiplication reaches every scoring shape, and that the standing
    "the loser keeps its belote" exception holds per pair rather than
    only for the first.
    """

    #: Two ♥ tens to E-W, everything else zero-point filler: the declarer
    #: N-S takes almost nothing, so every round built from it fails.
    _FAILING_CARDS = {
        TeamSide.EW: [("E", Card(Suit.HEARTS, Rank.TEN)),
                      ("W", Card(Suit.HEARTS, Rank.ACE))],
    }

    def _round(self, players, *, pairs, doubled=False):
        """A failed 80 ♥ round for N-S holding ``pairs``."""
        bid = ContractBid(players["N"], 80, Suit.HEARTS)
        contract = Contract(
            bid, double_player=players["E"] if doubled else None
        )
        return _numeric_round(
            players,
            contract=contract,
            team_cards=self._FAILING_CARDS,
            last_trick_winner="E",
            belote_pairs=pairs,
        )

    @pytest.mark.parametrize("suits, bonus", [
        ((), 0),
        ((Suit.HEARTS,), 20),
        ((Suit.HEARTS, Suit.SPADES), 40),
        ((Suit.HEARTS, Suit.SPADES, Suit.CLUBS), 60),
    ])
    def test_a_failed_declarer_keeps_twenty_per_pair(
        self, players, suits, bonus
    ):
        # Belote is the standing exception to "the loser marks 0" (§7.2),
        # and that holds per pair, not only for the first. Capped at three
        # pairs here because a fourth would take the declarer to 80 on
        # belote alone and make the contract — see the test below.
        pairs = {"N": suits} if suits else None
        round_ = self._round(players, pairs=pairs)
        score = score_round(round_)
        assert score.contract_made is False
        assert score.scores[TeamSide.NS] == bonus

    def test_four_belotes_alone_can_make_an_eighty_contract(self, players):
        # Belote counts toward *realized* points, not just toward the
        # mark, so a declarer taking nothing in cards still makes 80 on
        # four pairs — reachable only under the all-trump `four` regime.
        round_ = self._round(
            players,
            pairs={
                "N": (Suit.HEARTS, Suit.SPADES, Suit.CLUBS, Suit.DIAMONDS)
            },
        )
        score = score_round(round_)
        assert score.contract_made is True
        # 80 announced + 0 in cards + 80 of belote.
        assert score.scores[TeamSide.NS] == 160

    def test_a_doubled_loser_keeps_every_pair_it_holds(self, players):
        # Same rule on the winner-takes-all path.
        round_ = self._round(
            players, pairs={"N": (Suit.HEARTS, Suit.SPADES)}, doubled=True
        )
        assert score_round(round_).scores[TeamSide.NS] == 40

    def test_both_sides_can_hold_belotes_at_all_trump(self, players):
        # Three pairs split 2 / 1 across the two sides — unreachable under
        # a suit contract, routine under the all-trump `four` regime.
        base = self._round(players, pairs=None)
        with_pairs = self._round(
            players,
            pairs={"N": (Suit.HEARTS, Suit.SPADES), "E": (Suit.CLUBS,)},
        )
        plain = score_round(base).scores
        marked = score_round(with_pairs).scores
        assert marked[TeamSide.NS] - plain[TeamSide.NS] == 40
        assert marked[TeamSide.EW] - plain[TeamSide.EW] == 20

    def test_two_pairs_in_one_seat_count_twice(self, players):
        # Trap 8's shape: a holder-keyed state could only ever mark one.
        round_ = self._round(players, pairs={"N": (Suit.HEARTS, Suit.SPADES)})
        assert round_.belote_counts_by_side[TeamSide.NS] == 2
        assert score_round(round_).scores[TeamSide.NS] == 40
