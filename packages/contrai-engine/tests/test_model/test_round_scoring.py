"""Tests for round scoring — ``Round.calculate_round_scores`` and the
underlying pure :func:`contrai_engine.model.round.scoring.score_round`.

The scoring rules come from ``contree-domain.md`` §6.5, §7: the numeric
(80-180) share-the-pile path, the unannounced-capot 250 substitute, the
doubled/redoubled winner-takes-all path, and the symmetric Slam / Solo
Slam grid — with the Belote (+20) bonus layered onto every shape for the
team *holding* K + Q of trump.

These build a ``Round`` directly and stuff it with the minimal state the
scoring path reads (``contract`` / ``team_tricks`` / ``tricks`` /
``last_trick_winner`` / ``belote_holder``), then assert on the published
result attributes (``round_scores`` / ``contract_made`` /
``unannounced_capot``). The shared ``players`` fixture lives in
``conftest.py``.
"""

from __future__ import annotations

from contrai_core.bid import ContractBid, SlamLevel
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from contrai_engine.model.round import Round, UnannouncedSlam
from contrai_engine.model.round.scoring import RoundScore, score_round


def _contract(player, value, suit):
    return Contract(ContractBid(player, value, suit))


# ---------------------------------------------------------------------------
# Slam / Solo Slam scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# Tests below build a Round directly and stuff it with the minimal state
# the scoring path reads:
#   - ``self.contract``         — drives base / multiplier / family check.
#   - ``self.team_tricks``      — number of tricks per team (length used).
#   - ``self.tricks``           — per-trick winners (used by Solo Slam).
#   - ``self.last_trick_winner``— "dix de der" (irrelevant for Slam family).
#
# Cards inside each Trick only matter when belote / card points are
# computed; for Slam family they are not — we still seed at least one
# card per trick so :meth:`Trick.get_current_winner` has something to
# answer with.


def _slam_round(
    players_dict,
    *,
    contract,
    trick_winners,
):
    """Build a Round with synthesised tricks.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a Contract bound to one of the players.
        trick_winners: ordered list of seat letters — one per completed
            trick. Each entry is the player who wins that trick. Cards
            are filler (the suit-7), and the winner leads it so
            :meth:`Trick.get_current_winner` returns them.

    Returns:
        Round with ``contract``, ``tricks``, ``team_tricks``, and
        ``last_trick_winner`` populated.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    round_ = Round(order, dealer=players_dict["N"], deck=None, round_number=1)
    round_.contract = contract

    # Filler card per trick: a low non-trump card. The winner plays it
    # solo so get_current_winner returns them regardless of trump.
    filler = Card(Suit.CLUBS, Rank.SEVEN)
    for seat in trick_winners:
        trick = Trick()
        trick.add_play(players_dict[seat], filler)
        round_.tricks.append(trick)
        winner = players_dict[seat]
        if winner.team is not None:
            round_.team_tricks[winner.team.name].append(trick)

    if trick_winners:
        round_.last_trick_winner = players_dict[trick_winners[-1]]
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
        assert result.scores["North-South"] == 500
        assert result.contract_made is True
        assert result.unannounced_capot is None
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


class TestSlamScoring:
    """Symmetric grid: 500 / 1000 / 2000 to the winning side."""

    def test_slam_made_normal_attacker_scores_500(self, players):
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0

    def test_slam_failed_normal_defender_scores_500(self, players):
        # Attacker (N) takes only 7 tricks; W steals one → contract fails.
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 500

    def test_slam_made_doubled_attacker_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_slam_failed_doubled_defender_scores_1000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        winners = ["N"] * 6 + ["E", "W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_slam_made_redoubled_attacker_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_slam_failed_redoubled_defender_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 2000

    def test_slam_team_partner_wins_a_trick_still_makes(self, players):
        """Plain Slam only cares about the TEAM winning all 8. The
        partner taking some tricks is fine — that's the Solo Slam
        rule, not Slam."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        # N takes 5, partner S takes 3 → team owns all 8 → contract made.
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 500
        assert scores["East-West"] == 0


class TestSoloSlamScoring:
    """Bidder-personally rule + 1000 / 2000 / 4000 symmetric grid."""

    def test_solo_slam_made_bidder_takes_all_8(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 1000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_when_partner_takes_a_trick(self, players):
        """Key Solo Slam invariant: team owning all 8 tricks is NOT
        enough — the bidder personally must win them all."""
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["S"]  # partner wins the last trick
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        # Team took all 8 tricks, but partner won one → Solo Slam fails.
        # Defenders score the at-risk amount.
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_failed_when_opponent_takes_a_trick(self, players):
        contract = _contract(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 1000

    def test_solo_slam_made_doubled_scores_2000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 2000
        assert scores["East-West"] == 0

    def test_solo_slam_made_redoubled_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 4000
        assert scores["East-West"] == 0

    def test_solo_slam_failed_redoubled_defender_scores_4000(self, players):
        contract = Contract(
            ContractBid(players["N"], SlamLevel.SOLO_SLAM, Suit.SPADES),
            double_player=players["E"],
            redouble_player=players["N"],
        )
        winners = ["N"] * 7 + ["S"]  # partner steals one → Solo Slam fails
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 4000


class TestSlamFamilyBeloteLayering:
    """Belote (+20) applies on top of the Slam grid for whichever team
    *holds* the K + Q of trump, independent of who wins the contract."""

    def test_slam_made_belote_to_attacker(self, players):
        """Slam made, attacker holds belote → 500 + 20 to attacker."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        round_.belote_holder = players["N"]  # N-S holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 520  # 500 + 20
        assert scores["East-West"] == 0

    def test_slam_failed_belote_to_defender(self, players):
        """Slam failed, defender holds belote → 500 + 20 to defender."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["W"]  # E-W holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 520  # 500 + 20

    def test_slam_failed_belote_to_attacker_independent_of_contract(
        self, players
    ):
        """Belote is independent of contract outcome: attacker can hold
        belote even when they lost the contract → defender scores 500,
        attacker still scores +20."""
        contract = _contract(players["N"], SlamLevel.SLAM, Suit.SPADES)
        winners = ["N"] * 7 + ["W"]
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["N"]  # attacker holds belote
        scores = round_.calculate_round_scores()
        # Attacker still gets +20 from belote even though the contract failed.
        assert scores["North-South"] == 20
        assert scores["East-West"] == 500


class TestNumericContractScoringRegression:
    """Confirms numeric (80–180) contracts are *not* affected by the
    Slam-family branch added during this refactor."""

    @staticmethod
    def _trick_with_card(seat_player, card):
        trick = Trick()
        trick.add_play(seat_player, card)
        return trick

    def test_numeric_made_normal_uses_base_plus_card_points(self, players):
        """80 made by N-S without double, and *not* a capot: attacker =
        80 + card points, defender = its own card points. Trump = clubs;
        the bidder plays the trump Jack (20 pts) in seven tricks while
        E-W steal one 0-point trick — so the plain made formula, not the
        unannounced-capot substitute, is the path under test."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(
            order, dealer=players["N"], deck=None, round_number=1
        )
        round_.contract = contract
        # Seven tricks where N plays the trump Jack solo — 20 pts each.
        # (Card identity is fine — Card doesn't have unique-per-instance
        # invariants we care about for scoring.)
        for _ in range(7):
            trick = self._trick_with_card(
                players["N"], Card(Suit.CLUBS, Rank.JACK)
            )
            round_.tricks.append(trick)
            round_.team_tricks["North-South"].append(trick)
        # E-W steal a single 0-point trick so N-S did not sweep all 8.
        ew_trick = self._trick_with_card(
            players["E"], Card(Suit.HEARTS, Rank.SEVEN)
        )
        round_.tricks.append(ew_trick)
        round_.team_tricks["East-West"].append(ew_trick)
        round_.last_trick_winner = players["N"]
        scores = round_.calculate_round_scores()
        # Card points = 20*7 = 140; dix de der = +10 → 150 card pts.
        # Contract made (150 >= 80) → attacker score = 80 + 150 = 230.
        assert round_.unannounced_capot is None
        assert scores["North-South"] == 230
        # E-W captured a single 0-point trick → 0 card points.
        assert scores["East-West"] == 0

    def test_numeric_failed_normal_defender_gets_160_plus_base(self, players):
        """Failed 80 contract by N-S: defender gets (160 + 80) * 1 = 240."""
        contract = _contract(players["N"], 80, Suit.CLUBS)
        # 0 tricks to N — contract fails immediately on points (0 < 80).
        round_ = _slam_round(
            players, contract=contract, trick_winners=["E"] * 8
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 240


# ---------------------------------------------------------------------------
# Numeric scoring — belote attribution & doubled (winner-takes-all)
# ---------------------------------------------------------------------------
#
# These build a Round directly and stuff ``team_tricks`` with synthesised
# tricks. Scoring only sums ``card.get_points(trump)`` over each team's
# tricks, so the trick *shape* (how many cards, who else played) is
# irrelevant — we can pack all of a team's point-carrying cards into a
# single Trick. Trump = hearts throughout, where the trump-aware values
# are J=20, 9=14, A=11, 10=10, K=4, Q=3, 8=7=0.


def _numeric_round(
    players_dict,
    *,
    contract,
    team_cards,
    last_trick_winner=None,
    belote_holder=None,
):
    """Build a numeric-contract Round with synthesised tricks.

    Args:
        players_dict: the ``players`` fixture (seat → Player).
        contract: a numeric Contract bound to one of the players.
        team_cards: mapping team-name → list of ``(seat, Card)`` plays.
            Each team's cards are packed into Tricks of up to four cards
            (the Trick capacity), all credited to that team.
        last_trick_winner: seat letter credited with the dix de der, or
            None.
        belote_holder: seat letter holding K + Q of trump, or None.

    Returns:
        Round with ``contract``, ``tricks``, ``team_tricks``,
        ``last_trick_winner`` and ``belote_holder`` populated.
    """
    order = [players_dict[s] for s in ("N", "E", "S", "W")]
    round_ = Round(order, dealer=players_dict["N"], deck=None, round_number=1)
    round_.contract = contract
    for team_name, plays in team_cards.items():
        # Trick holds at most four cards — chunk the team's plays so the
        # synthesised pile spans as many tricks as needed.
        for start in range(0, len(plays), 4):
            trick = Trick()
            for seat, card in plays[start:start + 4]:
                trick.add_play(players_dict[seat], card)
            round_.tricks.append(trick)
            round_.team_tricks[team_name].append(trick)
    if last_trick_winner is not None:
        round_.last_trick_winner = players_dict[last_trick_winner]
    if belote_holder is not None:
        round_.belote_holder = players_dict[belote_holder]
    return round_


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
                "East-West": self._all_hearts_for("E"),
                "North-South": [],
            },
            last_trick_winner="N",  # der to N-S, not the declarer
            belote_holder=None,     # pair is split — nobody holds it
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores["East-West"] == 0
        assert scores["North-South"] == 240  # 160 + 80

    def test_belote_credited_to_holder_even_if_opponent_captures(self, players):
        """E-W capture the K+Q in their tricks, but S (N-S) *held* the
        pair → the +20 belote is credited to N-S, the holder, not E-W."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "East-West": self._all_hearts_for("E"),
                "North-South": [],
            },
            last_trick_winner="N",
            belote_holder="S",  # N-S holds the pair
        )
        scores = round_.calculate_round_scores()
        # Declarer E-W realized 62 < 80 → failed → 0.
        assert scores["East-West"] == 0
        # Defender N-S: 160 + 80 (winner-takes-all, M=1) + 20 belote.
        assert scores["North-South"] == 260

    def test_failed_declarer_keeps_only_its_belote(self, players):
        """A failed declarer keeps its belote bonus (always preserved)
        and nothing else."""
        contract = _contract(players["E"], 80, Suit.HEARTS)
        round_ = _numeric_round(
            players,
            contract=contract,
            team_cards={
                "East-West": [
                    ("E", Card(Suit.HEARTS, Rank.KING)),
                    ("E", Card(Suit.HEARTS, Rank.QUEEN)),
                ],
                "North-South": [],
            },
            last_trick_winner="N",
            belote_holder="E",  # declarer holds the pair
        )
        scores = round_.calculate_round_scores()
        # E-W realized = 7 cards + 20 belote = 27 < 80 → failed.
        assert round_.contract_made is False
        assert scores["East-West"] == 20    # belote only
        assert scores["North-South"] == 240  # 160 + 80


class TestNumericDoubledScoring:
    """Doubled / redoubled numeric contracts: winner-takes-all, the loser
    scores 0 except its belote. The winner amount is 160 + C×M whether it
    is the made declarer or the winning defense."""

    @staticmethod
    def _ns_big_pile():
        """76 trump-aware points for N-S — clears an 80 contract once the
        dix de der is added."""
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
                "North-South": self._ns_big_pile(),
                # E-W win a fat trick — under the old rule they'd keep
                # these 14 points; winner-takes-all zeroes them.
                "East-West": [
                    ("E", Card(Suit.DIAMONDS, Rank.TEN)),  # 10
                    ("E", Card(Suit.CLUBS, Rank.KING)),    # 4
                ],
            },
            last_trick_winner="N",  # +10 der → N-S realized 86 ≥ 80
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores["North-South"] == 320  # 160 + 80*2
        assert scores["East-West"] == 0

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
                "North-South": self._ns_big_pile(),
                "East-West": [("E", Card(Suit.CLUBS, Rank.KING))],
            },
            last_trick_winner="N",
            belote_holder="E",  # E-W (defender) holds the pair
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 320  # 160 + 80*2
        assert scores["East-West"] == 20     # belote only

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
                "North-South": [("N", Card(Suit.DIAMONDS, Rank.TEN))],  # 10 < 100
                "East-West": [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is False
        assert scores["North-South"] == 0
        assert scores["East-West"] == 360  # 160 + 100*2

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
                "North-South": [("N", Card(Suit.DIAMONDS, Rank.TEN))],
                "East-West": [("E", Card(Suit.HEARTS, Rank.JACK))],
            },
            last_trick_winner="E",
        )
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 0
        assert scores["East-West"] == 560  # 160 + 100*4


# ---------------------------------------------------------------------------
# Unannounced capot scoring (calculate_round_scores)
# ---------------------------------------------------------------------------
#
# When the declaring team wins all 8 tricks on an *un-doubled* numeric
# contract without having bid a Slam, the 162-point pile (152 cards + 10
# dix de der) is replaced by a flat 250 substitute: the declarer scores
# contract value + 250 (+ belote), the defence scores nothing, and the
# contract is necessarily made. The round is flagged UnannouncedSlam.GRAND_SLAM
# when the contracting player personally won all 8 tricks, else
# UnannouncedSlam.SLAM. A doubled/redoubled sweep keeps the winner-takes-all
# 160 + C×M shape, and a defence sweep is unaffected (declaring team only).


class TestUnannouncedSlamEnum:
    """The UnannouncedSlam member value is its display label."""

    def test_member_labels_via_str(self):
        assert str(UnannouncedSlam.SLAM) == "Slam"
        assert str(UnannouncedSlam.GRAND_SLAM) == "Grand Slam"


class TestUnannouncedSlamScoring:
    """Un-doubled numeric sweep by the declaring team → contract + 250."""

    def test_team_sweep_scores_contract_plus_250(self, players):
        """N takes 5, partner S takes 3 → the *team* swept (but no single
        player did) → UnannouncedSlam.SLAM, scored 100 + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is UnannouncedSlam.SLAM
        assert round_.contract_made is True
        assert scores["North-South"] == 350  # 100 + 250
        assert scores["East-West"] == 0

    def test_bidder_personal_sweep_is_grand_slam(self, players):
        """N wins all 8 personally → UnannouncedSlam.GRAND_SLAM (same 250 substitute)."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is UnannouncedSlam.GRAND_SLAM
        assert scores["North-South"] == 350  # 100 + 250
        assert scores["East-West"] == 0

    def test_capot_forces_made_below_threshold(self, players):
        """The filler tricks carry 0 card points, so a 180 contract could
        never clear its threshold on cards — but sweeping every trick
        makes it outright → 180 + 250 = 430."""
        contract = _contract(players["N"], 180, Suit.SPADES)
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.contract_made is True
        assert scores["North-South"] == 430  # 180 + 250
        assert scores["East-West"] == 0

    def test_capot_layers_belote_on_top(self, players):
        """Belote (+20) still credits the holder on top of contract + 250."""
        contract = _contract(players["N"], 100, Suit.SPADES)
        winners = ["N"] * 5 + ["S"] * 3
        round_ = _slam_round(players, contract=contract, trick_winners=winners)
        round_.belote_holder = players["N"]  # N-S holds K+Q of trump
        scores = round_.calculate_round_scores()
        assert scores["North-South"] == 370  # 100 + 250 + 20
        assert scores["East-West"] == 0

    def test_doubled_sweep_keeps_winner_takes_all_and_is_unflagged(self, players):
        """A doubled contract swept by the declarer keeps the
        winner-takes-all 160 + C×M shape — no 250 substitute, no flag."""
        contract = Contract(
            ContractBid(players["N"], 100, Suit.SPADES),
            double_player=players["E"],
        )
        order = [players[s] for s in ("N", "E", "S", "W")]
        round_ = Round(order, dealer=players["N"], deck=None, round_number=1)
        round_.contract = contract
        # N sweeps all 8 with the trump Jack (20 pts each → 160 card
        # points, clearing the 100 threshold). Card identity is
        # irrelevant to scoring, so the same Card may recur.
        for _ in range(8):
            trick = Trick()
            trick.add_play(players["N"], Card(Suit.SPADES, Rank.JACK))
            round_.tricks.append(trick)
            round_.team_tricks["North-South"].append(trick)
        round_.last_trick_winner = players["N"]
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is None
        assert round_.contract_made is True
        assert scores["North-South"] == 360  # 160 + 100*2
        assert scores["East-West"] == 0

    def test_defense_sweep_is_not_a_capot(self, players):
        """Declaring team only: when the *defence* sweeps, the declarer
        simply fails (160 + C to the defence) — no 250, not flagged."""
        contract = _contract(players["E"], 100, Suit.SPADES)  # E-W declares
        round_ = _slam_round(
            players, contract=contract, trick_winners=["N"] * 8
        )
        scores = round_.calculate_round_scores()
        assert round_.unannounced_capot is None
        assert round_.contract_made is False
        assert scores["East-West"] == 0
        assert scores["North-South"] == 260  # 160 + 100 (normal failed)
