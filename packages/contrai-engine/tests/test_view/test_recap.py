"""Tests for the round-recap screen in :mod:`contrai_engine.view.screens.recap`.

Covers the per-team ``_recap_breakdown`` (the point-component invariant
that drives both sub-tables) and the rendered recap panel, plus the
``RichView.show_round_recap`` orchestration that prints it.
"""

from __future__ import annotations

import re

import pytest

from contrai_core import Card, Rank, Suit, Trick
from contrai_core.bid import SlamLevel
from contrai_engine.model.round import UnannouncedSlam
from contrai_engine.view.rich_view import RichView
from contrai_engine.view.screens.recap import (
    _panel_round_recap,
    _recap_breakdown,
)


class TestRoundRecapPanel:
    """Between-rounds recap: contract, made/failed, totals, belote."""

    class _StubContract:
        def __init__(self, value, suit, team_name, double=False, redouble=False):
            self.value = value
            self.suit = suit
            class _T: pass
            self.team = _T()
            self.team.name = team_name
            self.double = double
            self.redouble = redouble

        def is_slam_family(self) -> bool:
            return isinstance(self.value, SlamLevel)

        def is_slam(self) -> bool:
            return self.value is SlamLevel.SLAM

        def is_solo_slam(self) -> bool:
            return self.value is SlamLevel.SOLO_SLAM

        def get_base_points(self) -> int:
            if isinstance(self.value, SlamLevel):
                return self.value.base_value
            return self.value

        def get_slam_card_substitute(self) -> int:
            if isinstance(self.value, SlamLevel):
                return self.value.base_value
            return 0

        def get_multiplier(self) -> int:
            if self.redouble:
                return 4
            if self.double:
                return 2
            return 1

    class _StubRound:
        def __init__(self, *, round_number, contract, round_scores,
                     team_tricks=None, belote_holder=None,
                     contract_made=None):
            self.round_number = round_number
            self.contract = contract
            self.round_scores = round_scores
            self.team_tricks = team_tricks or {}
            # Belote holder (player object exposing ``.team.name``) and
            # the engine's canonical made/failed flag. ``contract_made``
            # left None lets ``RichView._contract_made`` fall back to the
            # score heuristic, matching pre-flag behaviour for the simple
            # cases these stubs cover.
            self.belote_holder = belote_holder
            self.contract_made = contract_made

    def test_recap_made_contract_shows_check(self):
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        panel = _panel_round_recap(round_, {"North-South": 500, "East-West": 0})
        text = panel.renderable.plain
        assert "Round #3 recap" in panel.title.plain
        assert "Contract made" in text
        assert "162" in text  # round score, no leading "+"
        assert "+162" not in text
        assert "500" in text  # running NS total

    def test_recap_shows_trump_recall_line(self):
        """The recap spells out the contract trump on its own line."""
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        panel = _panel_round_recap(round_, {"North-South": 500, "East-West": 0})
        text = panel.renderable.plain
        assert "Trump:" in text
        assert "♥ Hearts" in text

    def test_recap_trump_line_omits_star(self):
        """The recap's Trump line drops the ★ flourish (it stays plain).

        The star is reserved for the in-game Round panel; nothing else in
        the recap renders a ★, so its absence is asserted panel-wide.
        """
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        panel = _panel_round_recap(round_, {"North-South": 500, "East-West": 0})
        text = panel.renderable.plain
        assert "♥ Hearts" in text
        assert "★" not in text

    def test_recap_outcome_holds_tally_scoring_holds_round_points(
        self, four_players
    ):
        """Section placement after the refactor: the factual play tally
        (Tricks points / Last trick / Belote / Total) sits under Outcome,
        while the rolled-up Round points sits under Scoring. On this
        normal-made round the Scoring Round points equals trick points +
        last trick + belote per side."""
        view = RichView()
        north, east, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # N-S takes one trick worth A♥ (trump ace = 11), wins the last
        # trick (+10) and holds the belote pair (+20) → 41 round points.
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        ns_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 141, "East-West": 0},
            team_tricks={"North-South": [ns_trick], "East-West": []},
            belote_holder=north,
        )
        round_.last_trick_winner = north
        breakdown = _recap_breakdown(round_)
        ns = breakdown["North-South"]
        # Round points is the sum of the three factual Outcome rows.
        assert (
            ns["round_points"]
            == ns["trick_points"] + ns["last_trick"] + ns["belote"]
            == 41
        )
        text = _panel_round_recap(
            round_, {"North-South": 141, "East-West": 0}
        ).renderable.plain
        outcome, scoring = text.split("Scoring")
        # Tally rows (and their Total) live above the Scoring rule, the
        # rolled-up Round points below it.
        for row in ("Tricks points", "Last trick", "Belote", "Total"):
            assert row in outcome
            assert row not in scoring
        assert "Round points" in scoring
        assert "Round points" not in outcome

    def test_recap_failed_contract_shows_cross(self):
        view = RichView()
        contract = self._StubContract(120, Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 280, "East-West": 0},
        )
        panel = _panel_round_recap(round_, {"North-South": 280, "East-West": 0})
        text = panel.renderable.plain
        assert "Contract failed" in text

    def test_recap_uses_verbose_doubled_marker(self):
        """The recap spells out 'doubled' rather than the ×2 glyph."""
        view = RichView()
        contract = self._StubContract(
            110, Suit.SPADES, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 320},
        )
        panel = _panel_round_recap(round_, {"North-South": 0, "East-West": 320})
        text = panel.renderable.plain
        assert "doubled" in text
        assert "×2" not in text

    def test_recap_all_passed(self):
        view = RichView()
        round_ = self._StubRound(
            round_number=5,
            contract=None,
            round_scores={"North-South": 0, "East-West": 0},
        )
        panel = _panel_round_recap(round_, {"North-South": 0, "East-West": 0})
        text = panel.renderable.plain
        assert "All passed" in text
        # No made/failed line for an all-passed round.
        assert "made" not in text
        assert "failed" not in text
        # Outcome table harmonizes with Scoring: em-dashes, not zeros, on
        # the Tricks won, Total and Round points rows when nothing was played.
        for line in text.splitlines():
            if (
                "Tricks won" in line
                or "Total" in line
                or "Round points" in line
            ):
                assert "0" not in line
                assert "—" in line

    def test_recap_includes_belote_when_holder_holds_kq_of_trump(
        self, four_players
    ):
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # Belote follows the *holder* of K+Q of trump, not who captures
        # them in a trick — so the recap reads ``belote_holder``.
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 200, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
            belote_holder=north,
        )
        panel = _panel_round_recap(round_, {"North-South": 200, "East-West": 0})
        text = panel.renderable.plain
        # The Belote row carries the holder's 20, with no leading "+".
        # (The "+" in the "K + Q" label is not a sign — guard the value.)
        belote_line = next(
            line for line in text.splitlines() if "Belote" in line
        )
        assert "20" in belote_line
        assert "+20" not in belote_line

    def test_recap_shows_card_points_sum_per_team(self, four_players):
        """Card-points row shows the trump-aware sum across each team's
        tricks (plus the trick count)."""
        view = RichView()
        north, east, south, west = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # N-S took J♥ (20) + 9♥ (14) + A♠ (11) = 45 across two tricks.
        ns_trick1 = Trick()
        ns_trick1.add_play(north, Card(Suit.HEARTS, Rank.JACK))
        ns_trick1.add_play(east, Card(Suit.HEARTS, Rank.SEVEN))
        ns_trick2 = Trick()
        ns_trick2.add_play(south, Card(Suit.SPADES, Rank.ACE))
        ns_trick2.add_play(west, Card(Suit.HEARTS, Rank.NINE))
        # E-W took two low tricks worth 0 + 0 = 0.
        ew_trick = Trick()
        ew_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 145, "East-West": 0},
            team_tricks={
                "North-South": [ns_trick1, ns_trick2],
                "East-West": [ew_trick],
            },
        )
        panel = _panel_round_recap(
            round_, {"North-South": 145, "East-West": 0}
        )
        text = panel.renderable.plain
        # Trump-aware card points:
        #   ns_trick1: J♥(20) + 7♥(0)  = 20
        #   ns_trick2: A♠(11) + 9♥(14) = 25
        # N-S total = 45 — shown in the Outcome "Tricks points" row.
        assert "45" in text
        assert "Outcome" in text
        assert "Tricks won" in text
        assert "Tricks points" in text
        # The rolled-up tally lives in the Scoring sub-table now.
        assert "Round points" in text

    def test_recap_round_points_sum_pile_last_trick_and_belote(
        self, four_players
    ):
        """Outcome ``round_points`` = trump-aware pile + last trick (10)
        + belote (20), the honest play tally per team."""
        view = RichView()
        north, east, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # N-S takes one trick worth A♥ (trump ace = 11).
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        ns_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 141, "East-West": 0},
            team_tricks={"North-South": [ns_trick], "East-West": []},
            belote_holder=north,
        )
        round_.last_trick_winner = north
        breakdown = _recap_breakdown(round_)
        # 11 (A♥) + 10 (last trick) + 20 (belote) = 41.
        assert breakdown["North-South"]["round_points"] == 41
        assert breakdown["East-West"]["round_points"] == 0

    def test_recap_round_points_survive_winner_takes_all_round(
        self, four_players
    ):
        """In a doubled/failed round the Scoring card row is dashed, but
        ``round_points`` still reports the real pile each side captured."""
        view = RichView()
        north, east, *_ = four_players
        # Doubled contract by N-S that fails — E-W scores winner-takes-all.
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        ew_trick = Trick()
        ew_trick.add_play(east, Card(Suit.HEARTS, Rank.JACK))  # trump J = 20
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 320},
            team_tricks={"North-South": [], "East-West": [ew_trick]},
            contract_made=False,
        )
        breakdown = _recap_breakdown(round_)
        ew = breakdown["East-West"]
        # Scoring zeroes the card row (winner-takes-all formula)...
        assert ew["cards_count"] is False
        # ...but the real captured pile still shows in round_points.
        assert ew["round_points"] == 20

    def test_recap_outcome_total_sums_the_tally(self, four_players):
        """The Outcome table closes with a Total row equal to the per-side
        honest tally — trick points + last trick + belote."""
        view = RichView()
        north, east, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # N-S: A♥ (11) + last trick (10) + belote (20) = 41.
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        ns_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 141, "East-West": 0},
            team_tricks={"North-South": [ns_trick], "East-West": []},
            belote_holder=north,
        )
        round_.last_trick_winner = north
        text = _panel_round_recap(
            round_, {"North-South": 141, "East-West": 0}
        ).renderable.plain
        outcome = text.split("Scoring")[0]
        total_line = next(
            line for line in outcome.splitlines() if "Total" in line
        )
        # 11 + 10 + 20 = 41, with no leading "+".
        assert "41" in total_line
        assert "+" not in total_line

    def test_recap_scoring_round_points_belote_only_when_contre(
        self, four_players
    ):
        """On a chuté/contré round the captured pile stops scoring, so the
        Scoring 'Round points' row collapses to the belote the holder keeps
        — while the Outcome 'Total' still reports the full captured tally."""
        view = RichView()
        north, east, *_ = four_players
        # N-S declares doubled, fails; N-S still captured A♥ (11) and holds
        # the belote (20). E-W took the last trick.
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        ns_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 20, "East-West": 360},
            team_tricks={"North-South": [ns_trick], "East-West": []},
            belote_holder=north,
            contract_made=False,
        )
        round_.last_trick_winner = east  # der goes to E-W, not N-S
        text = _panel_round_recap(
            round_, {"North-South": 20, "East-West": 360}
        ).renderable.plain
        outcome, scoring = text.split("Scoring")
        # Outcome Total = 11 (A♥) + 0 (no der) + 20 (belote) = 31.
        total_line = next(
            line for line in outcome.splitlines() if "Total" in line
        )
        assert "31" in total_line
        # Scoring Round points = belote only (20), not the 31 captured.
        rp_line = next(
            line for line in scoring.splitlines() if "Round points" in line
        )
        assert "20" in rp_line
        assert "31" not in rp_line

    def test_recap_scoring_round_points_dashed_when_chute_no_belote(
        self, four_players
    ):
        """A failed contract with no belote held → the Scoring 'Round
        points' row dashes out entirely (nothing of the pile scores)."""
        view = RichView()
        north, east, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        ew_trick = Trick()
        ew_trick.add_play(east, Card(Suit.HEARTS, Rank.JACK))  # E-W captures
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 260},
            team_tricks={"North-South": [], "East-West": [ew_trick]},
            contract_made=False,
        )
        text = _panel_round_recap(
            round_, {"North-South": 0, "East-West": 260}
        ).renderable.plain
        scoring = text.split("Scoring")[1]
        rp_line = next(
            line for line in scoring.splitlines() if "Round points" in line
        )
        # No belote anywhere → both sides dash on the scoring roll-up.
        assert "—" in rp_line
        assert "20" not in rp_line

    def test_recap_no_plus_signs_in_made_round(self, four_players):
        """Regression guard: no leading '+' survives anywhere in the recap
        after the sign cleanup, on a normal made round with bonuses."""
        view = RichView()
        north, east, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        ns_trick = Trick()
        ns_trick.add_play(north, Card(Suit.HEARTS, Rank.ACE))
        ns_trick.add_play(east, Card(Suit.CLUBS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 141, "East-West": 0},
            team_tricks={"North-South": [ns_trick], "East-West": []},
            belote_holder=north,
        )
        round_.last_trick_winner = north
        text = _panel_round_recap(
            round_, {"North-South": 141, "East-West": 0}
        ).renderable.plain
        # No signed numbers remain (a "+" before a digit). The literal "+"
        # in the "Belote (K + Q ♥)" label is not a sign and is allowed.
        assert re.search(r"\+\d", text) is None

    def test_recap_unannounced_capot_substitutes_250_and_folds_der(
        self, four_players
    ):
        """Unannounced capot: the Outcome 'Tricks points' row reads 250
        (the flat substitute), 'Last trick' is folded in (0), and the
        contract + substitute still sum to the round score."""
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.SPADES, "North-South")
        # N-S swept all 8 tricks. Filler cards — the engine's 250
        # substitute, not the raw pile, is what the recap must show.
        ns_tricks = []
        for _ in range(8):
            tr = Trick()
            tr.add_play(north, Card(Suit.CLUBS, Rank.SEVEN))
            ns_tricks.append(tr)
        round_ = self._StubRound(
            round_number=6,
            contract=contract,
            round_scores={"North-South": 350, "East-West": 0},
            team_tricks={"North-South": ns_tricks, "East-West": []},
            contract_made=True,
        )
        round_.unannounced_capot = UnannouncedSlam.GRAND_SLAM  # north swept personally
        round_.last_trick_winner = north  # der would be +10 — must fold in
        breakdown = _recap_breakdown(round_)
        ns = breakdown["North-South"]
        assert ns["trick_points"] == 250
        assert ns["last_trick"] == 0
        assert ns["card_points"] == 250
        assert ns["card_points_substituted"] is True
        assert ns["contract"] == 100
        assert ns["round_points"] == 250
        # Invariant preserved: contract + card_points + dix + belote == score.
        assert (
            ns["contract"] + ns["card_points"] + ns["dix_de_der"] + ns["belote"]
            == 350
        )
        text = _panel_round_recap(
            round_, {"North-South": 350, "East-West": 0}
        ).renderable.plain
        assert "250" in text
        # The der is folded into the substitute — no stray +10 in the row.
        outcome = text.split("Scoring")[0]
        last_trick_line = next(
            line for line in outcome.splitlines() if "Last trick" in line
        )
        assert "+10" not in last_trick_line

    @pytest.mark.parametrize(
        "marker, expected_tag",
        [
            (UnannouncedSlam.SLAM, "Slam"),
            (UnannouncedSlam.GRAND_SLAM, "Grand Slam"),
        ],
    )
    def test_recap_capot_tags_the_trick_points_row(
        self, four_players, marker, expected_tag
    ):
        """The unannounced-capot marker surfaces its label on the Trick
        points row to explain the 250 substitute."""
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(90, Suit.HEARTS, "North-South")
        ns_tricks = []
        for _ in range(8):
            tr = Trick()
            tr.add_play(north, Card(Suit.CLUBS, Rank.SEVEN))
            ns_tricks.append(tr)
        round_ = self._StubRound(
            round_number=7,
            contract=contract,
            round_scores={"North-South": 340, "East-West": 0},
            team_tricks={"North-South": ns_tricks, "East-West": []},
            contract_made=True,
        )
        round_.unannounced_capot = marker
        text = _panel_round_recap(
            round_, {"North-South": 340, "East-West": 0}
        ).renderable.plain
        assert expected_tag in text
        assert "250" in text

    def test_recap_shows_dix_de_der_for_last_trick_winner(self, four_players):
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        last_trick = Trick()
        last_trick.add_play(north, Card(Suit.HEARTS, Rank.SEVEN))
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 110, "East-West": 0},
            team_tricks={"North-South": [last_trick], "East-West": []},
        )
        round_.last_trick_winner = north
        panel = _panel_round_recap(
            round_, {"North-South": 110, "East-West": 0}
        )
        text = panel.renderable.plain
        # The Last trick row carries the der's 10, with no leading "+".
        last_trick_line = next(
            line for line in text.splitlines() if "Last trick" in line
        )
        assert "10" in last_trick_line
        assert "+10" not in last_trick_line

    def test_recap_contract_row_shows_contract_value_when_made_normal(
        self, four_players
    ):
        """100 ♥ made by N-S → 'Contract' row shows +100 on N-S column,
        em-dash on E-W."""
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["North-South"]["contract"] == 100
        assert breakdown["East-West"]["contract"] == 0
        # Cards / dix / belote DO contribute on a normal-made contract.
        assert breakdown["North-South"]["cards_count"] is True
        assert breakdown["East-West"]["cards_count"] is True

    def test_recap_contract_row_uses_slam_base_when_made(self, four_players):
        """A made Slam normal: the contract row carries the base (250)
        and the card-points row carries the flat substitute (250),
        summing to the engine's 500. Dix de der does not contribute;
        the row label flips to "(subst.)"."""
        view = RichView()
        contract = self._StubContract(SlamLevel.SLAM, Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 500},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        # Slam normal: base = 250, substitute = 250, mult = 1.
        assert breakdown["East-West"]["contract"] == 250
        assert breakdown["East-West"]["card_points"] == 250
        assert breakdown["East-West"]["card_points_substituted"] is True
        assert breakdown["East-West"]["cards_count"] is True
        # Dix de der is no longer counted on Slam family rounds.
        assert breakdown["East-West"]["dix_count"] is False
        assert breakdown["East-West"]["dix_de_der"] == 0
        # Losing side: zeros everywhere except belote (not tested here).
        assert breakdown["North-South"]["contract"] == 0
        assert breakdown["North-South"]["card_points"] == 0
        assert breakdown["North-South"]["card_points_substituted"] is True

    def test_recap_contract_row_uses_slam_grid_when_failed(self, four_players):
        """Failed Slam: defender wins the at-risk amount split into
        contract (250) + substituted card points (250)."""
        view = RichView()
        contract = self._StubContract(SlamLevel.SLAM, Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 500, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["North-South"]["contract"] == 250
        assert breakdown["North-South"]["card_points"] == 250
        assert breakdown["North-South"]["card_points_substituted"] is True
        assert breakdown["North-South"]["cards_count"] is True
        assert breakdown["East-West"]["contract"] == 0
        assert breakdown["East-West"]["card_points"] == 0
        assert breakdown["East-West"]["cards_count"] is False

    def test_recap_contract_row_uses_solo_slam_grid_when_made(self, four_players):
        """Made Solo Slam normal: contract = 500, substitute = 500,
        sum = 1000."""
        view = RichView()
        contract = self._StubContract(SlamLevel.SOLO_SLAM, Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 1000},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 500
        assert breakdown["East-West"]["card_points"] == 500
        assert breakdown["East-West"]["card_points_substituted"] is True
        assert breakdown["North-South"]["contract"] == 0
        assert breakdown["North-South"]["card_points"] == 0

    def test_recap_contract_row_uses_solo_slam_doubled_grid(self, four_players):
        """Doubled Solo Slam made: both halves scale with the multiplier.
        Contract = 500 * 2 = 1000; substitute = 500 * 2 = 1000; sum = 2000."""
        view = RichView()
        contract = self._StubContract(
            SlamLevel.SOLO_SLAM, Suit.SPADES, "East-West", double=True
        )
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 2000},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 1000
        assert breakdown["East-West"]["card_points"] == 1000
        assert breakdown["East-West"]["card_points_substituted"] is True

    def test_recap_contract_row_includes_full_bonus_when_doubled_made(
        self, four_players
    ):
        """When the engine substitutes the flat 160+base*mult bonus
        (doubled or redoubled made), the 'Contract' row carries the
        full amount and the cards/dix/belote rows are zeroed for the
        attacker so the breakdown sums to round_score."""
        view = RichView()
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 360, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        # 160 + 100*2 = 360
        assert breakdown["North-South"]["contract"] == 360
        # Attacker's cards/dix/belote are ignored by the engine — the
        # recap reflects that so the addition matches round_score.
        assert breakdown["North-South"]["cards_count"] is False
        assert breakdown["North-South"]["card_points"] == 0
        assert breakdown["North-South"]["dix_de_der"] == 0
        assert breakdown["North-South"]["belote"] == 0

    def test_recap_contract_row_includes_full_bonus_when_redoubled_made(
        self, four_players
    ):
        view = RichView()
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", redouble=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 560, "East-West": 0},  # 160 + 100*4
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        # 160 + 100*4 = 560
        assert breakdown["North-South"]["contract"] == 560
        assert breakdown["North-South"]["cards_count"] is False

    def test_recap_contract_row_shows_defender_bonus_when_failed(
        self, four_players
    ):
        """100 ♥ failed by N-S → E-W gets (160 + 100) * 1 = 260 in
        their 'Contract' row; their cards/dix/belote are zeroed."""
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 260},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 260
        assert breakdown["North-South"]["contract"] == 0
        # Defender's cards/dix/belote don't contribute on a failed
        # contract — the engine pays them a flat bonus instead.
        assert breakdown["East-West"]["cards_count"] is False
        # Attacker gets 0 on a failed contract; their cards/dix/belote
        # also don't contribute (round_score is 0).
        assert breakdown["North-South"]["cards_count"] is False

    def test_recap_contract_row_failed_doubled_winner_takes_160_plus_cm(
        self, four_players
    ):
        """Failed 100 ♥ ×2 by N-S → E-W wins 160 + 100*2 = 360 (same
        stake as a doubled made declarer — winner-takes-all)."""
        view = RichView()
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 360},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 360
        # Loser scores nothing (no belote here).
        assert breakdown["North-South"]["contract"] == 0
        assert breakdown["North-South"]["cards_count"] is False

    def test_recap_doubled_made_defender_scores_zero(self, four_players):
        """Doubled contract made → the losing defender's breakdown is all
        zeros (winner-takes-all). Mirrors the engine's Problem-2 fix."""
        view = RichView()
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 360, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = _recap_breakdown(round_)
        ew = breakdown["East-West"]
        assert ew["contract"] == 0
        assert ew["cards_count"] is False
        assert ew["card_points"] == 0
        assert ew["dix_de_der"] == 0
        assert ew["belote"] == 0

    def test_recap_loser_keeps_belote_when_doubled(self, four_players):
        """The one thing a losing side keeps is its belote — the recap
        shows +20 for the holder even when it lost a doubled round."""
        view = RichView()
        _north, east, _south, _west = four_players
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 360, "East-West": 20},
            team_tricks={"North-South": [], "East-West": []},
            belote_holder=east,  # losing defender holds the pair
        )
        breakdown = _recap_breakdown(round_)
        ew = breakdown["East-West"]
        assert ew["belote_count"] is True
        assert ew["belote"] == 20
        assert ew["contract"] == 0
        assert ew["cards_count"] is False
        # The four components still sum to the engine's round score.
        assert (
            ew["contract"] + ew["card_points"] + ew["dix_de_der"] + ew["belote"]
            == 20
        )

    def test_recap_panel_renders_contract_row(self, four_players):
        """End-to-end: the rendered panel contains a 'Contract' row."""
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        panel = _panel_round_recap(
            round_, {"North-South": 500, "East-West": 0}
        )
        text = panel.renderable.plain
        assert "Contract " in text  # row label
        assert "100" in text  # attacker contract bonus, no leading "+"
        assert "+100" not in text

    def test_recap_breakdown_sums_to_round_score_normal_made(
        self, four_players
    ):
        """Invariant: for any team, the four component rows must sum
        to the engine's round_score. This is the test for the normal
        (un-doubled) made case."""
        view = RichView()
        north, east, south, west = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # N-S took two tricks; sum of card.get_points(♥) =
        #   J♥(20)+7♥(0)+9♥(14)+8♥(0) = 34
        #   A♠(11)+7♠(0)+K♠(4)+8♠(0)  = 15
        # Total card points: 49.
        ns_trick1 = Trick()
        for p, c in [
            (north, Card(Suit.HEARTS, Rank.JACK)),
            (east, Card(Suit.HEARTS, Rank.SEVEN)),
            (south, Card(Suit.HEARTS, Rank.NINE)),
            (west, Card(Suit.HEARTS, Rank.EIGHT)),
        ]:
            ns_trick1.add_play(p, c)
        ns_trick2 = Trick()
        for p, c in [
            (north, Card(Suit.SPADES, Rank.ACE)),
            (east, Card(Suit.SPADES, Rank.SEVEN)),
            (south, Card(Suit.SPADES, Rank.KING)),
            (west, Card(Suit.SPADES, Rank.EIGHT)),
        ]:
            ns_trick2.add_play(p, c)
        # Engine score = 100 (contract) + 49 (cards) + 10 (dix) = 159.
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 159, "East-West": 0},
            team_tricks={
                "North-South": [ns_trick1, ns_trick2],
                "East-West": [],
            },
        )
        round_.last_trick_winner = north
        breakdown = _recap_breakdown(round_)
        ns = breakdown["North-South"]
        sum_ns = (
            ns["contract"]
            + ns["card_points"]
            + ns["dix_de_der"]
            + ns["belote"]
        )
        assert sum_ns == 159

    def test_recap_table_uses_trump_glyph_in_belote_label(self, four_players):
        """The Belote row label reflects the actual trump suit."""
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.SPADES, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 200, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        panel = _panel_round_recap(
            round_, {"North-South": 200, "East-West": 0}
        )
        text = panel.renderable.plain
        # Spade glyph in the Belote row, not the hearts glyph.
        assert "Belote (K + Q ♠)" in text

    def _capture_recap_prompt(self, view, round_, scores, **kwargs) -> str:
        """Run ``show_round_recap`` and return the printed plain text.

        Panels carry a ``renderable`` Text whose ``.plain`` is the
        prompt copy we want to assert on. Walk every printed argument
        and concatenate any plain-text payloads we can extract.
        """
        view.console.input = lambda *_a, **_kw: ""
        view.console.clear = lambda *_a, **_kw: None
        captured: list[str] = []
        def _record(*args, **_kw):
            for a in args:
                if hasattr(a, "plain"):
                    captured.append(a.plain)
                elif hasattr(a, "renderable") and hasattr(a.renderable, "plain"):
                    captured.append(a.renderable.plain)
                else:
                    captured.append(str(a))
        view.console.print = _record
        view.show_round_recap(round_, scores, **kwargs)
        return "\n".join(captured)

    def test_show_round_recap_default_prompt(self):
        """Without ``is_final`` the prompt invites the next deal."""
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        output = self._capture_recap_prompt(
            view, round_, {"North-South": 162, "East-West": 0}
        )
        assert "deal the next round" in output
        assert "final score" not in output

    def test_show_round_recap_final_prompt(self):
        """With ``is_final=True`` the prompt points at the final score."""
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=10,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        output = self._capture_recap_prompt(
            view, round_, {"North-South": 1620, "East-West": 1300},
            is_final=True,
        )
        assert "final score" in output
        assert "deal the next round" not in output
