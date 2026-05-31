"""Tests for the pure helpers in :mod:`contrai_engine.view.rich_view`.

We cover the four functions that have real branching logic and would
break silently if the engine APIs around them shifted:

- ``_parse_bid_input``         — humans type bids, this is the parser.
- ``_parse_card_input``        — humans type card numbers, this validates them.
- ``_sort_hand_for_display``   — trump-first ordering for the hand row.
- ``_current_winner``          — live trick-winner highlight.
- ``_explain_constraint``      — the green "↑ playable …" hint line.

Rendering helpers (``Panel``/``Table`` builders) are not unit-tested — the
smoke-test pass on ``uv run contrai`` validates them end-to-end.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from contrai_core import Auction, Card, Rank, Suit, Trick
from contrai_engine.model.player import AiPlayer
from contrai_core.team import Team
from contrai_core.bid import ContractBid, DoubleBid, PassBid, RedoubleBid
from contrai_core.contract import Contract
from contrai_engine.view.rich_view import (
    RichView,
    RoundSummary,
    _bid_to_legacy,
    _current_winner,
    _double_available_to,
    _explain_constraint,
    _format_contract_short,
    _illegal_bid_reason,
    _parse_bid_input,
    _parse_card_input,
    _redouble_available_to,
    _resolve_delay,
    _sort_hand_for_display,
)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def four_players():
    """A North/East/South/West quartet wired into N-S and E-W teams."""
    north = AiPlayer("North", "North")
    east = AiPlayer("East", "East")
    south = AiPlayer("South", "South")
    west = AiPlayer("West", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    north.team = south.team = ns
    east.team = west.team = ew
    return north, east, south, west


# ======================================================================
# _parse_bid_input
# ======================================================================


class TestParseBidInput:
    """Bid-string parser. Returns engine-shaped bid or ``None`` on error."""

    @pytest.mark.parametrize("raw", ["pass", "PASS", "Pass", "p", " pass "])
    def test_pass_variants(self, raw):
        assert _parse_bid_input(raw) == "Pass"

    @pytest.mark.parametrize(
        "raw", ["double", "d", "Double", "DOUBLE", " double "]
    )
    def test_double_variants(self, raw):
        assert _parse_bid_input(raw) == "Double"

    @pytest.mark.parametrize(
        "raw", ["redouble", "r", "Redouble", "REDOUBLE", " redouble "]
    )
    def test_redouble_variants(self, raw):
        assert _parse_bid_input(raw) == "Redouble"

    @pytest.mark.parametrize(
        "raw",
        ["coinche", "surcoinche", "contrée", "contree",
         "surcontrée", "surcontree", "passe"],
    )
    def test_rejects_french_aliases(self, raw):
        """The CLI uses the English vocabulary exclusively. The parser
        used to accept the French aliases ``coinche`` / ``surcoinche`` /
        ``contrée`` / ``surcontrée`` / ``passe``; those have been
        retired."""
        assert _parse_bid_input(raw) is None

    @pytest.mark.parametrize(
        "raw,value,suit",
        [
            ("80 h", 80, Suit.HEARTS),
            ("100 hearts", 100, Suit.HEARTS),
            ("100 heart", 100, Suit.HEARTS),
            ("90 s", 90, Suit.SPADES),
            ("110 spades", 110, Suit.SPADES),
            ("120 d", 120, Suit.DIAMONDS),
            ("130 diamond", 130, Suit.DIAMONDS),
            ("140 c", 140, Suit.CLUBS),
            ("150 clubs", 150, Suit.CLUBS),
            ("160 nt", 160, Suit.NO_TRUMP),
            ("160 notrump", 160, Suit.NO_TRUMP),
            ("80 ♥", 80, Suit.HEARTS),
            ("80 ♠", 80, Suit.SPADES),
        ],
    )
    def test_contract_bid_separated(self, raw, value, suit):
        assert _parse_bid_input(raw) == (value, suit)

    @pytest.mark.parametrize(
        "raw,value,suit",
        [
            ("100h", 100, Suit.HEARTS),
            ("80s", 80, Suit.SPADES),
            ("130c", 130, Suit.CLUBS),
        ],
    )
    def test_contract_bid_glued(self, raw, value, suit):
        """Value and suit may be glued together with no separator."""
        assert _parse_bid_input(raw) == (value, suit)

    @pytest.mark.parametrize(
        "raw,suit",
        [
            ("slam s", Suit.SPADES),
            ("slam h", Suit.HEARTS),
            ("slam d", Suit.DIAMONDS),
            ("slam c", Suit.CLUBS),
            ("slams", Suit.SPADES),  # glued
            ("SLAM H", Suit.HEARTS),  # case-insensitive
        ],
    )
    def test_slam(self, raw, suit):
        assert _parse_bid_input(raw) == ("Slam", suit)

    @pytest.mark.parametrize(
        "raw,suit",
        [
            ("soloslam s", Suit.SPADES),
            ("solo slam h", Suit.HEARTS),  # two-word form
            ("solo slam d", Suit.DIAMONDS),
            ("soloslam c", Suit.CLUBS),
            ("soloslams", Suit.SPADES),  # glued
            ("SOLO SLAM H", Suit.HEARTS),  # case-insensitive
        ],
    )
    def test_solo_slam(self, raw, suit):
        assert _parse_bid_input(raw) == ("SoloSlam", suit)

    def test_capital_letters_in_value_suit(self):
        assert _parse_bid_input("100 H") == (100, Suit.HEARTS)

    @pytest.mark.parametrize(
        "raw",
        [
            "",          # empty
            "  ",        # whitespace only
            "xyz",       # garbage
            "80",        # value but no suit
            "h",         # suit but no value
            "80 q",      # invalid suit letter
            "70 h",      # value below the 80 floor
            "85 h",      # value not on the 10-step ladder
            "190 h",     # value above the 180 ceiling
            "abc h",     # non-numeric value
            "80 h s",    # too many tokens
            "capot s",   # legacy name no longer accepted
            "160 sa",    # French sans-atout alias no longer accepted
        ],
    )
    def test_rejects_garbage(self, raw):
        assert _parse_bid_input(raw) is None


# ======================================================================
# _redouble_available_to + adaptive bid prompt
# ======================================================================


class TestRedoubleAvailability:
    """Validates the helper that drives the '(pass / redouble)' hint."""

    def test_empty_history_no_redouble(self, four_players):
        north, *_ = four_players
        assert _redouble_available_to([], north) is False

    def test_after_contract_only_no_redouble(self, four_players):
        """A bare contract bid hasn't been doubled yet."""
        north, _east, _south, _west = four_players
        history = [(north, (100, Suit.HEARTS))]
        assert _redouble_available_to(history, north) is False

    def test_contractor_can_redouble_after_opponent_doubles(
        self, four_players
    ):
        """N bid 100♥, E doubled. N (contractor) is up — must offer
        redouble."""
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        assert _redouble_available_to(history, north) is True
        # Contractor's partner (South) is also on the contracting team.
        assert _redouble_available_to(history, south) is True

    def test_opponent_cannot_redouble(self, four_players):
        """An opponent of the contractor cannot redouble even when a
        Double is on the table."""
        north, east, _south, west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
        ]
        # West is on East's team → not the contracting team.
        assert _redouble_available_to(history, west) is False

    def test_pass_after_double_closes_window(self, four_players):
        """Once any player has passed after the Double, the redouble
        window has closed."""
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
            (south, "Pass"),
        ]
        # North is the only contracting-team member who hasn't acted —
        # but their PARTNER (S) already passed. By bidding-loop rules
        # the redouble window is closed once a pass intervenes.
        assert _redouble_available_to(history, north) is False

    def test_already_redoubled_no_more(self, four_players):
        north, east, south, _west = four_players
        history = [
            (north, (100, Suit.HEARTS)),
            (east, "Double"),
            (south, "Redouble"),
        ]
        assert _redouble_available_to(history, north) is False


class TestBiddingPromptHint:
    """End-to-end test that the prompt text adapts to the bid history."""

    def _prompt(self, history, next_player):
        view = RichView()
        return view._bidding_prompt_text(history, next_player).plain

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
        # Bidding higher and passing are still on the table.
        assert "80 H" in text and "pass" in text

    def test_double_hint_when_opponent_holds_contract(self, four_players):
        """East (an opponent of South) holds the contract → offer double."""
        _north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES))]
        text = self._prompt(history, south)
        assert "double" in text


class TestDoubleAvailability:
    """Validates the helper that gates the 'double' hint."""

    def test_empty_history_no_double(self, four_players):
        north, *_ = four_players
        assert _double_available_to([], north) is False

    def test_only_passes_no_double(self, four_players):
        north, east, south, _west = four_players
        history = [(east, "Pass"), (south, "Pass")]
        assert _double_available_to(history, north) is False

    def test_opponent_contract_is_doublable(self, four_players):
        """South may double East's standing contract."""
        _north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES))]
        assert _double_available_to(history, south) is True

    def test_own_side_contract_not_doublable(self, four_players):
        """South may NOT double North's (partner's) contract."""
        north, _east, south, _west = four_players
        history = [(north, (90, Suit.SPADES))]
        assert _double_available_to(history, south) is False

    def test_passes_do_not_close_double_window(self, four_players):
        """Intervening passes keep the Coinche window open."""
        _north, east, south, west = four_players
        history = [(east, (90, Suit.SPADES)), (west, "Pass")]
        assert _double_available_to(history, south) is True

    def test_already_doubled_not_doublable_again(self, four_players):
        north, east, south, _west = four_players
        history = [(east, (90, Suit.SPADES)), (south, "Double")]
        # North is on the contracting side's opponents... but a Double
        # already stands, so no further Double is legal regardless.
        assert _double_available_to(history, north) is False


class TestIllegalBidReason:
    """The specific nudge shown when a human types an illegal bid."""

    def _auction(self, bids):
        auction = Auction.empty()
        for bid in bids:
            auction = auction.apply(bid)
        return auction

    def test_double_own_partner(self, four_players):
        north, east, south, west = four_players
        auction = self._auction(
            [PassBid(east), ContractBid(north, 90, Suit.SPADES), PassBid(west)]
        )
        reason = _illegal_bid_reason(DoubleBid(south), auction)
        assert "own side" in reason

    def test_double_with_no_contract(self, four_players):
        north, east, _south, _west = four_players
        auction = self._auction([PassBid(east)])
        reason = _illegal_bid_reason(DoubleBid(north), auction)
        assert "no contract" in reason.lower()

    def test_double_already_doubled(self, four_players):
        north, east, south, _west = four_players
        auction = self._auction(
            [ContractBid(east, 90, Suit.SPADES), DoubleBid(south)]
        )
        reason = _illegal_bid_reason(DoubleBid(north), auction)
        assert "already" in reason.lower()

    def test_contract_must_outrank(self, four_players):
        _north, east, south, _west = four_players
        auction = self._auction([ContractBid(east, 100, Suit.SPADES)])
        reason = _illegal_bid_reason(
            ContractBid(south, 80, Suit.HEARTS), auction
        )
        assert "outrank" in reason and "100" in reason


class TestRequestBidActionLegality:
    """Regression: an illegal human bid must re-prompt, never crash.

    Reproduces the reported traceback — South types 'double' against
    their partner North's 90♠ contract. Before the fix this escaped to
    ``Auction.apply`` and raised ``IllegalBidError``; now the view
    rejects it inline and loops for fresh input.
    """

    def _drive(self, four_players, raws):
        """Run request_bid_action feeding *raws* as successive inputs.

        Returns ``(bid, printed_lines)``. Rendering and console I/O are
        stubbed so the loop runs headless.
        """
        view = RichView()
        inputs = iter(raws)
        printed: list[str] = []
        view._render_in_game = lambda **kwargs: None
        view.console.input = lambda *a, **k: next(inputs)
        view.console.print = lambda renderable=None, *a, **k: printed.append(
            getattr(renderable, "plain", str(renderable))
        )
        return view, printed, inputs

    def test_double_own_partner_reprompts_then_passes(self, four_players):
        north, east, south, west = four_players
        auction = Auction.empty()
        for bid in (
            PassBid(east),
            ContractBid(north, 90, Suit.SPADES),
            PassBid(west),
        ):
            auction = auction.apply(bid)

        view, printed, _ = self._drive(four_players, ["double", "pass"])
        result = view.request_bid_action(south, auction)

        # The illegal Double was rejected inline (no exception), and the
        # loop accepted the follow-up Pass.
        assert isinstance(result, PassBid)
        assert any("own side" in line for line in printed)
        # And whatever it returns is genuinely legal — the property the
        # crash violated.
        assert auction.is_legal(result)

    def test_legal_double_against_opponent_is_accepted(self, four_players):
        _north, east, south, _west = four_players
        auction = Auction.empty().apply(ContractBid(east, 90, Suit.SPADES))

        view, printed, _ = self._drive(four_players, ["double"])
        result = view.request_bid_action(south, auction)

        assert isinstance(result, DoubleBid)
        assert printed == []  # accepted on first try, no rejection notice


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
        text = view._panel_bidding_history(bids).renderable.plain
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
        text = view._panel_bidding_history(bids).renderable.plain
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
        text = view._panel_bidding_history(bids).renderable.plain
        line1, line2 = text.split("\n", 1)
        # The seat letters start at identical offsets on both lines, so
        # the bids stack in vertical lanes despite differing bid widths.
        for letter in ("S", "E", "N", "W"):
            assert line1.index(f"{letter} ") == line2.index(f"{letter} ")


class TestResolveDelay:
    """Env-var pacing resolver — used by the AI hooks."""

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CONTRAI_AI_TEST", raising=False)
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.7

    def test_reads_float_from_env(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "0.25")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.25

    def test_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "fast")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.7

    def test_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("CONTRAI_AI_TEST", "-2.0")
        assert _resolve_delay("CONTRAI_AI_TEST", default=0.7) == 0.0


class TestBidToLegacy:
    def test_pass(self):
        assert _bid_to_legacy(PassBid(player=None)) == "Pass"

    def test_double(self):
        assert _bid_to_legacy(DoubleBid(player=None)) == "Double"

    def test_contract(self, four_players):
        north, *_ = four_players
        bid = ContractBid(north, 100, Suit.HEARTS)
        assert _bid_to_legacy(bid) == (100, Suit.HEARTS)


class TestFormatContractShort:
    """The shared contract label: value + taker seat + Coinche caller.

    Used by the in-game round panel, the after-round recap, and the
    event-log 'Contract set' line — all three render through this.
    """

    def test_plain_contract_names_taker_seat(self, four_players):
        _north, east, *_ = four_players
        contract = Contract(ContractBid(east, 100, Suit.HEARTS))
        text = _format_contract_short(contract).plain
        assert "100 by E" in text
        # No multiplier marker on an un-doubled contract.
        assert "×2" not in text and "×4" not in text

    def test_doubled_contract_names_coincheur(self, four_players):
        north, east, _south, west = four_players
        contract = Contract(
            ContractBid(north, 110, Suit.SPADES),
            double=True,
            double_player=east,
        )
        text = _format_contract_short(contract).plain
        assert "110 by N" in text
        assert "×2 by E" in text

    def test_redoubled_contract_names_surcoincheur(self, four_players):
        north, east, _south, west = four_players
        contract = Contract(
            ContractBid(north, 120, Suit.CLUBS),
            double=True,
            redouble=True,
            double_player=east,
            redouble_player=north,
        )
        text = _format_contract_short(contract).plain
        assert "120 by N" in text
        # Redouble takes precedence over the double marker.
        assert "×4 by N" in text
        assert "×2" not in text

    def test_double_without_known_player_still_shows_multiplier(
        self, four_players
    ):
        north, *_ = four_players
        contract = Contract(
            ContractBid(north, 90, Suit.DIAMONDS),
            double=True,
            double_player=None,
        )
        text = _format_contract_short(contract).plain
        assert "×2" in text
        # No 'by …' tail when the caller is unknown.
        assert "×2 by" not in text

    def test_slam_value_label(self, four_players):
        _north, east, *_ = four_players
        contract = Contract(ContractBid(east, "Slam", Suit.HEARTS))
        text = _format_contract_short(contract).plain
        assert "Slam by E" in text


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

    def test_panel_event_log_renders_lines(self, monkeypatch):
        view = self._make_view(monkeypatch)
        view._log(Text("alpha"))
        view._log(Text("beta"))
        panel = view._panel_event_log()
        assert "alpha" in panel.renderable.plain
        assert "beta" in panel.renderable.plain
        assert panel.title.plain == "Log"

    def test_panel_event_log_empty_placeholder(self, monkeypatch):
        view = self._make_view(monkeypatch)
        panel = view._panel_event_log()
        assert "(no events yet)" in panel.renderable.plain

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
        diamond = view._render_diamond(
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
        diamond = view._render_diamond(
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
        diamond = view._render_diamond(
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
        diamond = view._render_bidding_diamond(
            history, pending_position=None, width=42
        )
        text = diamond.plain
        # West's bid renders as "80 ♥"; South and North passed.
        assert "80 ♥" in text
        assert "Pass" in text

    def test_pending_seat_marked_with_question(self, four_players):
        view = RichView()
        north, east, south, west = four_players
        diamond = view._render_bidding_diamond(
            [(west, (80, Suit.HEARTS))],
            pending_position="North",
            width=42,
        )
        # North is on the move → "N ?"; West shows its standing bid.
        assert "N ?" in diamond.plain
        assert "80 ♥" in diamond.plain

    def test_seat_without_bid_shows_dot(self, four_players):
        view = RichView()
        diamond = view._render_bidding_diamond(
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
        text = view._render_bidding_diamond(
            history, pending_position=None, width=42
        ).plain
        assert "100 ♥" in text
        assert "80 ♥" not in text

    def test_panel_current_trick_bidding_renders_diamond(self, four_players):
        """During bidding the Current-trick slot becomes the auction diamond."""
        view = RichView()
        north, east, south, west = four_players
        panel = view._panel_current_trick(
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
            return self.value in ("Slam", "SoloSlam")

        def is_slam(self) -> bool:
            return self.value == "Slam"

        def is_solo_slam(self) -> bool:
            return self.value == "SoloSlam"

        def get_base_points(self) -> int:
            if self.value == "Slam":
                return 250
            if self.value == "SoloSlam":
                return 500
            return self.value

        def get_slam_card_substitute(self) -> int:
            if self.value == "Slam":
                return 250
            if self.value == "SoloSlam":
                return 500
            return 0

        def get_multiplier(self) -> int:
            if self.redouble:
                return 4
            if self.double:
                return 2
            return 1

    class _StubRound:
        def __init__(self, *, round_number, contract, round_scores,
                     team_tricks=None):
            self.round_number = round_number
            self.contract = contract
            self.round_scores = round_scores
            self.team_tricks = team_tricks or {}

    def test_recap_made_contract_shows_check(self):
        view = RichView()
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 162, "East-West": 0},
        )
        panel = view._panel_round_recap(round_, {"North-South": 500, "East-West": 0})
        text = panel.renderable.plain
        assert "Round #3 recap" in panel.title.plain
        assert "Contract made" in text
        assert "+162" in text
        assert "500" in text  # running NS total

    def test_recap_failed_contract_shows_cross(self):
        view = RichView()
        contract = self._StubContract(120, Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 280, "East-West": 0},
        )
        panel = view._panel_round_recap(round_, {"North-South": 280, "East-West": 0})
        text = panel.renderable.plain
        assert "Contract failed" in text

    def test_recap_all_passed(self):
        view = RichView()
        round_ = self._StubRound(
            round_number=5,
            contract=None,
            round_scores={"North-South": 0, "East-West": 0},
        )
        panel = view._panel_round_recap(round_, {"North-South": 0, "East-West": 0})
        text = panel.renderable.plain
        assert "All passed" in text
        # No made/failed line for an all-passed round.
        assert "made" not in text
        assert "failed" not in text

    def test_recap_includes_belote_when_kq_of_trump_taken_together(
        self, four_players
    ):
        view = RichView()
        north, *_ = four_players
        contract = self._StubContract(100, Suit.HEARTS, "North-South")
        # Build a fake trick where N took both K♥ and Q♥.
        trick1 = Trick()
        trick1.add_play(north, Card(Suit.HEARTS, Rank.KING))
        trick2 = Trick()
        trick2.add_play(north, Card(Suit.HEARTS, Rank.QUEEN))
        round_ = self._StubRound(
            round_number=2,
            contract=contract,
            round_scores={"North-South": 200, "East-West": 0},
            team_tricks={"North-South": [trick1, trick2], "East-West": []},
        )
        panel = view._panel_round_recap(round_, {"North-South": 200, "East-West": 0})
        text = panel.renderable.plain
        assert "Belote" in text
        assert "+20" in text

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
        panel = view._panel_round_recap(
            round_, {"North-South": 145, "East-West": 0}
        )
        text = panel.renderable.plain
        # Trump-aware card points:
        #   ns_trick1: J♥(20) + 7♥(0)  = 20
        #   ns_trick2: A♠(11) + 9♥(14) = 25
        # N-S total = 45.
        assert "45" in text
        # Trick count line — N-S 2 tricks, E-W 1 trick.
        assert "(2/1 tricks)" in text

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
        panel = view._panel_round_recap(
            round_, {"North-South": 110, "East-West": 0}
        )
        text = panel.renderable.plain
        assert "Last trick" in text
        assert "+10" in text

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
        breakdown = view._recap_breakdown(round_)
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
        contract = self._StubContract("Slam", Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 500},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = view._recap_breakdown(round_)
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
        contract = self._StubContract("Slam", Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 500, "East-West": 0},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = view._recap_breakdown(round_)
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
        contract = self._StubContract("SoloSlam", Suit.SPADES, "East-West")
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 1000},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = view._recap_breakdown(round_)
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
            "SoloSlam", Suit.SPADES, "East-West", double=True
        )
        round_ = self._StubRound(
            round_number=3,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 2000},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = view._recap_breakdown(round_)
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
        breakdown = view._recap_breakdown(round_)
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
        breakdown = view._recap_breakdown(round_)
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
        breakdown = view._recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 260
        assert breakdown["North-South"]["contract"] == 0
        # Defender's cards/dix/belote don't contribute on a failed
        # contract — the engine pays them a flat bonus instead.
        assert breakdown["East-West"]["cards_count"] is False
        # Attacker gets 0 on a failed contract; their cards/dix/belote
        # also don't contribute (round_score is 0).
        assert breakdown["North-South"]["cards_count"] is False

    def test_recap_contract_row_failed_doubled_quadruples_bonus(
        self, four_players
    ):
        """Failed 100 ♥ ×2 by N-S → E-W gets (160+100)*2 = 520."""
        view = RichView()
        contract = self._StubContract(
            100, Suit.HEARTS, "North-South", double=True
        )
        round_ = self._StubRound(
            round_number=4,
            contract=contract,
            round_scores={"North-South": 0, "East-West": 520},
            team_tricks={"North-South": [], "East-West": []},
        )
        breakdown = view._recap_breakdown(round_)
        assert breakdown["East-West"]["contract"] == 520

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
        panel = view._panel_round_recap(
            round_, {"North-South": 500, "East-West": 0}
        )
        text = panel.renderable.plain
        assert "Contract " in text  # row label
        assert "+100" in text  # attacker contract bonus

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
        breakdown = view._recap_breakdown(round_)
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
        panel = view._panel_round_recap(
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
        panel = view._panel_round(self._StubRound(7), phase="bidding")
        assert "Round #7" in panel.title.plain

    def test_title_defaults_when_round_is_none(self):
        view = RichView()
        panel = view._panel_round(None, phase="bidding")
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
        panel = view._panel_current_trick(
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
        panel = view._panel_last_trick(round_)
        assert "Last trick (#7)" in panel.title.plain

    def test_last_trick_title_bare_when_no_round(self):
        view = RichView()
        # No last_completed_trick set → '(none)' panel with the bare
        # "Last trick" title.
        panel = view._panel_last_trick(None)
        assert panel.title.plain == "Last trick"


# ======================================================================
# _parse_card_input
# ======================================================================


class TestParseCardInput:
    """Card-number parser. Validates that the picked card is playable."""

    @pytest.fixture
    def hand(self):
        return [
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ]

    def test_valid_choice_in_playable(self, hand):
        playable = hand[:2]  # only hearts are playable
        assert _parse_card_input("1", hand, playable) is hand[0]
        assert _parse_card_input("2", hand, playable) is hand[1]

    def test_choice_not_in_playable(self, hand):
        """User picks a number that maps to a non-playable card."""
        playable = hand[:2]
        assert _parse_card_input("3", hand, playable) is None  # A♠ not playable

    def test_choice_out_of_range(self, hand):
        assert _parse_card_input("0", hand, hand) is None
        assert _parse_card_input("5", hand, hand) is None

    @pytest.mark.parametrize("raw", ["", "abc", "1.5", "-1", " 1a"])
    def test_non_digit(self, hand, raw):
        assert _parse_card_input(raw, hand, hand) is None

    def test_whitespace_trimmed(self, hand):
        assert _parse_card_input(" 1 ", hand, hand) is hand[0]


# ======================================================================
# _sort_hand_for_display
# ======================================================================


class TestSortHandForDisplay:
    """Display-order sort: trump-first, then suit-by-suit, rank desc."""

    def test_no_trump_default_order(self):
        cards = [
            Card(Suit.CLUBS, Rank.SEVEN),
            Card(Suit.HEARTS, Rank.QUEEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.ACE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        # Default suit order: S, H, D, C
        assert [c.suit for c in result] == [
            Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_trump_goes_first(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.DIAMONDS, Rank.KING),
            Card(Suit.CLUBS, Rank.NINE),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert result[0].suit == Suit.HEARTS
        # Non-trump suits keep S, D, C order with hearts removed.
        assert [c.suit for c in result[1:]] == [
            Suit.SPADES, Suit.DIAMONDS, Suit.CLUBS,
        ]

    def test_within_suit_rank_desc_no_trump(self):
        """Within a non-trump suit, highest rank first (normal order)."""
        cards = [
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.SPADES, Rank.JACK),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        assert [c.rank for c in result] == [Rank.ACE, Rank.JACK, Rank.SEVEN]

    def test_within_trump_suit_uses_trump_order(self):
        """Inside the trump suit, the Jack out-ranks the Ace (trump order)."""
        cards = [
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.NINE),
            Card(Suit.HEARTS, Rank.SEVEN),
        ]
        result = _sort_hand_for_display(cards, trump_suit=Suit.HEARTS)
        # Trump order: 7, 8, Q, K, 10, A, 9, J — so J on top, then 9, then A.
        assert [c.rank for c in result] == [
            Rank.JACK, Rank.NINE, Rank.ACE, Rank.SEVEN,
        ]

    def test_empty_suit_skipped(self):
        cards = [
            Card(Suit.SPADES, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.KING),
        ]
        result = _sort_hand_for_display(cards, trump_suit=None)
        assert len(result) == 2
        assert {c.suit for c in result} == {Suit.SPADES, Suit.DIAMONDS}

    def test_empty_hand_returns_empty(self):
        assert _sort_hand_for_display([], trump_suit=None) == []
        assert _sort_hand_for_display([], trump_suit=Suit.SPADES) == []


# ======================================================================
# _current_winner
# ======================================================================


class TestCurrentWinner:
    """Live trick-winner computation for the diamond gold-pill highlight."""

    def test_empty_plays_returns_none(self):
        assert _current_winner([], trump_suit=Suit.HEARTS) is None
        assert _current_winner([], trump_suit=None) is None

    def test_single_play_wins(self, four_players):
        north, _, _, _ = four_players
        plays = [(north, Card(Suit.SPADES, Rank.SEVEN))]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_of_led_suit_wins_no_trump_played(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.TEN)),
            (east, Card(Suit.SPADES, Rank.ACE)),  # ace wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_off_suit_non_trump_cannot_win(self, four_players):
        """Discarding off-suit (no trump) doesn't take the trick."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.SEVEN)),
            (north, Card(Suit.DIAMONDS, Rank.ACE)),  # off suit, no trump
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is west

    def test_trump_beats_non_trump(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.ACE)),
            (north, Card(Suit.HEARTS, Rank.SEVEN)),  # weakest trump still wins
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is north

    def test_highest_trump_wins(self, four_players):
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),     # led
            (north, Card(Suit.HEARTS, Rank.NINE)),    # trump
            (east, Card(Suit.HEARTS, Rank.JACK)),     # jack is top trump
            (south, Card(Suit.HEARTS, Rank.ACE)),     # ace below jack/9
        ]
        assert _current_winner(plays, trump_suit=Suit.HEARTS) is east

    def test_no_trump_contract_uses_led_suit(self, four_players):
        """``trump_suit=None`` (or NoTrump) means highest led-suit card wins."""
        north, east, south, west = four_players
        plays = [
            (west, Card(Suit.SPADES, Rank.KING)),
            (north, Card(Suit.SPADES, Rank.ACE)),
            (east, Card(Suit.HEARTS, Rank.JACK)),     # off suit, can't win
        ]
        assert _current_winner(plays, trump_suit=None) is north


# ======================================================================
# _explain_constraint
# ======================================================================


class TestExplainConstraint:
    """Human-readable hint under the hand row."""

    def _make_trick(self, *plays):
        t = Trick()
        for player, card in plays:
            t.add_play(player, card)
        return t

    def test_empty_trick_is_your_lead(self, four_players):
        _, _, south, _ = four_players
        south.hand.clear()
        south.hand.append(Card(Suit.SPADES, Rank.ACE))
        empty = Trick()
        result = _explain_constraint(south, empty, list(south.hand), Suit.HEARTS)
        assert "your lead" in result.plain.lower()

    def test_must_follow_led_suit(self, four_players):
        north, _, south, west = four_players
        # West led ♠K, South has ♠s in hand → must follow.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.SPADES, Rank.SEVEN),
            Card(Suit.SPADES, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
        ])
        trick = self._make_trick((west, Card(Suit.SPADES, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.SPADES)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must follow" in result.plain
        assert "♠" in result.plain

    def test_must_trump_when_partner_not_winning(self, four_players):
        north, east, south, west = four_players
        # West led ♣K, South has no clubs, has hearts (trump) → must trump.
        south.hand.clear()
        south.hand.extend([
            Card(Suit.HEARTS, Rank.JACK),
            Card(Suit.HEARTS, Rank.ACE),
            Card(Suit.DIAMONDS, Rank.QUEEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        playable = south.hand.cards_of_suit(Suit.HEARTS)  # only trumps legal
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "must trump" in result.plain
        # The leader's position label should appear in the hint.
        assert "W" in result.plain

    def test_free_discard_when_no_led_suit_no_trump_obligation(self, four_players):
        """No led-suit in hand, playable includes non-trump → free discard."""
        north, _, south, west = four_players
        south.hand.clear()
        south.hand.extend([
            Card(Suit.DIAMONDS, Rank.QUEEN),
            Card(Suit.DIAMONDS, Rank.TEN),
        ])
        trick = self._make_trick((west, Card(Suit.CLUBS, Rank.KING)))
        # Playable list includes non-trump (Round logic decides — when partner
        # leads, the engine returns the full hand). Here we simulate "free".
        playable = list(south.hand)
        result = _explain_constraint(south, trick, playable, Suit.HEARTS)
        assert "free discard" in result.plain


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
        panel = view._panel_hand(
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
        panel = view._panel_hand(
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
        panel = view._panel_hand(
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


# ======================================================================
# _format_summary_contract — end-game round-by-round table cell
# ======================================================================


class TestFormatSummaryContract:
    """The end-game summary contract cell must use English vocabulary
    exclusively — no French ``coinché`` / ``surcoinché`` leakage."""

    class _StubContract:
        def __init__(self, value, suit, *, double=False, redouble=False):
            self.value = value
            self.suit = suit
            self.double = double
            self.redouble = redouble

    @staticmethod
    def _row(contract, team_name="North-South"):
        return RoundSummary(
            round_number=1,
            contract=contract,
            contract_team_name=team_name,
            contract_made=True,
            ns_pts=100,
            ew_pts=0,
            running_ns=100,
            running_ew=0,
        )

    def test_doubled_contract_reads_english(self):
        view = RichView()
        row = self._row(self._StubContract(100, Suit.HEARTS, double=True))
        text = view._format_summary_contract(row).plain
        assert "doubled" in text
        assert "coinché" not in text

    def test_redoubled_contract_reads_english(self):
        view = RichView()
        row = self._row(self._StubContract(100, Suit.HEARTS, redouble=True))
        text = view._format_summary_contract(row).plain
        assert "redoubled" in text
        assert "surcoinché" not in text

    def test_plain_contract_has_no_double_marker(self):
        view = RichView()
        row = self._row(self._StubContract(100, Suit.HEARTS))
        text = view._format_summary_contract(row).plain
        assert "doubled" not in text
        assert "redoubled" not in text
