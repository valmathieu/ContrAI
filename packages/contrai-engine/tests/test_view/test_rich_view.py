"""Tests for the :class:`~contrai_engine.view.rich_view.RichView` class.

The pure helpers that used to live in ``rich_view`` now have their own
modules and test files (``test_formatting`` / ``test_parsing`` /
``test_bidding_rules`` / ``test_state_helpers``). What remains here is the
stateful, ``self``-coupled behaviour: the engine hooks (bid/card pacing,
event log, belote announcements), the input loops, and the in-game /
recap panel builders driven off ``RichView`` state.

The deeper ``Panel``/``Table`` rendering is smoke-validated by the
``uv run contrai`` pass; these tests assert titles and key text only.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from contrai_core import Auction, Card, Rank, Suit, Trick
from contrai_core.bid import ContractBid, DoubleBid, PassBid
from contrai_engine.view.rich_view import RichView
from contrai_engine.view.screens.bidding import (
    _bidding_prompt_text,
    _panel_bidding_history,
    _render_bidding_diamond,
)
from contrai_engine.view.screens.trick import (
    _panel_current_trick,
    _panel_hand,
    _panel_last_trick,
    _panel_round,
    _render_diamond,
)


# ======================================================================
# _redouble_available_to + adaptive bid prompt
# ======================================================================


class TestBiddingPromptHint:
    """End-to-end test that the prompt text adapts to the bid history."""

    def _prompt(self, history, next_player):
        view = RichView()
        return _bidding_prompt_text(history, next_player).plain

    def test_no_double_hint_before_any_contract(self, four_players):
        """With nothing but a Pass on the table there's no contract to
        double, so the hint offers only bidding and passing."""
        north, _east, _south, _west = four_players
        history = [(north, "Pass")]
        text = self._prompt(history, north)
        assert "double" not in text
        assert "redouble" not in text
        assert "80 H" in text and "pass" in text

    def test_redouble_hint_when_contractor_was_doubled(
        self, four_players
    ):
        north, east, _south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        text = self._prompt(history, north)
        assert "redouble" in text
        # The default '80 H' example shouldn't appear in the redouble
        # variant since the only meaningful play is pass/redouble.
        assert "80 H" not in text

    def test_no_double_hint_when_own_partner_holds_contract(
        self, four_players
    ):
        """The reported bug: N (South's partner) holds the contract, so
        the hint must NOT advertise 'double' to South."""
        north, east, _south, west = four_players
        history = [
            (east, "Pass"),
            (north, (90, Suit.SPADES)),
            (west, "Pass"),
        ]
        # South is North's partner — doubling own side is illegal.
        _, _, south, _ = four_players
        text = self._prompt(history, south)
        assert "double" not in text
        # Bidding higher and passing are still on the table — and the
        # example tracks the 90♠ contract, so it offers 100, not 80.
        assert "100 H" in text and "pass" in text
        assert "80 H" not in text

    def test_double_hint_when_opponent_holds_contract(self, four_players):
        """East (an opponent of South) holds the contract → offer double."""
        _north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES))]
        text = self._prompt(history, south)
        assert "double" in text

    def test_example_tracks_highest_contract(self, four_players):
        """The reported request: with 90♦ standing, the worked example
        must propose at least 100, never the bare 80 floor."""
        north, east, south, _west = four_players
        history = [
            (east, (80, Suit.HEARTS)),
            (south, (90, Suit.DIAMONDS)),
        ]
        text = self._prompt(history, north)
        assert "100 H" in text
        assert "80 H" not in text and "90 H" not in text

    def test_example_dropped_when_only_slam_outranks(self, four_players):
        """At 180 only Slam/SoloSlam are legal raises, so the numeric
        example is dropped rather than suggesting an illegal bid."""
        north, east, _south, _west = four_players
        history = [(east, (180, Suit.HEARTS))]
        text = self._prompt(history, north)
        # No numeric contract example, but passing/doubling remain.
        assert "180 H" not in text
        assert "pass" in text and "double" in text


class TestRequestBidActionLegality:
    """Regression: an illegal human bid must re-prompt, never crash.

    Reproduces the reported traceback — South types 'double' against
    their partner North's 90♠ contract. Before the fix this escaped to
    ``Auction.apply`` and raised ``IllegalBidError``; now the view
    rejects it inline and loops for fresh input.
    """

    def _drive(self, four_players, raws):
        """Run request_bid_action feeding *raws* as successive inputs.

        Returns ``(view, notices, inputs)``. Rendering and console I/O
        are stubbed so the loop runs headless. ``notices`` collects the
        ``notice`` Text handed to each ``_render_in_game`` frame — the
        rejection now rides inside the frame rather than a standalone
        ``console.print`` (which a re-render's ``console.clear()`` would
        bury in scrollback).
        """
        view = RichView()
        inputs = iter(raws)
        notices: list[str] = []

        def fake_render(**kwargs):
            note = kwargs.get("notice")
            notices.append(getattr(note, "plain", None) if note else None)

        view._render_in_game = fake_render
        view.console.input = lambda *a, **k: next(inputs)
        return view, notices, inputs

    def test_double_own_partner_reprompts_then_passes(self, four_players):
        north, east, south, west = four_players
        auction = Auction.empty()
        for bid in (
            PassBid(east),
            ContractBid(north, 90, Suit.SPADES),
            PassBid(west),
        ):
            auction = auction.apply(bid)

        view, notices, _ = self._drive(four_players, ["double", "pass"])
        result = view.request_bid_action(south, auction)

        # The illegal Double was rejected inline (no exception), and the
        # loop accepted the follow-up Pass.
        assert isinstance(result, PassBid)
        # The rejection rode inside the re-prompt frame (notice arg), not
        # a standalone print: the first frame had no notice, the retry
        # frame carried the "own side" reason.
        assert notices[0] is None
        assert any(n and "own side" in n for n in notices)
        # And whatever it returns is genuinely legal — the property the
        # crash violated.
        assert auction.is_legal(result)

    def test_legal_double_against_opponent_is_accepted(self, four_players):
        _north, east, south, _west = four_players
        auction = Auction.empty().apply(ContractBid(east, 90, Suit.SPADES))

        view, notices, _ = self._drive(four_players, ["double"])
        result = view.request_bid_action(south, auction)

        assert isinstance(result, DoubleBid)
        # Accepted on the first frame — no rejection notice was ever set.
        assert notices == [None]


# ======================================================================
# _panel_round — round number in the title
# ======================================================================


class TestPanelBiddingHistorySeparator:
    """Bidding rounds break onto separate lines."""

    def test_single_line_within_first_round(self, four_players):
        view = RichView()
        north, east, south, west = four_players
        bids = [
            (south, "Pass"),
            (east, "Pass"),
            (north, (80, Suit.HEARTS)),
            (west, "Pass"),
        ]
        text = _panel_bidding_history(bids).renderable.plain
        assert "\n" not in text

    def test_newline_between_rounds(self, four_players):
        view = RichView()
        north, east, south, west = four_players
        bids = [
            (south, "Pass"),
            (east, "Pass"),
            (north, (80, Suit.HEARTS)),
            (west, "Pass"),
            # round 2 begins:
            (south, (100, Suit.HEARTS)),
            (east, "Pass"),
            (north, (130, Suit.HEARTS)),
            (west, "Double"),
        ]
        text = _panel_bidding_history(bids).renderable.plain
        # Exactly one line break between round 1 and round 2.
        assert text.count("\n") == 1
        # Each line opens with its round-number gutter.
        before, after = text.split("\n", 1)
        assert before.startswith("#1")
        assert after.startswith("#2")
        # Round 1 holds the first four bids; round 2 the next four.
        assert "W Pass" in before
        assert "S 100" in after

    def test_seats_align_vertically_across_rounds(self, four_players):
        """Each seat sits in the same column on every round's line."""
        view = RichView()
        north, east, south, west = four_players
        bids = [
            (south, "Pass"),
            (east, "Pass"),
            (north, (80, Suit.HEARTS)),
            (west, "Pass"),
            (south, (100, Suit.HEARTS)),
            (east, "Pass"),
            (north, (130, Suit.HEARTS)),
            (west, "Double"),
        ]
        text = _panel_bidding_history(bids).renderable.plain
        line1, line2 = text.split("\n", 1)
        # The seat letters start at identical offsets on both lines, so
        # the bids stack in vertical lanes despite differing bid widths.
        for letter in ("S", "E", "N", "W"):
            assert line1.index(f"{letter} ") == line2.index(f"{letter} ")


class TestOnBidMadePacing:
    """on_bid_made renders + sleeps for AI players, skips humans."""

    def test_ai_bid_calls_sleep_with_env_delay(
        self, monkeypatch, four_players
    ):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AI_BID_DELAY", "0.01")

        view = RichView()
        bid = ContractBid(north, 100, Suit.HEARTS)
        view.on_bid_made(north, bid, [bid])

        assert sleep_calls == [0.01]

    def test_human_bid_does_not_sleep(
        self, monkeypatch, four_players
    ):
        from contrai_engine.view import rich_view
        from contrai_engine.model.player import HumanPlayer

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))

        human = HumanPlayer("You", "South")
        human.team = four_players[0].team  # any team
        view = RichView()
        bid = PassBid(human)
        view.on_bid_made(human, bid, [bid])

        assert sleep_calls == []


class TestOnCardPlayedPacing:
    def test_ai_card_calls_sleep(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AI_CARD_DELAY", "0.01")

        trick = Trick()
        view = RichView()
        view.on_card_played(north, Card(Suit.HEARTS, Rank.ACE), trick)

        assert sleep_calls == [0.01]

    def test_human_card_does_not_sleep(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view
        from contrai_engine.model.player import HumanPlayer

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))

        human = HumanPlayer("You", "South")
        human.team = four_players[0].team
        view = RichView()
        view.on_card_played(human, Card(Suit.HEARTS, Rank.ACE), Trick())
        assert sleep_calls == []


class TestEventLog:
    """Rolling narrative log shown below the hand panel."""

    def _make_view(self, monkeypatch):
        """RichView with sleep patched out — we don't want real pauses."""
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        return RichView()

    def test_log_appends_and_trims(self, monkeypatch):
        view = self._make_view(monkeypatch)
        for i in range(view.LOG_MAX + 3):
            view._log(Text(f"line {i}"))
        assert len(view.event_log) == view.LOG_MAX
        # Earliest entries are dropped first.
        assert view.event_log[0].plain == f"line {3}"
        assert view.event_log[-1].plain == f"line {view.LOG_MAX + 2}"

    def test_on_bid_made_logs_styled_entry(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        bid = ContractBid(north, 100, Suit.HEARTS)
        view.on_bid_made(north, bid, [bid])
        assert any("bid 100" in line.plain for line in view.event_log)
        assert any("♥" in line.plain for line in view.event_log)

    def test_on_bid_made_logs_pass(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        view.on_bid_made(north, PassBid(north), [PassBid(north)])
        assert any(line.plain.endswith("passed.") for line in view.event_log)

    def test_on_card_played_logs(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        view.on_card_played(north, Card(Suit.HEARTS, Rank.JACK), Trick())
        # Card log: "N plays J♥."
        assert any("plays" in line.plain for line in view.event_log)
        assert any("J♥" in line.plain for line in view.event_log)

    def test_on_trick_complete_logs_winner_with_points(
        self, monkeypatch, four_players
    ):
        view = self._make_view(monkeypatch)
        north, east, south, west = four_players

        class _StubRound:
            def __init__(self, contract):
                self.contract = contract
                self.tricks = []
                self.team_tricks = {}

        class _StubContract:
            suit = Suit.HEARTS

        trick = Trick()
        # Build a real-ish trick. With Hearts trump, J♥(20)+A♥(11)+K♥(4)+Q♥(3)=38.
        trick.add_play(north, Card(Suit.HEARTS, Rank.JACK))
        trick.add_play(east, Card(Suit.HEARTS, Rank.ACE))
        trick.add_play(south, Card(Suit.HEARTS, Rank.KING))
        trick.add_play(west, Card(Suit.HEARTS, Rank.QUEEN))
        # Avoid blocking on console.input — patch it.
        view.console.input = lambda *_a, **_kw: ""
        view.on_trick_complete(trick, north, _StubRound(_StubContract()))

        win_line = view.event_log[-1].plain
        assert "wins trick" in win_line
        assert "38" in win_line

    def test_on_round_dealt_logs(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players

        class _StubRound:
            round_number = 5
            dealer = north

        view.on_round_dealt(_StubRound())
        assert any("Round #5" in line.plain for line in view.event_log)
        assert any("deals" in line.plain for line in view.event_log)

    def test_on_all_pass_redeal_logs(self, monkeypatch):
        view = self._make_view(monkeypatch)
        view.on_all_pass_redeal(round_=None)
        assert any("redealing" in line.plain for line in view.event_log)

    def test_on_contract_established_logs(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players

        class _StubContract:
            value = 100
            suit = Suit.HEARTS
            double = False
            redouble = False
            double_player = None
            redouble_player = None
            player = north
            team = north.team

        class _StubRound:
            contract = _StubContract()

        view.on_contract_established(_StubRound())
        line = view.event_log[-1].plain
        assert "Contract set:" in line
        # The contract short label embeds value + the taker's seat letter.
        assert "100" in line
        assert "by N" in line

    def test_on_contract_established_includes_double_multiplier(
        self, monkeypatch, four_players
    ):
        view = self._make_view(monkeypatch)
        _north, east, _south, west = four_players

        class _StubContract:
            value = 120
            suit = Suit.SPADES
            double = True
            redouble = False
            double_player = west
            redouble_player = None
            player = east
            team = east.team

        class _StubRound:
            contract = _StubContract()

        view.on_contract_established(_StubRound())
        line = view.event_log[-1].plain
        # Multiplier plus the coincheur's seat letter.
        assert "×2 by W" in line
        # Taker is still named.
        assert "by E" in line

    def test_on_contract_established_no_op_when_no_contract(
        self, monkeypatch
    ):
        view = self._make_view(monkeypatch)

        class _StubRound:
            contract = None

        view.on_contract_established(_StubRound())
        assert view.event_log == []

    def test_attach_resets_log(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        view._log(Text("from previous game"))
        # Attach without a real Game (just enough for the method to work).
        class _StubGame:
            def __init__(self):
                self.current_round = None
                self.scores = {"North-South": 0, "East-West": 0}

        view.attach(_StubGame(), target_score=1500)
        assert view.event_log == []


class TestBeloteAnnouncement:
    """Belote announcement hook + diamond badge."""

    def _make_view(self, monkeypatch):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        return RichView()

    class _StubContract:
        def __init__(self, suit):
            self.suit = suit
            class _T: pass
            self.team = _T()
            self.team.name = "North-South"

    class _StubRound:
        def __init__(self, contract, belote_state):
            self.contract = contract
            self.belote_state = belote_state
            self.tricks = []
            self.team_tricks = {}

    def test_on_belote_announced_logs_belote(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        round_ = self._StubRound(self._StubContract(Suit.HEARTS), {north: "belote"})
        view.on_belote_announced(north, "belote", round_)
        line = view.event_log[-1].plain
        assert "Belote" in line
        assert "Rebelote" not in line

    def test_on_belote_announced_logs_rebelote(self, monkeypatch, four_players):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        round_ = self._StubRound(self._StubContract(Suit.HEARTS),
                                 {north: "rebelote"})
        view.on_belote_announced(north, "rebelote", round_)
        assert "Rebelote" in view.event_log[-1].plain

    def test_on_belote_announced_sleeps(self, monkeypatch, four_players):
        """Announcement uses the AI card delay so it lands visibly."""
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AI_CARD_DELAY", "0.01")
        north, *_ = four_players
        view = RichView()
        round_ = self._StubRound(self._StubContract(Suit.HEARTS), {})
        view.on_belote_announced(north, "belote", round_)
        assert sleep_calls == [0.01]

    def test_diamond_renders_belote_badge_for_announcer(
        self, monkeypatch, four_players
    ):
        view = self._make_view(monkeypatch)
        north, *_ = four_players
        trick = Trick()
        # Empty trick is fine — the badge is keyed off belote_by_position.
        diamond = _render_diamond(
            trick,
            Suit.HEARTS,
            pending_position=None,
            winner_position=None,
            dimmed=False,
            width=42,
            belote_by_position={"North": "belote"},
        )
        text = diamond.plain
        assert "★ Belote" in text
        # The badge sits below the N slot, so the badge appears AFTER
        # "N · " in linear text order.
        assert text.index("N") < text.index("★ Belote")

    def test_diamond_badge_is_belote_regardless_of_kind(
        self, monkeypatch, four_players
    ):
        """After the second K-or-Q of trump (kind='rebelote'), the
        seat badge still reads '★ Belote' — the rebelote distinction
        lives only in the event log, not under the seat."""
        view = self._make_view(monkeypatch)
        diamond = _render_diamond(
            Trick(),
            Suit.HEARTS,
            pending_position=None,
            winner_position=None,
            dimmed=False,
            width=42,
            belote_by_position={"South": "rebelote"},
        )
        assert "★ Belote" in diamond.plain
        assert "Rebelote" not in diamond.plain

    def test_diamond_no_badge_when_state_empty(self, monkeypatch):
        view = self._make_view(monkeypatch)
        diamond = _render_diamond(
            Trick(),
            Suit.HEARTS,
            pending_position=None,
            winner_position=None,
            dimmed=False,
            width=42,
            belote_by_position=None,
        )
        assert "Belote" not in diamond.plain
        assert "Rebelote" not in diamond.plain


class TestBiddingDiamond:
    """The auction reuses the table diamond: each seat shows its latest bid."""

    class _StubRound:
        def __init__(self):
            self.round_number = 1
            self.contract = None
            self.dealer = None
            self.tricks = []
            self.team_tricks = {}
            self.belote_state = {}

    def test_each_seat_shows_its_latest_bid(self, four_players):
        view = RichView()
        north, east, south, west = four_players
        history = [
            (south, "Pass"),
            (west, (80, Suit.HEARTS)),
            (north, "Pass"),
        ]
        diamond = _render_bidding_diamond(
            history, pending_position=None, width=42
        )
        text = diamond.plain
        # West's bid renders as "80 ♥"; South and North passed.
        assert "80 ♥" in text
        assert "Pass" in text

    def test_pending_seat_marked_with_question(self, four_players):
        view = RichView()
        north, east, south, west = four_players
        diamond = _render_bidding_diamond(
            [(west, (80, Suit.HEARTS))],
            pending_position="North",
            width=42,
        )
        # North is on the move → "N ?"; West shows its standing bid.
        assert "N ?" in diamond.plain
        assert "80 ♥" in diamond.plain

    def test_seat_without_bid_shows_dot(self, four_players):
        view = RichView()
        diamond = _render_bidding_diamond(
            [], pending_position=None, width=42
        )
        # Empty auction: every seat is a placeholder dot, no "?".
        assert "·" in diamond.plain
        assert "?" not in diamond.plain

    def test_latest_bid_overwrites_earlier(self, four_players):
        """A second bid by the same seat replaces the first in the diamond."""
        view = RichView()
        north, east, south, west = four_players
        history = [
            (west, (80, Suit.HEARTS)),
            (north, (90, Suit.SPADES)),
            (east, "Pass"),
            (south, "Pass"),
            (west, (100, Suit.HEARTS)),
        ]
        text = _render_bidding_diamond(
            history, pending_position=None, width=42
        ).plain
        assert "100 ♥" in text
        assert "80 ♥" not in text

    def test_panel_current_trick_bidding_renders_diamond(self, four_players):
        """During bidding the Current-trick slot becomes the auction diamond."""
        view = RichView()
        north, east, south, west = four_players
        panel = _panel_current_trick(
            self._StubRound(),
            trick=None,
            phase="bidding",
            current_player=south,
            trick_winner=None,
            bidding_history=[(west, (80, Suit.HEARTS))],
        )
        assert panel.title.plain == "Bidding"
        body = panel.renderable.plain
        assert "80 ♥" in body
        # South is the human about to bid → seat marked, prompt line shown.
        assert "S ?" in body


class TestPanelRoundTitle:
    """The Round panel's title shows the active round number."""

    class _StubRound:
        # Minimal stand-in. _panel_round only reads round_number,
        # contract, dealer, tricks during this phase path.
        def __init__(self, round_number):
            self.round_number = round_number
            self.contract = None
            self.dealer = None
            self.tricks = []
            self.team_tricks = {}

    def test_title_contains_round_number(self):
        view = RichView()
        panel = _panel_round(self._StubRound(7), phase="bidding")
        assert "Round #7" in panel.title.plain

    def test_title_defaults_when_round_is_none(self):
        view = RichView()
        panel = _panel_round(None, phase="bidding")
        assert panel.title.plain.startswith("Round")
        # No # marker when there is no round to talk about.
        assert "#" not in panel.title.plain


class TestTrickPanelTitles:
    """Trick panel titles use the (#N) format."""

    class _StubRound:
        def __init__(self, tricks_done):
            self.round_number = 1
            self.contract = None
            self.dealer = None
            self.tricks = [object()] * tricks_done
            self.team_tricks = {}
            self.belote_state = {}

    def test_current_trick_title_uses_hash_format(self):
        view = RichView()
        # 4 tricks done, currently playing trick #5.
        panel = _panel_current_trick(
            self._StubRound(tricks_done=4),
            trick=Trick(),
            phase="playing",
            current_player=None,
            trick_winner=None,
        )
        assert "Current trick (#5)" in panel.title.plain

    def test_last_trick_title_uses_hash_format(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView()
        north, *_ = four_players
        trick = Trick()
        # Stub a completed trick.
        view.last_completed_trick = (trick, north)
        round_ = self._StubRound(tricks_done=7)
        panel = _panel_last_trick(round_, view.last_completed_trick)
        assert "Last trick (#7)" in panel.title.plain

    def test_last_trick_title_bare_when_no_round(self):
        view = RichView()
        # No last_completed_trick set → '(none)' panel with the bare
        # "Last trick" title.
        panel = _panel_last_trick(None, view.last_completed_trick)
        assert panel.title.plain == "Last trick"


# ======================================================================
# Hand panel — always visible while a human is seated
# ======================================================================


class TestPanelHandPersistence:
    """The hand slot stays visible across non-interactive frames.

    Bug: previously the panel was only rendered when ``current_player``
    was the human, so it vanished during AI bidding/play frames and the
    trick-won pause. Tests below lock the new "always render when a
    human is seated" contract, plus the empty-hand and non-interactive
    styling branches.
    """

    def _build_view_with_human(self):
        """RichView wired to a minimal Game-like stub holding one human."""
        from contrai_engine.model.player import HumanPlayer

        human = HumanPlayer("You", "South")
        human.team = None  # not exercised by these tests

        class _StubGame:
            def __init__(self, human):
                self.players = [human]
                self.current_round = None
                self.scores = {"North-South": 0, "East-West": 0}

        view = RichView()
        view.attach(_StubGame(human), target_score=1500)
        return view, human

    def test_find_human_returns_human_player(self):
        view, human = self._build_view_with_human()
        assert view._find_human_player() is human

    def test_find_human_returns_none_when_no_game(self):
        view = RichView()
        assert view._find_human_player() is None

    def test_panel_hand_empty_hand_renders_placeholder(self):
        """After the 8th trick the hand is empty — the cards row shows a
        single '(no cards left)' line so the slot stays in the layout
        rather than disappearing. No redundant second empty-state line."""
        view, human = self._build_view_with_human()
        human.hand.clear()
        panel = _panel_hand(
            human, trick=None, playable_cards=None,
            phase="trick_won", round_=None, interactive=False,
        )
        text = panel.renderable.plain
        assert "(no cards left)" in text
        assert "(hand empty)" not in text

    def test_panel_hand_non_interactive_omits_constraint_hint(self):
        """During AI/trick-won frames the hand renders neutrally — no
        green playable pills, no '↑ playable …' constraint hint."""
        view, human = self._build_view_with_human()
        human.hand.clear()
        human.hand.extend([
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
        ])
        # Pretend hearts is trump and clubs were led — interactive mode
        # would emit "must trump"; non-interactive mode must not.
        from contrai_core.trick import Trick as _Trick

        trick = _Trick()
        trick.add_play(human, Card(Suit.CLUBS, Rank.KING))
        panel = _panel_hand(
            human, trick=trick, playable_cards=[human.hand[1]],
            phase="playing", round_=None, interactive=False,
        )
        text = panel.renderable.plain
        assert "must trump" not in text
        assert "↑ playable" not in text
        # Size readout takes the hint slot.
        assert "2 cards remaining" in text

    def test_panel_hand_interactive_still_shows_constraint_hint(self):
        """The interactive path is unchanged — the constraint hint
        still appears when the human is the acting player."""
        view, human = self._build_view_with_human()
        human.hand.clear()
        human.hand.extend([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
        ])
        from contrai_core.trick import Trick as _Trick

        west_stub = type("_W", (), {"position": "West", "team": None})()
        trick = _Trick()
        trick.add_play(west_stub, Card(Suit.CLUBS, Rank.KING))
        # Stub a round with hearts trump so the explain helper knows
        # the human's hearts are trumps and emits the "must trump" hint.
        contract_stub = type("_C", (), {"suit": Suit.HEARTS})()
        round_stub = type("_R", (), {"contract": contract_stub})()
        panel = _panel_hand(
            human, trick=trick, playable_cards=list(human.hand),
            phase="playing", round_=round_stub, interactive=True,
        )
        text = panel.renderable.plain
        assert "must trump" in text


class TestRenderInGameHandSlot:
    """End-to-end: the hand slot persists across in-game frames."""

    def _make_view(self, monkeypatch):
        from contrai_engine.view import rich_view
        from contrai_engine.model.player import HumanPlayer

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        human = HumanPlayer("You", "South")
        human.team = None
        human.hand.clear()
        human.hand.extend([
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
        ])

        class _StubGame:
            def __init__(self, human):
                self.players = [human]
                self.current_round = None
                self.scores = {"North-South": 0, "East-West": 0}

        view = RichView()
        view.attach(_StubGame(human), target_score=1500)
        return view, human

    @staticmethod
    def _capture_render(view) -> list[str]:
        """Intercept console output and return the plain-text body of
        every panel/text printed during the next ``_render_in_game``.

        Walks Panel titles as well as renderables so assertions can
        target the ``Your hand (South)`` title line.
        """
        captured: list[str] = []

        def _record(*args, **_kw):
            for a in args:
                title = getattr(a, "title", None)
                if title is not None and hasattr(title, "plain"):
                    captured.append(title.plain)
                if hasattr(a, "plain"):
                    captured.append(a.plain)
                elif hasattr(a, "renderable") and hasattr(a.renderable, "plain"):
                    captured.append(a.renderable.plain)

        view.console.clear = lambda *_a, **_kw: None
        view.console.print = _record
        return captured

    def test_hand_visible_during_ai_bidding_frame(self, monkeypatch):
        """No current_player (AI just bid) — the hand must still render."""
        view, human = self._make_view(monkeypatch)
        captured = self._capture_render(view)
        view._render_in_game(
            phase="bidding",
            current_player=None,
            bidding_history=[],
            prompt_question=Text(""),
            mandatory=False,
        )
        combined = "\n".join(captured)
        assert "Your hand (South)" in combined

    def test_hand_visible_during_trick_won_frame(self, monkeypatch):
        """Trick-won frame uses current_player=None — hand must persist."""
        view, human = self._make_view(monkeypatch)
        captured = self._capture_render(view)
        view._render_in_game(
            phase="trick_won",
            current_player=None,
            current_trick=None,
            trick_winner=None,
            prompt_question=Text(""),
            mandatory=False,
        )
        combined = "\n".join(captured)
        assert "Your hand (South)" in combined

    def test_hand_omitted_when_no_human_seated(self, monkeypatch):
        """All-AI table (no human) — hand panel is correctly suppressed."""
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView()

        class _StubGame:
            players = []
            current_round = None
            scores = {"North-South": 0, "East-West": 0}

        view.attach(_StubGame(), target_score=1500)
        captured = self._capture_render(view)
        view._render_in_game(
            phase="bidding",
            current_player=None,
            bidding_history=[],
            prompt_question=Text(""),
            mandatory=False,
        )
        combined = "\n".join(captured)
        assert "Your hand" not in combined
