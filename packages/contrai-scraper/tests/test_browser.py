"""Pins the spectator: the walk, the gates, the panels, the resume frame."""

import asyncio
from typing import Any

import pytest
from contrai_core import Position

from contrai_scraper import BrowserError, Spectator
from contrai_scraper.browser import PAGE_SETTLE_MS


class Match:
    """One element a selector resolved to."""

    def __init__(self, text="", attrs=None, classes=(), children=None):
        self.text = text
        self.attrs = dict(attrs or {})
        self.classes = tuple(classes)
        self.children = dict(children or {})


class FakeLocator:
    def __init__(self, page, selector, matches):
        self.page = page
        self.selector = selector
        # A bare string stands for an element with that text and nothing
        # else, which is what most of these fixtures need.
        self._matches = [
            Match(item) if isinstance(item, str) else item for item in matches
        ]

    async def count(self):
        return len(self._matches)

    async def is_visible(self):
        return bool(self._matches)

    async def click(self, timeout=None):
        if not self._matches:
            raise TimeoutError(self.selector)
        self.page.clicks.append(self.selector)

    async def inner_text(self, timeout=None):
        if not self._matches:
            raise TimeoutError(self.selector)
        return self._matches[0].text

    async def all(self):
        return [FakeLocator(self.page, self.selector, [m]) for m in self._matches]

    @property
    def first(self):
        # Narrowed to at most one match, empty when there was none — the same
        # shape Playwright's own ``.first`` leaves a locator in.
        return FakeLocator(self.page, self.selector, self._matches[:1])

    async def get_attribute(self, name, timeout=None):
        if not self._matches:
            return None
        if len(self._matches) > 1:
            # Mirrors Playwright's real strict-mode error: an action that
            # resolves to one element refuses when the locator did not.
            raise Exception(
                f"strict mode violation: {self.selector} resolved to "
                f"{len(self._matches)} elements"
            )
        if name == "class":
            return " ".join(self._matches[0].classes)
        return self._matches[0].attrs.get(name)

    def locator(self, selector):
        children = self._matches[0].children if self._matches else {}
        return FakeLocator(self.page, selector, children.get(selector, []))


class FakePage:
    """A page answering from a ``selector -> [Match]`` map.

    It implements only the surface ``Spectator`` actually uses. Playwright's
    ``.first`` is a property rather than a method: ``read_options`` narrows a
    child lookup to it before reading an attribute, so this fake's locator
    carries the same property, sliced to its own first match.
    """

    def __init__(self, matches=None):
        self.matches = {k: list(v) for k, v in (matches or {}).items()}
        self.clicks: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.evaluated: list[tuple[str, Any]] = []
        self.init_scripts: list[str] = []
        self.goto_url: str | None = None
        self.waited: list[int] = []
        self.trace: list[tuple[str, Any]] = []
        """Every ``locator``/``goto``/``wait``/``fill`` call, in call order."""

    def locator(self, selector):
        self.trace.append(("locator", selector))
        return FakeLocator(self, selector, self.matches.get(selector, []))

    async def goto(self, url):
        self.trace.append(("goto", url))
        self.goto_url = url

    async def wait_for_timeout(self, ms):
        self.trace.append(("wait", ms))
        self.waited.append(ms)

    async def fill(self, selector, value, timeout=None):
        if selector not in self.matches:
            raise TimeoutError(selector)
        self.trace.append(("fill", selector))
        self.filled.append((selector, value))

    async def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        return True

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    def on(self, name, handler):
        pass


def drain(scenario):
    """Runs an async scenario and returns what it produced.

    ``pytest-asyncio`` is **not** a dependency and must not become one — see
    ``test_frames.py``'s helper of the same name.
    """

    return asyncio.run(scenario())


class LatePledgePage(FakePage):
    """A page whose pledge is drawn only after the walk first looked for it.

    Until the pledge is accepted it covers the spectator menu, so the menu
    click fails — the timing the browser-flow probe met on fresh accounts.
    """

    def __init__(self):
        super().__init__({"#online": ["Online"], "#pledge-ok": ["OK"],
                          "#variant": ["Contree"]})
        self.probes = 0

    def locator(self, selector):
        answered = "#pledge-ok" in self.clicks
        if selector == "#pledge":
            self.probes += 1
            drawn = self.probes > 1 and not answered
            return FakeLocator(self, selector, ["Fair play"] if drawn else [])
        if selector == "#observe":
            return FakeLocator(self, selector, ["Watch"] if answered else [])
        return super().locator(selector)


def option_row(name=None, on=False, *, state=True):
    """One options-panel row, laid out as the observed panel lays it out.

    The id sits on one child and the on/off class on another, so a reader
    that looks for both on the row itself finds neither. ``name=None`` leaves
    the id attribute off; ``state=False`` leaves the switch out, as a group
    heading does.
    """

    children = {".option-name": [Match(attrs={} if name is None else {"data-option": name})]}
    if state:
        children[".option-switch"] = [
            Match(classes=("option-switch", "on") if on else ("option-switch",))
        ]
    return Match(classes=("option-row",), children=children)


def option_panel(**options):
    """A table options panel showing ``id -> on`` and nothing else."""

    return {
        "#options": ["Options"],
        ".option-row": [option_row(name, on) for name, on in options.items()],
        "#close": ["x"],
    }


def score_panel(*rows, header=False):
    """A scoreboard panel showing one ``(us, them)`` pair per row."""

    cells = [
        Match(f"{us} {them}",
              children={".score-cell": [Match(str(us)), Match(str(them))]})
        for us, them in rows
    ]
    if header:
        cells.insert(
            0,
            Match("us them",
                  children={".score-cell": [Match("us"), Match("them")]}),
        )
    return {"#scoreboard": ["Scores"], ".score-row": cells, "#close": ["x"]}


class TestPledge:
    def test_a_showing_pledge_is_answered(self, profile):
        page = FakePage({"#pledge": ["Fair play"], "#pledge-ok": ["OK"]})

        async def scenario():
            return await Spectator(page, profile).answer_pledge()

        assert (drain(scenario), page.clicks) == (True, ["#pledge-ok"])

    def test_an_absent_pledge_is_not_an_error(self, profile):
        # It appears once per account and never again, so the common path is
        # "not there". Waiting for it would stall every later session.
        page = FakePage({})

        async def scenario():
            return await Spectator(page, profile).answer_pledge()

        assert (drain(scenario), page.clicks) == (False, [])


class TestLogin:
    def test_the_address_and_the_code_are_filled_in_order(self, profile):
        page = FakePage({"#by-email": ["Email"], "#email": ["input"],
                         "#go": ["Continue"], "#code": ["input"], "#submit": ["OK"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        drain(scenario)
        assert (page.goto_url, page.filled, page.clicks) == (
            "https://example.invalid/lobby",
            [("#email", "watcher@example.invalid"), ("#code", "0000")],
            ["#by-email", "#go", "#submit"],
        )

    def test_a_showing_tutorial_is_dismissed_first(self, profile):
        page = FakePage({"#by-email": ["Email"], "#no-thanks": ["No thanks"],
                         "#email": ["input"], "#go": ["Continue"],
                         "#code": ["input"], "#submit": ["OK"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        drain(scenario)
        assert page.clicks[0] == "#no-thanks"

    def test_an_absent_tutorial_is_skipped(self, profile):
        # The offer comes once per browser profile, so every later session
        # logs in without it.
        page = FakePage({"#by-email": ["Email"], "#email": ["input"],
                         "#go": ["Continue"], "#code": ["input"], "#submit": ["OK"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        drain(scenario)
        assert "#no-thanks" not in page.clicks

    def test_the_landing_page_settles_before_anything_is_probed(self, profile):
        # The tutorial is probed, not waited for; probed the instant
        # navigation returns, it is missed — and then covers the login entry.
        page = FakePage({"#by-email": ["Email"], "#email": ["input"],
                         "#go": ["Continue"], "#code": ["input"], "#submit": ["OK"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        drain(scenario)
        # The order itself is the point: each marker's first occurrence must
        # come strictly after the one before it.
        markers = [
            ("goto", "https://example.invalid/lobby"),
            ("wait", PAGE_SETTLE_MS),
            ("locator", "#no-thanks"),
            ("locator", "#by-email"),
            ("fill", "#email"),
        ]
        indices = [page.trace.index(marker) for marker in markers]
        for earlier, later in zip(indices, indices[1:]):
            assert earlier < later

    def test_a_missing_email_entry_names_the_key(self, profile):
        page = FakePage({"#email": ["input"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        with pytest.raises(BrowserError, match="login_start"):
            drain(scenario)


class TestMenu:
    def test_entering_the_variant_walks_the_menu(self, profile):
        page = FakePage({"#online": ["Online"], "#observe": ["Watch"],
                         "#variant": ["Contree"]})

        async def scenario():
            return await Spectator(page, profile).enter_variant()

        assert (drain(scenario), page.clicks) == (False, ["#online", "#observe", "#variant"])

    def test_a_pledge_between_the_steps_is_answered(self, profile):
        # It blocks the spectator menu, so the walk stops at the second step
        # unless the dialog is answered on the way through — and the walk
        # says so, because only it can see the dialog between the steps.
        page = FakePage({"#online": ["Online"], "#pledge": ["Fair play"],
                         "#pledge-ok": ["OK"], "#observe": ["Watch"],
                         "#variant": ["Contree"]})

        async def scenario():
            return await Spectator(page, profile).enter_variant()

        assert (drain(scenario), page.clicks) == (
            True, ["#online", "#pledge-ok", "#observe", "#variant"]
        )

    def test_a_pledge_drawn_late_is_answered_and_the_menu_retried(self, profile):
        page = LatePledgePage()

        async def scenario():
            return await Spectator(page, profile).enter_variant()

        assert (drain(scenario), page.clicks) == (
            True, ["#online", "#pledge-ok", "#observe", "#variant"]
        )

    def test_a_blocked_menu_with_no_pledge_still_names_the_key(self, profile):
        page = FakePage({"#online": ["Online"], "#variant": ["Contree"]})

        async def scenario():
            await Spectator(page, profile).enter_variant()

        with pytest.raises(BrowserError, match="mode_observe"):
            drain(scenario)

    def test_the_hop_is_the_table_control(self, profile):
        # There is no leave operation to test: the exit control leaves
        # spectating, and nothing in-session recovers from that.
        page = FakePage({"#next": ["Another table"]})

        async def scenario():
            await Spectator(page, profile).next_table()

        drain(scenario)
        assert page.clicks == ["#next"]


class TestMarker:
    def test_a_tournament_marker_is_recognised(self, profile):
        page = FakePage({"#table-kind": ["Cup — round 3"]})

        async def scenario():
            return await Spectator(page, profile).read_tournament_marker()

        assert drain(scenario) is True

    def test_a_plain_table_is_not_a_tournament(self, profile):
        page = FakePage({"#table-kind": ["Friendly table"]})

        async def scenario():
            return await Spectator(page, profile).read_tournament_marker()

        assert drain(scenario) is False

    def test_an_absent_marker_is_not_a_tournament(self, profile):
        page = FakePage({})

        async def scenario():
            return await Spectator(page, profile).read_tournament_marker()

        assert drain(scenario) is False


class TestOptionsGate:
    def test_a_matching_table_reports_no_difference(self, profile):
        page = FakePage(option_panel(opt_alpha=True, opt_beta=False))

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        reading = drain(scenario)
        assert (reading.matches, reading.observed) == (
            True,
            {"opt_alpha": True, "opt_beta": False},
        )

    def test_a_flipped_option_is_reported_as_differing(self, profile):
        page = FakePage(option_panel(opt_alpha=True, opt_beta=True))

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        reading = drain(scenario)
        assert (reading.matches, reading.differing) == (False, ("opt_beta",))

    def test_an_absent_option_is_reported_as_missing(self, profile):
        page = FakePage(option_panel(opt_alpha=True))

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        reading = drain(scenario)
        assert (reading.matches, reading.missing) == (False, ("opt_beta",))

    def test_an_unexpected_option_is_reported_as_extra(self, profile):
        # An option the profile has never seen may well change the rules, and
        # a record claiming the expected preset would then be wrong about
        # them. The table is refused until the profile says what it is.
        page = FakePage(option_panel(opt_alpha=True, opt_beta=False,
                                     opt_gamma=True))

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        reading = drain(scenario)
        assert (reading.matches, reading.extra) == (False, ("opt_gamma",))

    def test_a_row_without_an_id_is_ignored(self, profile):
        page = FakePage({"#options": ["Options"],
                         ".option-row": [option_row(),
                                         option_row("opt_alpha", True),
                                         option_row("opt_beta", False)],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        assert drain(scenario).matches is True

    def test_a_heading_row_without_a_switch_is_ignored(self, profile):
        # The panel interleaves group headings and the objective selector with
        # the toggles. Neither carries a switch, and neither is an option.
        page = FakePage({"#options": ["Options"],
                         ".option-row": [option_row("objective", state=False),
                                         option_row("opt_alpha", True),
                                         option_row("opt_beta", False)],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        assert drain(scenario).observed == {"opt_alpha": True, "opt_beta": False}

    def test_a_row_without_an_id_element_is_ignored(self, profile):
        page = FakePage({"#options": ["Options"],
                         ".option-row": [Match(classes=("option-row",)),
                                         option_row("opt_alpha", True),
                                         option_row("opt_beta", False)],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        assert drain(scenario).matches is True

    def test_the_panel_is_closed_after_an_options_read(self, profile):
        page = FakePage(option_panel(opt_alpha=True, opt_beta=False))

        async def scenario():
            await Spectator(page, profile).read_options(profile.rules.options)

        drain(scenario)
        assert page.clicks[-1] == "#close"

    def test_a_row_whose_id_element_matches_twice_reads_the_first(self, profile):
        # The id and the switch each sit on a child selector, and nothing
        # promises that selector is unique inside the row. Reading the first
        # match rather than tripping Playwright's strict mode is the point.
        row = Match(
            classes=("option-row",),
            children={
                ".option-name": [
                    Match(attrs={"data-option": "opt_alpha"}),
                    Match(attrs={"data-option": "opt_other"}),
                ],
                ".option-switch": [Match(classes=("option-switch", "on"))],
            },
        )
        page = FakePage({"#options": ["Options"],
                         ".option-row": [row, option_row("opt_beta", False)],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_options(
                profile.rules.options
            )

        reading = drain(scenario)
        assert reading.observed == {"opt_alpha": True, "opt_beta": False}


class TestScoreboard:
    def test_rows_are_read_oldest_first(self, profile):
        page = FakePage(score_panel((90, 72), (100, 62)))

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).rows == ((90, 72), (100, 62))

    def test_an_empty_panel_is_a_valid_reading(self, profile):
        # A game with no scored round yet reads zero rows. P-B initially
        # scored that as a failure and it was not one.
        page = FakePage(score_panel())

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).rows == ()

    def test_a_row_that_is_not_a_pair_of_numbers_is_skipped(self, profile):
        page = FakePage(score_panel((90, 72), header=True))

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).rows == ((90, 72),)

    def test_a_row_with_a_single_cell_is_skipped(self, profile):
        page = FakePage({"#scoreboard": ["Scores"],
                         ".score-row": [Match("total",
                                              children={".score-cell": [Match("90")]})],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).rows == ()

    def test_the_rows_text_is_kept_for_the_raw_log(self, profile):
        page = FakePage(score_panel((90, 72), (100, 62)))

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).text == "90 72\n100 62"

    def test_the_panel_is_closed_after_a_read(self, profile):
        page = FakePage(score_panel((90, 72)))

        async def scenario():
            await Spectator(page, profile).read_scoreboard()

        drain(scenario)
        assert page.clicks[-1] == "#close"


class TestPlayerPanel:
    def test_a_seat_panel_reports_its_id(self, profile):
        page = FakePage({"#seat-bottom": ["South"],
                         ".player-panel .title": ["no. 1003"],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_player_id(Position.SOUTH)

        assert drain(scenario) == "1003"

    def test_the_panel_is_closed_after_a_seat_read(self, profile):
        page = FakePage({"#seat-bottom": ["South"],
                         ".player-panel .title": ["no. 1003"],
                         "#close": ["x"]})

        async def scenario():
            await Spectator(page, profile).read_player_id(Position.SOUTH)

        drain(scenario)
        assert page.clicks == ["#seat-bottom", "#close"]

    def test_a_missing_seat_element_names_the_key(self, profile):
        page = FakePage({})

        async def scenario():
            await Spectator(page, profile).read_player_id(Position.SOUTH)

        with pytest.raises(BrowserError, match="seat_element"):
            drain(scenario)

    def test_a_panel_without_a_title_names_the_key(self, profile):
        page = FakePage({"#seat-bottom": ["South"], "#close": ["x"]})

        async def scenario():
            await Spectator(page, profile).read_player_id(Position.SOUTH)

        with pytest.raises(BrowserError, match="player_id_title"):
            drain(scenario)

    def test_a_title_holding_no_number_reads_as_unknown(self, profile):
        # A guest seat shows a label and no account, and a record is better
        # off saying nothing than saying the label.
        page = FakePage({"#seat-bottom": ["South"],
                         ".player-panel .title": ["guest"],
                         "#close": ["x"]})

        async def scenario():
            return await Spectator(page, profile).read_player_id(Position.SOUTH)

        assert drain(scenario) is None


class TestResume:
    def test_the_resume_frame_carries_the_last_event_id(self, profile):
        # Without the parameter the server acknowledges and ignores the
        # request — 0 snapshots in 4 attempts.
        page = FakePage({})

        async def scenario():
            return await Spectator(page, profile).request_state("t1", "f-42")

        drain(scenario)
        sent = page.evaluated[0][1]
        assert (sent["action"], sent["data"]["room"],
                sent["data"]["params"]["lastSeen"]) == ("resume", "room-t1", "f-42")

    def test_a_send_that_finds_no_socket_reports_failure(self, profile):
        # The page keeps its sockets, so "none open" means the connection
        # dropped; the caller falls back to the panel rather than waiting for
        # a snapshot that is not coming.
        page = FakePage({})

        async def scenario():
            spectator = Spectator(page, profile)
            page.evaluate = _refusing(page)
            return await spectator.request_state("t1", "f-42")

        assert drain(scenario) is False

    def test_each_request_carries_an_id_of_its_own(self, profile):
        page = FakePage({})

        async def scenario():
            spectator = Spectator(page, profile)
            await spectator.request_state("t1", "f-1")
            await spectator.request_state("t1", "f-2")

        drain(scenario)
        first, second = (sent["id"] for _, sent in page.evaluated)
        assert first != second


def _refusing(page):
    """A ``page.evaluate`` that reports the send did not go."""

    async def evaluate(script, arg=None):
        page.evaluated.append((script, arg))
        return False

    return evaluate


class TestWalk:
    def test_a_missing_step_names_the_profile_key(self, profile):
        # A timeout on a menu button looks identical whether the cause is a
        # refused login, a declined session or a dialog nobody knew about.
        page = FakePage({})

        async def scenario():
            await Spectator(page, profile).enter_variant()

        with pytest.raises(BrowserError, match="mode_online"):
            drain(scenario)

    def test_an_error_never_quotes_the_selector(self, profile):
        # The repo names the site nowhere, and a message is as tracked as a
        # docstring once it lands in a log somebody pastes into an issue.
        page = FakePage({})

        async def scenario():
            await Spectator(page, profile).enter_variant()

        with pytest.raises(BrowserError) as raised:
            drain(scenario)
        assert "#online" not in str(raised.value)

    def test_a_missing_field_names_the_profile_key(self, profile):
        # A login form that moved its address input fails here rather than
        # three steps later on a verification code nobody was asked for.
        page = FakePage({"#by-email": ["Email"], "#go": ["Continue"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        with pytest.raises(BrowserError, match="login_email"):
            drain(scenario)

    def test_a_selector_list_falls_back_to_the_next_candidate(self, profile):
        page = FakePage({"#by-email": ["Email"], "#email": ["input"],
                         "#go-icon": ["icon"], "#code": ["input"], "#submit": ["OK"]})

        async def scenario():
            await Spectator(page, profile).log_in()

        drain(scenario)
        assert page.clicks == ["#by-email", "#go-icon", "#submit"]
