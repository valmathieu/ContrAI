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

import logging

import pytest
from rich.text import Text

from contrai_core import (
    Auction,
    Card,
    PRESETS,
    Play,
    Position,
    Rank,
    Rounding,
    RuleConfig,
    Suit,
    TeamSide,
    TurnDirection,
)
from contrai_core.bid import ContractBid, DoubleBid, PassBid
from contrai_engine.model.game import GameOverStatus
from contrai_engine.options import DebugOptions, TableAids
from contrai_engine.ruleset import SECTION_HEADINGS, SECTIONS, TableSetup
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
    """End-to-end test that the prompt text adapts to the auction state.

    The hint is derived from :meth:`Auction.legal_actions`, so each case
    builds a real :class:`Auction` from :class:`Bid` objects.
    """

    def _prompt(self, auction, next_player):
        return _bidding_prompt_text(auction, next_player).plain

    def test_no_double_hint_before_any_contract(self, four_players):
        """With nothing but a Pass on the table there's no contract to
        double, so the hint offers only bidding and passing."""
        north, _east, _south, _west = four_players
        auction = Auction((PassBid(north),))
        text = self._prompt(auction, north)
        assert "double" not in text
        assert "redouble" not in text
        assert "80 H" in text and "pass" in text

    def test_redouble_hint_when_contractor_was_doubled(
        self, four_players
    ):
        north, east, _south, _west = four_players
        auction = Auction((
            ContractBid(north, 100, Suit.HEARTS),
            DoubleBid(east),
        ))
        text = self._prompt(auction, north)
        assert "redouble" in text
        # The default '80 H' example shouldn't appear in the redouble
        # variant since the only meaningful play is pass/redouble.
        assert "80 H" not in text

    def test_no_double_hint_when_own_partner_holds_contract(
        self, four_players
    ):
        """The reported bug: N (South's partner) holds the contract, so
        the hint must NOT advertise 'double' to South."""
        north, east, south, west = four_players
        auction = Auction((
            PassBid(east),
            ContractBid(north, 90, Suit.SPADES),
            PassBid(west),
        ))
        # South is North's partner — doubling own side is illegal.
        text = self._prompt(auction, south)
        assert "double" not in text
        # Bidding higher and passing are still on the table — and the
        # example tracks the 90♠ contract, so it offers 100, not 80.
        assert "100 H" in text and "pass" in text
        assert "80 H" not in text

    def test_double_hint_when_opponent_holds_contract(self, four_players):
        """East (an opponent of South) holds the contract → offer double."""
        _north, east, south, _west = four_players
        auction = Auction((ContractBid(east, 90, Suit.SPADES),))
        text = self._prompt(auction, south)
        assert "double" in text

    def test_example_tracks_highest_contract(self, four_players):
        """The reported request: with 90♦ standing, the worked example
        must propose at least 100, never the bare 80 floor."""
        north, east, south, _west = four_players
        auction = Auction((
            ContractBid(east, 80, Suit.HEARTS),
            ContractBid(south, 90, Suit.DIAMONDS),
        ))
        text = self._prompt(auction, north)
        assert "100 H" in text
        assert "80 H" not in text and "90 H" not in text

    def test_example_dropped_when_only_slam_outranks(self, four_players):
        """At 180 only Slam/SoloSlam are legal raises, so the numeric
        example is dropped rather than suggesting an illegal bid."""
        north, east, _south, _west = four_players
        auction = Auction((ContractBid(east, 180, Suit.HEARTS),))
        text = self._prompt(auction, north)
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
        north, east, south, west = four_players
        bids = [
            PassBid(south),
            PassBid(east),
            ContractBid(north, 80, Suit.HEARTS),
            PassBid(west),
        ]
        text = _panel_bidding_history(bids).renderable.plain
        assert "\n" not in text

    def test_newline_between_rounds(self, four_players):
        north, east, south, west = four_players
        bids = [
            PassBid(south),
            PassBid(east),
            ContractBid(north, 80, Suit.HEARTS),
            PassBid(west),
            # round 2 begins:
            ContractBid(south, 100, Suit.HEARTS),
            PassBid(east),
            ContractBid(north, 130, Suit.HEARTS),
            DoubleBid(west),
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
        north, east, south, west = four_players
        bids = [
            PassBid(south),
            PassBid(east),
            ContractBid(north, 80, Suit.HEARTS),
            PassBid(west),
            ContractBid(south, 100, Suit.HEARTS),
            PassBid(east),
            ContractBid(north, 130, Suit.HEARTS),
            DoubleBid(west),
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

        human = HumanPlayer("You", Position.SOUTH)
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

        view = RichView()
        view.on_card_played(north, Card(Suit.HEARTS, Rank.ACE), ())

        assert sleep_calls == [0.01]

    def test_human_card_does_not_sleep(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view
        from contrai_engine.model.player import HumanPlayer

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))

        human = HumanPlayer("You", Position.SOUTH)
        human.team = four_players[0].team
        view = RichView()
        view.on_card_played(human, Card(Suit.HEARTS, Rank.ACE), ())
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
        view.on_card_played(north, Card(Suit.HEARTS, Rank.JACK), ())
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
                self.play_state = None

        class _StubContract:
            suit = Suit.HEARTS

        # A real-ish trick. With Hearts trump, J♥(20)+A♥(11)+K♥(4)+Q♥(3)=38.
        plays = (
            Play(north, Card(Suit.HEARTS, Rank.JACK)),
            Play(east, Card(Suit.HEARTS, Rank.ACE)),
            Play(south, Card(Suit.HEARTS, Rank.KING)),
            Play(west, Card(Suit.HEARTS, Rank.QUEEN)),
        )
        # Avoid blocking on console.input — patch it.
        view.console.input = lambda *_a, **_kw: ""
        view.on_trick_complete(plays, north, _StubRound(_StubContract()))

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
        view.on_all_pass_redeal()
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
        # The contract short label embeds value + trump glyph + the
        # taker's seat letter.
        assert "100 ♥" in line
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
        # Multiplier plus the double caller's seat letter.
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
                self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}

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
            self.team.name = TeamSide.NS

    class _StubRound:
        def __init__(self, contract, belote_state):
            self.contract = contract
            self.belote_state = belote_state

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
        # An empty trick is fine — the badge is keyed off
        # belote_by_position.
        diamond = _render_diamond(
            (),
            Suit.HEARTS,
            pending_position=None,
            winner_position=None,
            dimmed=False,
            width=42,
            belote_by_position={Position.NORTH: "belote"},
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
            (),
            Suit.HEARTS,
            pending_position=None,
            winner_position=None,
            dimmed=False,
            width=42,
            belote_by_position={Position.SOUTH: "rebelote"},
        )
        assert "★ Belote" in diamond.plain
        assert "Rebelote" not in diamond.plain

    def test_diamond_no_badge_when_state_empty(self, monkeypatch):
        view = self._make_view(monkeypatch)
        diamond = _render_diamond(
            (),
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
            self.belote_state = {}

    def test_each_seat_shows_its_latest_bid(self, four_players):
        north, east, south, west = four_players
        history = [
            PassBid(south),
            ContractBid(west, 80, Suit.HEARTS),
            PassBid(north),
        ]
        diamond = _render_bidding_diamond(
            history, pending_position=None, width=42
        )
        text = diamond.plain
        # West's bid renders as "80 ♥"; South and North passed.
        assert "80 ♥" in text
        assert "Pass" in text

    def test_pending_seat_marked_with_question(self, four_players):
        north, east, south, west = four_players
        diamond = _render_bidding_diamond(
            [ContractBid(west, 80, Suit.HEARTS)],
            pending_position=Position.NORTH,
            width=42,
        )
        # North is on the move → "N ?"; West shows its standing bid.
        assert "N ?" in diamond.plain
        assert "80 ♥" in diamond.plain

    def test_seat_without_bid_shows_dot(self, four_players):
        diamond = _render_bidding_diamond(
            [], pending_position=None, width=42
        )
        # Empty auction: every seat is a placeholder dot, no "?".
        assert "·" in diamond.plain
        assert "?" not in diamond.plain

    def test_latest_bid_overwrites_earlier(self, four_players):
        """A second bid by the same seat replaces the first in the diamond."""
        north, east, south, west = four_players
        history = [
            ContractBid(west, 80, Suit.HEARTS),
            ContractBid(north, 90, Suit.SPADES),
            PassBid(east),
            PassBid(south),
            ContractBid(west, 100, Suit.HEARTS),
        ]
        text = _render_bidding_diamond(
            history, pending_position=None, width=42
        ).plain
        assert "100 ♥" in text
        assert "80 ♥" not in text

    def test_panel_current_trick_bidding_renders_diamond(self, four_players):
        """During bidding the Current-trick slot becomes the auction diamond."""
        north, east, south, west = four_players
        panel = _panel_current_trick(
            self._StubRound(),
            plays=None,
            phase="bidding",
            current_player=south,
            trick_winner=None,
            bidding_history=[ContractBid(west, 80, Suit.HEARTS)],
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
        # contract and dealer during this phase path.
        def __init__(self, round_number):
            self.round_number = round_number
            self.contract = None
            self.dealer = None

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
        def __init__(self):
            self.round_number = 1
            self.contract = None
            self.dealer = None
            self.belote_state = {}

    def test_current_trick_title_uses_hash_format(self):
        view = RichView()
        panel = _panel_current_trick(
            self._StubRound(),
            plays=(),
            phase="playing",
            current_player=None,
            trick_winner=None,
            trick_index=5,
        )
        assert "Current trick (#5)" in panel.title.plain

    def test_last_trick_title_uses_hash_format(self, monkeypatch, four_players):
        """The echo is numbered one below the trick on the table."""
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView()
        north, *_ = four_players
        # Stub a completed trick.
        view.last_completed_trick = ((), north)
        panel = _panel_last_trick(
            self._StubRound(), view.last_completed_trick, trick_index=8
        )
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

        human = HumanPlayer("You", Position.SOUTH)
        human.team = None  # not exercised by these tests

        class _StubGame:
            def __init__(self, human):
                self.players = [human]
                self.current_round = None
                self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}

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
            human, plays=None, playable_cards=None,
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
        plays = (Play(human, Card(Suit.CLUBS, Rank.KING)),)
        panel = _panel_hand(
            human, plays=plays, playable_cards=[list(human.hand)[1]],
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
        west_stub = type("_W", (), {"position": Position.WEST, "team": None})()
        plays = (Play(west_stub, Card(Suit.CLUBS, Rank.KING)),)
        # Stub a round with hearts trump so the explain helper knows
        # the human's hearts are trumps and emits the "must trump" hint.
        contract_stub = type("_C", (), {"suit": Suit.HEARTS})()
        round_stub = type("_R", (), {"contract": contract_stub})()
        panel = _panel_hand(
            human, plays=plays, playable_cards=list(human.hand),
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
        human = HumanPlayer("You", Position.SOUTH)
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
                self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}

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
            current_plays=None,
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
            scores = {TeamSide.NS: 0, TeamSide.EW: 0}

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


# ======================================================================
# Debug / autoplay options wiring
# ======================================================================
#
# Shared fixtures below back the test classes covering RichView(options=...):
# the back-compat anchor (RichView() with no args), the _pause/_wait_or_pause
# pacing helpers, the autoplay branches of the four blocking-prompt sites,
# the _log -> events-logger mirror, and the debug hands strip.


@pytest.fixture
def _forbid_console_input():
    """A ``console.input`` stand-in that fails the test if ever invoked.

    A blocking ``console.input`` call reaching an autoplay code path is
    the primary failure mode of this feature — it means an unattended
    run would hang forever. Tests that exercise an autoplay branch wire
    this in place of ``console.input`` so any such call fails loudly
    instead of hanging the test process.
    """

    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "console.input must not be called under autoplay"
        )

    return _boom


@pytest.fixture
def end_game_status():
    """A representative ``GameOverStatus`` for ``show_end_game`` tests."""
    return GameOverStatus(
        game_over=True,
        winner=TeamSide.NS,
        tied_teams=None,
        final_scores={TeamSide.NS: 1620, TeamSide.EW: 1420},
    )


def _capture_prints(view) -> list[str]:
    """Patch ``view.console`` to a no-op clear + a print recorder.

    Returns the list the recorder appends to. Mirrors
    ``TestRenderInGameHandSlot._capture_render``: walks both bare
    ``Text`` renderables and ``Panel``s (via ``.renderable``), plus
    ``Panel`` titles, collecting every ``.plain`` string printed.
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


class TestOptionsBackCompat:
    """``RichView()`` with no arguments is the back-compat anchor."""

    def test_default_construction_holds_all_off_options(self):
        view = RichView()
        assert view.options == DebugOptions()

    def test_none_options_defaults(self):
        view = RichView(options=None)
        assert view.options == DebugOptions()

    def test_options_can_be_passed(self):
        opts = DebugOptions(debug=True, autoplay=True, seed=7)
        view = RichView(options=opts)
        assert view.options is opts


class TestPause:
    """``_pause`` sleeps a tunable delay, zeroed by default under debug."""

    def test_uses_default_when_no_env_and_not_debug(self, monkeypatch):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.delenv("CONTRAI_TEST_PAUSE", raising=False)
        view = RichView()

        view._pause("CONTRAI_TEST_PAUSE", 3.0)

        assert sleep_calls == [3.0]

    def test_default_zeroed_under_debug(self, monkeypatch):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.delenv("CONTRAI_TEST_PAUSE", raising=False)
        view = RichView(options=DebugOptions(debug=True))

        view._pause("CONTRAI_TEST_PAUSE", 3.0)

        assert sleep_calls == [0.0]

    def test_env_var_wins_over_default(self, monkeypatch):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_TEST_PAUSE", "0.05")
        view = RichView()

        view._pause("CONTRAI_TEST_PAUSE", 3.0)

        assert sleep_calls == [0.05]

    def test_env_var_wins_over_debug_zeroing(self, monkeypatch):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_TEST_PAUSE", "0.05")
        view = RichView(options=DebugOptions(debug=True))

        view._pause("CONTRAI_TEST_PAUSE", 3.0)

        assert sleep_calls == [0.05]


class TestWaitOrPause:
    """``_wait_or_pause``: autoplay bypasses ``console.input`` entirely."""

    def test_autoplay_never_calls_console_input(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        # Must not raise -- proves console.input was never reached.
        view._wait_or_pause("gold1", "CONTRAI_TEST_WAIT", 0.01)

    def test_autoplay_pauses_via_pause_helper(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_TEST_WAIT", "0.02")
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        view._wait_or_pause("gold1", "CONTRAI_TEST_WAIT", 0.01)

        assert sleep_calls == [0.02]

    def test_non_autoplay_calls_console_input(self):
        """Outside autoplay, the pre-existing console.input idiom runs."""
        view = RichView()
        calls = []
        view.console.input = lambda *a, **k: calls.append((a, k)) or ""

        view._wait_or_pause("gold1", "CONTRAI_TEST_WAIT", 0.01)

        assert len(calls) == 1


class TestAutoplayCtrlCPropagation:
    """Ctrl+C during an autoplay pause propagates; it is never swallowed.

    Outside autoplay the pre-existing idiom (``console.input`` wrapped in
    ``try/except (EOFError, KeyboardInterrupt): pass``) is unchanged —
    verified here too, as a regression guard on the refactor.
    """

    def test_pause_propagates_keyboard_interrupt(self, monkeypatch):
        """Ctrl+C during a timed pause must reach ``main``'s handler.

        ``_pause`` is the single sleep chokepoint every autoplay pause
        and AI-pacing delay routes through, so nothing may swallow the
        interrupt here — including on the default, non-autoplay path.
        """
        from contrai_engine.view import rich_view

        def _raise(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(rich_view.time, "sleep", _raise)
        view = RichView()

        with pytest.raises(KeyboardInterrupt):
            view._pause("CONTRAI_TEST_PAUSE", 0.01)

    def test_wait_or_pause_propagates_under_autoplay(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        def _raise(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(rich_view.time, "sleep", _raise)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        with pytest.raises(KeyboardInterrupt):
            view._wait_or_pause("gold1", "CONTRAI_TEST_WAIT", 0.01)

    def test_wait_or_pause_swallows_keyboard_interrupt_outside_autoplay(self):
        view = RichView()

        def _raise(*_a, **_kw):
            raise KeyboardInterrupt

        view.console.input = _raise

        # Must not raise -- matches the pre-existing console.input idiom.
        view._wait_or_pause("gold1", "CONTRAI_TEST_WAIT", 0.01)

    def test_show_landing_autoplay_pause_propagates_keyboard_interrupt(
        self, monkeypatch
    ):
        from contrai_engine.view import rich_view

        def _raise(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(rich_view.time, "sleep", _raise)
        view = RichView(options=DebugOptions(autoplay=True))

        with pytest.raises(KeyboardInterrupt):
            view.show_landing(TableSetup())


class TestAiDelayDebugZeroing:
    """The 3 AI-pacing sleeps route through ``_pause``: debug zeroes them
    too (env var still wins), on top of the pre-existing env-override
    behavior the untouched tests above already cover."""

    def test_on_bid_made_zeroes_delay_under_debug_without_env(
        self, monkeypatch, four_players
    ):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.delenv("CONTRAI_AI_BID_DELAY", raising=False)
        view = RichView(options=DebugOptions(debug=True))
        bid = ContractBid(north, 100, Suit.HEARTS)

        view.on_bid_made(north, bid, [bid])

        assert sleep_calls == [0.0]

    def test_on_bid_made_env_still_wins_under_debug(
        self, monkeypatch, four_players
    ):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AI_BID_DELAY", "0.07")
        view = RichView(options=DebugOptions(debug=True))
        bid = ContractBid(north, 100, Suit.HEARTS)

        view.on_bid_made(north, bid, [bid])

        assert sleep_calls == [0.07]


class TestOnTrickCompleteAutoplay:
    """``on_trick_complete`` under autoplay: timed pause, no blocking input."""

    class _StubRound:
        contract = None
        play_state = None

    def test_autoplay_never_calls_console_input(
        self, monkeypatch, four_players, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        north, east, south, west = four_players
        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        plays = (
            Play(north, Card(Suit.HEARTS, Rank.JACK)),
            Play(east, Card(Suit.HEARTS, Rank.ACE)),
            Play(south, Card(Suit.HEARTS, Rank.KING)),
            Play(west, Card(Suit.HEARTS, Rank.QUEEN)),
        )

        view.on_trick_complete(plays, north, self._StubRound())

        # Did not hang, and still rotated the last-completed trick.
        assert view.last_completed_trick == (plays, north)

    def test_autoplay_pauses_using_trick_env_delay(
        self, monkeypatch, four_players, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AUTOPLAY_PAUSE", "0.02")
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        plays = (Play(north, Card(Suit.HEARTS, Rank.JACK)),)

        view.on_trick_complete(plays, north, self._StubRound())

        assert sleep_calls == [0.02]

    def test_autoplay_and_debug_together_zero_the_pause_by_default(
        self, monkeypatch, four_players, _forbid_console_input
    ):
        """The unattended-stress-run story: --debug --autoplay races
        through with no artificial pacing anywhere, trick pause included."""
        from contrai_engine.view import rich_view

        north, *_ = four_players
        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.delenv("CONTRAI_AUTOPLAY_PAUSE", raising=False)
        view = RichView(options=DebugOptions(debug=True, autoplay=True))
        view.console.input = _forbid_console_input

        plays = (Play(north, Card(Suit.HEARTS, Rank.JACK)),)

        view.on_trick_complete(plays, north, self._StubRound())

        assert sleep_calls == [0.0]

    def test_autoplay_prompt_uses_autoplay_wrapper_text(
        self, monkeypatch, four_players, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        north, *_ = four_players
        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input
        captured = _capture_prints(view)

        plays = (Play(north, Card(Suit.HEARTS, Rank.JACK)),)

        view.on_trick_complete(plays, north, self._StubRound())

        assert any(t.startswith("(autoplay) ") for t in captured)


class TestShowRoundRecapAutoplay:
    """``show_round_recap`` under autoplay: timed pause, no blocking input."""

    class _StubRound:
        round_number = 3
        contract = None
        round_scores = {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_autoplay_never_calls_console_input(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        view.show_round_recap(
            self._StubRound(), {TeamSide.NS: 0, TeamSide.EW: 0}
        )
        # Did not hang -- reaching this line proves it.

    def test_autoplay_pauses_using_recap_env_delay(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AUTOPLAY_RECAP_PAUSE", "0.03")
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        view.show_round_recap(
            self._StubRound(), {TeamSide.NS: 0, TeamSide.EW: 0}
        )

        assert sleep_calls == [0.03]

    def test_autoplay_prompt_uses_autoplay_wrapper_text(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input
        captured = _capture_prints(view)

        view.show_round_recap(
            self._StubRound(), {TeamSide.NS: 0, TeamSide.EW: 0}
        )

        assert any("(autoplay)" in t for t in captured)


class TestShowLandingAutoplay:
    """``show_landing`` under autoplay: renders once, pauses, returns the
    setup it was handed -- there is no human to type a choice."""

    def test_autoplay_returns_the_passed_setup_without_input(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input
        setup = TableSetup(rules=RuleConfig(target_score=3000), origin="house")

        assert view.show_landing(setup) is setup

    def test_autoplay_returns_the_defaults_when_not_passed(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        assert view.show_landing() == TableSetup()

    def test_autoplay_pauses_using_landing_env_delay(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AUTOPLAY_LANDING_PAUSE", "0.04")
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        view.show_landing(TableSetup())

        assert sleep_calls == [0.04]

    def test_autoplay_prompt_uses_autoplay_wrapper_text(
        self, monkeypatch, _forbid_console_input
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input
        captured = _capture_prints(view)

        view.show_landing(TableSetup())

        assert any("(autoplay)" in t for t in captured)


def _drive_landing(view, raws):
    """Feed ``raws`` to ``view.console.input`` and silence the printing."""
    inputs = iter(raws)
    view.console.clear = lambda *a, **k: None
    view.console.print = lambda *a, **k: None
    view.console.input = lambda *a, **k: next(inputs)
    return view


class TestShowLandingDispatcher:
    """The landing screen is the setup dispatcher: it routes keys to the
    sub-screens and returns whichever setup the player leaves it with."""

    def test_blank_input_deals_the_setup_unchanged(self):
        setup = TableSetup(rules=RuleConfig(target_score=1000), origin="house")
        view = _drive_landing(RichView(), [""])

        assert view.show_landing(setup) is setup

    def test_no_argument_opens_on_the_catalogue_defaults(self):
        view = _drive_landing(RichView(), [""])

        assert view.show_landing() == TableSetup()

    def test_l_toggles_the_live_round_score(self):
        view = _drive_landing(RichView(), ["l", ""])

        result = view.show_landing(TableSetup())

        assert result.aids == TableAids(live_round_score=False)
        # The aid is not a rule: switching it must not touch the ruleset.
        assert result.rules == RuleConfig()

    def test_l_twice_returns_to_where_it_started(self):
        view = _drive_landing(RichView(), ["l", "l", ""])

        assert view.show_landing(TableSetup()).aids == TableAids()

    def test_p_routes_to_the_preset_picker(self, monkeypatch):
        picked = TableSetup(rules=RuleConfig(target_score=500), origin="picked")
        view = _drive_landing(RichView(), ["p", ""])
        monkeypatch.setattr(
            RichView, "_show_preset_picker", lambda self, current: picked
        )

        assert view.show_landing(TableSetup()) is picked

    def test_f_routes_to_the_file_loader(self, monkeypatch):
        loaded = TableSetup(origin="house.toml")
        view = _drive_landing(RichView(), ["f", ""])
        monkeypatch.setattr(
            RichView, "_show_file_loader", lambda self, current: loaded
        )

        assert view.show_landing(TableSetup()) is loaded

    def test_a_sub_screen_is_handed_the_setup_in_play(self, monkeypatch):
        """Each sub-screen edits the live setup, not a fresh one."""
        seen = []
        setup = TableSetup(rules=RuleConfig(target_score=1000))
        view = _drive_landing(RichView(), ["p", ""])

        def _record(self, current):
            seen.append(current)
            return current

        monkeypatch.setattr(RichView, "_show_preset_picker", _record)
        view.show_landing(setup)

        assert seen == [setup]

    def test_an_unknown_key_reprompts_without_changing_anything(self):
        setup = TableSetup()
        view = _drive_landing(RichView(), ["z", ""])

        assert view.show_landing(setup) is setup

    def test_keys_are_case_insensitive_and_have_long_forms(self):
        view = _drive_landing(RichView(), ["LIVE", ""])

        assert view.show_landing(TableSetup()).aids == TableAids(
            live_round_score=False
        )


class TestShowPresetPicker:
    """The preset picker: a numbered radio over ``PRESETS``."""

    def test_blank_input_keeps_the_current_setup(self):
        setup = TableSetup(origin="house.toml")
        view = _drive_landing(RichView(), [""])

        assert view._show_preset_picker(setup) is setup

    def test_a_number_selects_that_row(self):
        view = _drive_landing(RichView(), ["1"])

        result = view._show_preset_picker(TableSetup(origin="house.toml"))

        assert result.rules == PRESETS["classic"]
        assert result.origin == "classic"

    def test_a_name_selects_that_preset(self):
        view = _drive_landing(RichView(), ["classic"])

        assert view._show_preset_picker(TableSetup(origin="x")).origin == "classic"

    def test_the_aids_ride_along_unchanged(self):
        """A preset names 22 rules; the §9.7 aids are not among them."""
        aids = TableAids(live_round_score=False)
        view = _drive_landing(RichView(), ["1"])

        assert view._show_preset_picker(TableSetup(aids=aids)).aids == aids

    def test_an_unknown_pick_reprompts(self):
        view = _drive_landing(RichView(), ["99", "nope", "classic"])

        assert view._show_preset_picker(TableSetup(origin="x")).origin == "classic"


class TestShowFileLoader:
    """The file loader: a path typed at the prompt."""

    def test_blank_input_cancels(self):
        setup = TableSetup(origin="house.toml")
        view = _drive_landing(RichView(), [""])

        assert view._show_file_loader(setup) is setup

    def test_a_valid_path_is_loaded(self, tmp_path):
        path = tmp_path / "house.toml"
        path.write_text(
            "[general]\ntarget_score = 1000\n"
            "[table_aids]\nlive_round_score = false\n",
            encoding="utf-8",
        )
        view = _drive_landing(RichView(), [str(path)])

        result = view._show_file_loader(TableSetup())

        assert result.rules == RuleConfig(target_score=1000)
        assert result.aids == TableAids(live_round_score=False)
        assert result.origin == "house.toml"

    def test_a_quoted_path_is_accepted(self, tmp_path):
        """Terminals paste paths with quotes; the loader strips them."""
        path = tmp_path / "house.toml"
        path.write_text("[general]\ntarget_score = 500\n", encoding="utf-8")
        view = _drive_landing(RichView(), [f'"{path}"'])

        assert view._show_file_loader(TableSetup()).rules.target_score == 500

    def test_a_missing_file_reprompts_rather_than_leaving(self, tmp_path):
        """Mistyping a filename must not cost the setup already assembled."""
        setup = TableSetup(rules=RuleConfig(target_score=3000))
        view = _drive_landing(RichView(), [str(tmp_path / "nope.toml"), ""])

        assert view._show_file_loader(setup) is setup

    def test_a_malformed_file_reprompts(self, tmp_path):
        path = tmp_path / "broken.toml"
        path.write_text("[general\n", encoding="utf-8")
        setup = TableSetup()
        view = _drive_landing(RichView(), [str(path), ""])

        assert view._show_file_loader(setup) is setup

    def test_an_impossible_table_reprompts(self, tmp_path):
        """Core's own validation is a rejection notice, not a crash."""
        path = tmp_path / "impossible.toml"
        path.write_text(
            "[scoring]\nmark_made_points = false\nmark_announced_points = false\n",
            encoding="utf-8",
        )
        setup = TableSetup()
        view = _drive_landing(RichView(), [str(path), ""])

        assert view._show_file_loader(setup) is setup


class TestShowEndGameAutoplay:
    """``show_end_game`` under autoplay: pause, log, return ``"q"``."""

    def test_autoplay_returns_q_without_input(
        self, monkeypatch, _forbid_console_input, end_game_status
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        assert view.show_end_game(end_game_status) == "q"

    def test_autoplay_logs_game_over(
        self, monkeypatch, _forbid_console_input, end_game_status, caplog
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        with caplog.at_level(logging.INFO, logger="contrai_engine.view.events"):
            view.show_end_game(end_game_status)

        events_records = [
            r for r in caplog.records if r.name == "contrai_engine.view.events"
        ]
        assert any("GAME OVER" in r.getMessage() for r in events_records)

    def test_autoplay_pauses_using_endgame_env_delay(
        self, monkeypatch, _forbid_console_input, end_game_status
    ):
        from contrai_engine.view import rich_view

        sleep_calls = []
        monkeypatch.setattr(rich_view.time, "sleep",
                            lambda s: sleep_calls.append(s))
        monkeypatch.setenv("CONTRAI_AUTOPLAY_ENDGAME_PAUSE", "0.06")
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input

        view.show_end_game(end_game_status)

        assert sleep_calls == [0.06]

    def test_autoplay_prompt_uses_autoplay_wrapper_text(
        self, monkeypatch, _forbid_console_input, end_game_status
    ):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(autoplay=True))
        view.console.input = _forbid_console_input
        captured = _capture_prints(view)

        view.show_end_game(end_game_status)

        assert any("(autoplay)" in t for t in captured)


class TestShowEndGameInteractive:
    """Non-autoplay path — a regression net on the banner-print refactor."""

    def test_q_input_returns_q(self, end_game_status):
        view = RichView()
        view.console.input = lambda *a, **k: "q"

        assert view.show_end_game(end_game_status) == "q"

    def test_n_input_returns_n(self, end_game_status):
        view = RichView()
        view.console.input = lambda *a, **k: "n"

        assert view.show_end_game(end_game_status) == "n"

    def test_invalid_then_valid_input_reprompts(self, end_game_status):
        view = RichView()
        inputs = iter(["bogus", "", "r"])
        view.console.input = lambda *a, **k: next(inputs)

        assert view.show_end_game(end_game_status) == "r"


class TestLogMirror:
    """``_log`` mirrors every line to the events logger, uncapped, while
    ``event_log`` keeps its ``LOG_MAX`` display cap."""

    def test_mirrors_every_line_uncapped(self, caplog):
        view = RichView()
        with caplog.at_level(logging.DEBUG, logger="contrai_engine.view.events"):
            for i in range(view.LOG_MAX + 3):
                view._log(Text(f"line {i}"))

        # Display cap still holds.
        assert len(view.event_log) == view.LOG_MAX
        assert view.event_log[0].plain == f"line {3}"

        # The mirror captured every line, uncapped.
        events_records = [
            r for r in caplog.records if r.name == "contrai_engine.view.events"
        ]
        messages = [r.getMessage() for r in events_records]
        assert len(messages) == view.LOG_MAX + 3
        assert messages[0] == "line 0"
        assert messages[-1] == f"line {view.LOG_MAX + 2}"

    def test_mirror_uses_plain_text_not_markup(self, caplog):
        view = RichView()
        line = Text()
        line.append("N ", style="bold red")
        line.append("wins trick.")

        with caplog.at_level(logging.DEBUG, logger="contrai_engine.view.events"):
            view._log(line)

        events_records = [
            r for r in caplog.records if r.name == "contrai_engine.view.events"
        ]
        assert events_records[-1].getMessage() == "N wins trick."


class TestDebugStrip:
    """The debug hands strip appears only under ``options.debug`` with a
    round attached — never by default, never without a round."""

    class _StubRound:
        round_number = 1
        contract = None
        dealer = None

    class _StubGame:
        def __init__(self, players):
            self.players = players
            self.current_round = TestDebugStrip._StubRound()
            self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}

    def _render_bidding_frame(self, view):
        captured = _capture_prints(view)
        view._render_in_game(
            phase="bidding",
            current_player=None,
            bidding_history=[],
            prompt_question=Text(""),
            mandatory=False,
        )
        return captured

    def test_strip_present_when_debug_and_round(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(debug=True, seed=42))
        view.attach(self._StubGame(list(four_players)), target_score=1500)

        combined = "\n".join(self._render_bidding_frame(view))

        assert "Debug — all hands" in combined
        assert "seed 42" in combined

    def test_strip_absent_by_default(self, monkeypatch, four_players):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView()
        view.attach(self._StubGame(list(four_players)), target_score=1500)

        combined = "\n".join(self._render_bidding_frame(view))

        assert "Debug — all hands" not in combined

    def test_strip_absent_when_debug_but_no_round(self, monkeypatch):
        from contrai_engine.view import rich_view

        monkeypatch.setattr(rich_view.time, "sleep", lambda _: None)
        view = RichView(options=DebugOptions(debug=True))
        # No .attach() -> self.game is None -> round_ is None internally.

        combined = "\n".join(self._render_bidding_frame(view))

        assert "Debug — all hands" not in combined


class TestLiveRoundScoreAid:
    """``RichView`` carries the §9.7 interface aid and hands it to the frame.

    The Round panel is printed inside a ``Table.grid``, which has no
    ``.plain`` to read back, so the aid's journey is asserted by spying on
    ``_panel_round`` itself — the seam the frame actually crosses.
    """

    class _StubRound:
        round_number = 1
        contract = None
        dealer = None

    class _StubGame:
        def __init__(self):
            self.players = []
            self.current_round = TestLiveRoundScoreAid._StubRound()
            self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}

    def _spy_on_panel_round(self, monkeypatch) -> dict:
        from contrai_engine.view import rich_view

        seen: dict = {}

        def _spy(round_, phase, trick_index=1, live_score=True):
            seen["live_score"] = live_score
            return Text("")

        monkeypatch.setattr(rich_view, "_panel_round", _spy)
        return seen

    def _render(self, view) -> None:
        view.console.clear = lambda *a, **k: None
        view.console.print = lambda *a, **k: None
        view._render_in_game(
            phase="playing", prompt_question=Text(""), mandatory=False
        )

    def test_default_view_leaves_the_aid_on(self, monkeypatch):
        seen = self._spy_on_panel_round(monkeypatch)
        view = RichView()
        view.attach(self._StubGame(), target_score=1500)

        self._render(view)

        assert view.aids == TableAids()
        assert seen["live_score"] is True

    def test_aid_off_reaches_the_round_panel(self, monkeypatch):
        seen = self._spy_on_panel_round(monkeypatch)
        view = RichView(aids=TableAids(live_round_score=False))
        view.attach(self._StubGame(), target_score=1500)

        self._render(view)

        assert seen["live_score"] is False

    def test_aids_can_be_swapped_after_construction(self, monkeypatch):
        """The CLI re-points ``view.aids`` when the setup screen edits it."""
        seen = self._spy_on_panel_round(monkeypatch)
        view = RichView()
        view.attach(self._StubGame(), target_score=1500)
        view.aids = TableAids(live_round_score=False)

        self._render(view)

        assert seen["live_score"] is False


class TestShowKnobEditor:
    """The per-knob editor: a numbered grid per §9 subsection."""

    def test_blank_input_leaves_the_setup_untouched(self):
        setup = TableSetup(origin="house.toml")
        view = _drive_landing(RichView(), [""])

        assert view._show_knob_editor(setup) == setup

    def test_a_number_cycles_that_knob(self):
        # [general] is the first section: 1 is target_score, 2 is
        # turn_direction.
        view = _drive_landing(RichView(), ["2", ""])

        result = view._show_knob_editor(TableSetup())

        assert result.rules.turn_direction is TurnDirection.CLOCKWISE

    def test_the_target_score_climbs_the_ladder(self):
        view = _drive_landing(RichView(), ["1", ""])

        result = view._show_knob_editor(TableSetup())

        assert result.rules.target_score == 3000

    def test_n_walks_to_the_next_section(self):
        # [trump] is the second section: 1 is extended_trump_choices.
        view = _drive_landing(RichView(), ["n", "1", ""])

        result = view._show_knob_editor(TableSetup())

        assert result.rules.extended_trump_choices is True

    def test_b_walks_back_and_wraps_to_the_last_section(self):
        # Stepping back from [general] lands on [scoring], whose 9th knob
        # is `rounding`.
        view = _drive_landing(RichView(), ["b", "9", ""])

        result = view._show_knob_editor(TableSetup())

        assert result.rules.rounding is Rounding.NEAREST_10

    def test_editing_marks_the_setup_custom(self):
        view = _drive_landing(RichView(), ["2", ""])

        assert view._show_knob_editor(TableSetup(origin="classic")).origin == "custom"

    def test_a_knob_turned_and_turned_back_keeps_the_origin(self):
        """Two of the two turn directions is where it started."""
        view = _drive_landing(RichView(), ["2", "2", ""])

        result = view._show_knob_editor(TableSetup(origin="house.toml"))

        assert result.rules == RuleConfig()
        assert result.origin == "house.toml"

    def test_the_aids_are_not_the_editors_business(self):
        aids = TableAids(live_round_score=False)
        view = _drive_landing(RichView(), ["2", ""])

        assert view._show_knob_editor(TableSetup(aids=aids)).aids == aids

    def test_an_impossible_toggle_leaves_the_ruleset_alone(self):
        """[scoring] 1 is mark_made_points, 2 is mark_announced_points.
        Turning both off is the one combination §9 forbids: the editor
        must refuse it and keep the config it had."""
        opened = TableSetup(rules=RuleConfig(mark_made_points=False))
        # Walk back to [scoring], try to turn the second one off, finish.
        view = _drive_landing(RichView(), ["b", "2", ""])

        result = view._show_knob_editor(opened)

        assert result.rules == opened.rules
        assert result.origin == opened.origin

    def test_an_impossible_toggle_is_reported_with_cores_own_message(self):
        opened = TableSetup(rules=RuleConfig(mark_made_points=False))
        printed = []
        view = RichView()
        inputs = iter(["b", "2", ""])
        view.console.clear = lambda *a, **k: None
        view.console.print = lambda *a, **k: printed.extend(
            getattr(getattr(a_, "renderable", None), "plain", "") for a_ in a
        )
        view.console.input = lambda *a, **k: next(inputs)

        view._show_knob_editor(opened)

        assert any("mark_made_points" in text for text in printed)

    def test_an_out_of_range_number_reprompts(self):
        view = _drive_landing(RichView(), ["99", "2", ""])

        result = view._show_knob_editor(TableSetup())

        assert result.rules.turn_direction is TurnDirection.CLOCKWISE

    def test_an_unknown_key_reprompts(self):
        view = _drive_landing(RichView(), ["zz", ""])

        assert view._show_knob_editor(TableSetup()) == TableSetup()

    def test_k_routes_the_landing_screen_to_the_editor(self, monkeypatch):
        edited = TableSetup(rules=RuleConfig(target_score=500), origin="custom")
        view = _drive_landing(RichView(), ["k", ""])
        monkeypatch.setattr(
            RichView, "_show_knob_editor", lambda self, current: edited
        )

        assert view.show_landing(TableSetup()) is edited

    def test_every_section_can_be_reached_by_walking_forward(self):
        """``[n]`` wraps, so the walk visits all six and returns."""
        seen = []
        view = RichView()
        inputs = iter(["n"] * len(SECTIONS) + [""])
        view.console.clear = lambda *a, **k: None
        view.console.print = lambda *a, **k: [
            seen.append(getattr(getattr(t, "renderable", None), "plain", ""))
            for t in a
        ]
        view.console.input = lambda *a, **k: next(inputs)

        view._show_knob_editor(TableSetup())

        headings = {h for h in SECTION_HEADINGS.values()}
        rendered = " ".join(seen)
        assert all(h in rendered for h in headings)
