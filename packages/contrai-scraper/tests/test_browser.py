"""Pins the spectator: the walk, the gates, the panels, the resume frame."""

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from contrai_core import Position

from contrai_scraper import BrowserError, Spectator
from contrai_scraper.browser import (
    PAGE_SETTLE_MS,
    PANEL_ATTEMPT_TIMEOUT_MS,
    PANEL_ATTEMPTS,
    STEP_TIMEOUT_MS,
)


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


class RailLocator(FakeLocator):
    """The rail toggle. Clicking it brings the rails back in."""

    async def click(self, timeout=None):
        await super().click(timeout=timeout)
        self.page.rails_out = False


class CoveredPanelPage(FakePage):
    """A table whose panel control is covered when the read starts.

    Reproduces what 9A measured. The rails are *in*, so the toggle is hidden
    and there is correctly nothing to reveal; a transient dialog is drawn
    over the control, so the click cannot land; and the rails then slide away
    under the waiting click. That last step is what turns a moment's blockage
    into a dead session, because the control is now off-screen and the click
    that is still waiting has no way to ask for it back.
    """

    def __init__(self, matches, *, covered="#options", attempts=1):
        super().__init__(matches)
        self.covered = covered
        self.blocked = attempts
        self.rails_out = False

    def locator(self, selector):
        if selector == "#rail-in:visible":
            return RailLocator(self, selector, ["<"] if self.rails_out else [])
        if selector == self.covered and self.blocked:
            self.blocked -= 1
            self.rails_out = True
            return FakeLocator(self, selector, [])
        return super().locator(selector)


class CapturePage:
    """A page that can be photographed, or refuse to be."""

    def __init__(self, *, shot=True, dom=True):
        self._shot = shot
        self._dom = dom
        self.shots: list[str] = []

    async def screenshot(self, path):
        if not self._shot:
            raise RuntimeError("the page went away")
        self.shots.append(path)

    async def content(self):
        if not self._dom:
            raise RuntimeError("the page went away")
        return "<html></html>"


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


class TestRails:
    def test_the_rail_is_pulled_back_before_a_panel_opens(self, profile):
        # The table's rails slide away during play. Their buttons stay in the
        # DOM, translated off the screen, so a click on one waits out its
        # timeout rather than landing. The site's own toggle pulls them back,
        # and that toggle is showing exactly while they are away.
        page = FakePage({**option_panel(opt_alpha=True, opt_beta=False),
                         "#rail-in:visible": ["<"]})

        async def scenario():
            await Spectator(page, profile).read_options(profile.rules.options)

        drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#options", "#close"]

    def test_rails_that_are_already_in_are_left_alone(self, profile):
        # The toggle is hidden while the rails are in, so its :visible
        # selector matches nothing and the panel opens straight away.
        page = FakePage(option_panel(opt_alpha=True, opt_beta=False))

        async def scenario():
            await Spectator(page, profile).read_options(profile.rules.options)

        drain(scenario)
        assert page.clicks == ["#options", "#close"]

    def test_a_profile_naming_no_toggle_opens_the_panel_anyway(self, profile):
        # The key is optional: a site whose controls never move needs none.
        bare = dataclasses.replace(
            profile,
            selectors=dataclasses.replace(profile.selectors, rail_show=None),
        )
        page = FakePage({**option_panel(opt_alpha=True, opt_beta=False),
                         "#rail-in:visible": ["<"]})

        async def scenario():
            await Spectator(page, bare).read_options(bare.rules.options)

        drain(scenario)
        assert page.clicks == ["#options", "#close"]

    def test_the_scoreboard_read_pulls_the_rail_back_too(self, profile):
        page = FakePage({**score_panel((90, 72)), "#rail-in:visible": ["<"]})

        async def scenario():
            await Spectator(page, profile).read_scoreboard()

        drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#scoreboard", "#close"]

    def test_the_seat_panel_read_pulls_the_rail_back_too(self, profile):
        page = FakePage({"#seat-bottom": ["South"],
                         ".player-panel .title": ["no. 1003"],
                         "#close": ["x"], "#rail-in:visible": ["<"]})

        async def scenario():
            await Spectator(page, profile).read_player_id(Position.SOUTH)

        drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#seat-bottom", "#close"]

    def test_a_control_covered_at_first_is_clicked_on_the_next_attempt(self, profile):
        # 9A's fault, in one page. The first click cannot land, and the rails
        # leave while it waits — so a single attempt can never recover, however
        # long it is given. The second attempt reveals them again and lands.
        page = CoveredPanelPage(
            {**option_panel(opt_alpha=True, opt_beta=False), "#rail-in:visible": ["<"]}
        )

        async def scenario():
            return await Spectator(page, profile).read_options(profile.rules.options)

        reading = drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#options", "#close"]
        assert reading.matches

    def test_a_control_that_never_frees_names_its_key(self, profile):
        page = CoveredPanelPage(
            {**option_panel(opt_alpha=True), "#rail-in:visible": ["<"]},
            attempts=PANEL_ATTEMPTS,
        )

        async def scenario():
            await Spectator(page, profile).read_options(profile.rules.options)

        with pytest.raises(BrowserError, match=r"\[selectors\].options_button"):
            drain(scenario)
        # The rails are revealed again at the top of every attempt but the
        # first, which found them already in.
        assert page.clicks == ["#rail-in:visible"] * (PANEL_ATTEMPTS - 1)

    def test_each_attempt_waits_a_share_of_one_step(self, profile):
        # The attempts together must still cost what a single step costs, or a
        # panel read could outlast the rest of the walk.
        assert PANEL_ATTEMPT_TIMEOUT_MS * PANEL_ATTEMPTS == STEP_TIMEOUT_MS

    def test_the_scoreboard_control_is_retried_too(self, profile):
        page = CoveredPanelPage(
            {**score_panel((90, 72)), "#rail-in:visible": ["<"]}, covered="#scoreboard"
        )

        async def scenario():
            return await Spectator(page, profile).read_scoreboard()

        assert drain(scenario).rows == ((90, 72),)
        assert page.clicks == ["#rail-in:visible", "#scoreboard", "#close"]

    def test_the_seat_element_is_retried_too(self, profile):
        # The per-seat selector is built rather than read, and took a
        # single-shot path of its own before the retry existed.
        page = CoveredPanelPage(
            {"#seat-bottom": ["South"], ".player-panel .title": ["no. 1003"],
             "#close": ["x"], "#rail-in:visible": ["<"]},
            covered="#seat-bottom",
        )

        async def scenario():
            return await Spectator(page, profile).read_player_id(Position.SOUTH)

        assert drain(scenario) == "1003"
        assert page.clicks == ["#rail-in:visible", "#seat-bottom", "#close"]

    def test_the_hop_pulls_the_rail_back_too(self, profile):
        # The hop control lives on a rail like every other panel button, and
        # took the single-shot path until 9B's 24-hour run ended two sessions
        # on it.
        page = FakePage({"#next": ["Another table"], "#rail-in:visible": ["<"]})

        async def scenario():
            await Spectator(page, profile).next_table()

        drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#next"]

    def test_the_hop_control_is_retried_too(self, profile):
        page = CoveredPanelPage(
            {"#next": ["Another table"], "#rail-in:visible": ["<"]}, covered="#next"
        )

        async def scenario():
            await Spectator(page, profile).next_table()

        drain(scenario)
        assert page.clicks == ["#rail-in:visible", "#next"]


class TestCapture:
    def test_both_the_image_and_the_dom_are_written(self, profile, tmp_path):
        page = CapturePage()

        async def scenario():
            return await Spectator(page, profile).capture(tmp_path / "session")

        saved = drain(scenario)
        assert saved == (tmp_path / "session.png", tmp_path / "session.html")
        assert (tmp_path / "session.html").read_text(encoding="utf-8") == "<html></html>"
        assert page.shots == [str(tmp_path / "session.png")]

    def test_a_page_that_cannot_be_photographed_still_yields_its_dom(
        self, profile, tmp_path
    ):
        # A diagnosis must never replace the failure it was taken for, so each
        # half is written on its own.
        page = CapturePage(shot=False)

        async def scenario():
            return await Spectator(page, profile).capture(tmp_path / "session")

        assert drain(scenario) == (tmp_path / "session.html",)

    def test_a_page_that_cannot_be_read_still_yields_its_image(self, profile, tmp_path):
        page = CapturePage(dom=False)

        async def scenario():
            return await Spectator(page, profile).capture(tmp_path / "session")

        assert drain(scenario) == (tmp_path / "session.png",)

    def test_a_page_that_is_gone_yields_nothing_and_does_not_raise(
        self, profile, tmp_path
    ):
        page = CapturePage(shot=False, dom=False)

        async def scenario():
            return await Spectator(page, profile).capture(tmp_path / "session")

        assert drain(scenario) == ()


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


class SessionLog:
    """What a fake browser was asked, in the order it was asked."""

    def __init__(self):
        self.calls: list[tuple] = []


class SessionPage:
    def __init__(self, log, index):
        self.log = log
        self.index = index

    def on(self, name, handler):
        self.log.calls.append(("on", self.index, name))


class SessionContext:
    def __init__(self, log, index):
        self.log = log
        self.index = index

    async def add_init_script(self, script):
        self.log.calls.append(("init_script", self.index, script))

    async def new_page(self):
        self.log.calls.append(("new_page", self.index))
        return SessionPage(self.log, self.index)

    async def close(self):
        self.log.calls.append(("close", self.index))


class SessionBrowser:
    """A browser whose contexts only say what was done to them."""

    def __init__(self):
        self.log = SessionLog()
        self.opened = 0

    async def new_context(self):
        index = self.opened
        self.opened += 1
        self.log.calls.append(("new_context", index))
        return SessionContext(self.log, index)


class TestSessions:
    def test_the_socket_script_precedes_the_page_and_the_listener_precedes_use(
        self, profile
    ):
        # The script must be on the context before the page exists, so it runs
        # on every document the page loads; the frame source must be listening
        # before anyone navigates, or the join snapshot is gone.
        from contrai_scraper import INIT_SCRIPT, open_session

        browser = SessionBrowser()

        async def scenario():
            async with open_session(browser, profile):
                return list(browser.log.calls)

        assert drain(scenario) == [
            ("new_context", 0),
            ("init_script", 0, INIT_SCRIPT),
            ("new_page", 0),
            ("on", 0, "websocket"),
        ]

    def test_the_context_is_closed_however_the_session_ends(self, profile):
        from contrai_scraper import open_session

        browser = SessionBrowser()

        async def scenario():
            async with open_session(browser, profile):
                raise BrowserError("the walk broke")

        with pytest.raises(BrowserError):
            drain(scenario)
        assert browser.log.calls[-1] == ("close", 0)

    def test_two_sessions_on_one_browser_are_two_contexts(self, profile):
        # One Chromium carries the fleet; each worker still gets a login and
        # sockets of its own, and closing one leaves the other open.
        from contrai_scraper import open_session

        browser = SessionBrowser()

        async def scenario():
            async with open_session(browser, profile) as (first, _):
                async with open_session(browser, profile) as (second, _):
                    pass
                return first is not second, list(browser.log.calls)

        distinct, calls = drain(scenario)
        assert (distinct, browser.opened, calls[-1], ("close", 0) in calls) == (
            True, 2, ("close", 1), False
        )

    def test_the_session_speaks_for_the_profile_it_was_given(self, profile):
        # A fleet worker's profile is the site's with its own account swapped
        # in; the spectator must log in as that account, not the site's.
        from contrai_scraper import AccountSection, open_session

        worker = dataclasses.replace(
            profile, account=AccountSection("bot@example.invalid", "4242")
        )
        page = FakePage({"#by-email": ["Email"], "#email": ["input"],
                         "#go": ["Continue"], "#code": ["input"], "#submit": ["OK"]})

        class OnePageContext(SessionContext):
            async def new_page(self):
                return page

        class OnePageBrowser(SessionBrowser):
            async def new_context(self):
                return OnePageContext(self.log, 0)

        async def scenario():
            async with open_session(OnePageBrowser(), worker) as (spectator, _):
                await spectator.log_in()

        drain(scenario)
        assert page.filled == [("#email", "bot@example.invalid"), ("#code", "4242")]


# ---------------------------------------------------------------------------
# The lobby
# ---------------------------------------------------------------------------


class ScreenLocator(FakeLocator):
    """A locator whose ``nth`` match can be clicked, as a back control is."""

    def nth(self, index):
        return NthMatch(self.page, self.selector, index)


class NthMatch:
    def __init__(self, page, selector, index):
        self.page = page
        self.selector = selector
        self.index = index

    async def click(self, timeout=None):
        page = self.page
        page.nth_clicks.append((self.selector, self.index))
        if page.stuck:
            raise TimeoutError(self.selector)
        # A back control takes the page down one screen: the one it sits on.
        if page.layout[self.selector][self.index] == page.screens[-1]:
            page.screens.pop()


class ScreenPage(FakePage):
    """A page of stacked screens, of which only the top one shows.

    ``layout`` says which screen each match of a selector sits on, in DOM
    order — every screen keeps its controls in the DOM, which is the whole
    difficulty — and the page's own script reports a control's *screen* as
    displayed only when that screen is on top.
    """

    def __init__(self, matches=None, *, screens=("online",), layout=None,
                 centre=None):
        super().__init__(matches)
        self.screens = list(screens)
        self.layout = {key: list(value) for key, value in (layout or {}).items()}
        self.centre = centre if centre is not None else {
            "x": 640, "y": 360, "found": True, "inList": True}
        self.mouse_clicks: list[tuple[float, float]] = []
        self.mouse = SimpleNamespace(click=self._mouse_click)
        self.nth_clicks: list[tuple[str, int]] = []
        self.stuck = False
        self.script_fails = False

    async def _mouse_click(self, x, y):
        self.mouse_clicks.append((x, y))

    def locator(self, selector):
        self.trace.append(("locator", selector))
        return ScreenLocator(self, selector, self.matches.get(selector, []))

    async def evaluate(self, script, arg=None):
        from contrai_scraper.browser import READ_CENTRE, READ_CONTROLS

        self.evaluated.append((script, arg))
        if script == READ_CONTROLS:
            if self.script_fails:
                raise ValueError("not a selector the page's own script can read")
            selectors, _ = arg
            top = self.screens[-1] if self.screens else None
            return [
                control(rank=rank, index=index,
                        layerDisplay="block" if screen == top else "none")
                for rank, selector in enumerate(selectors)
                for index, screen in enumerate(self.layout.get(selector, []))
            ]
        if script == READ_CENTRE:
            return self.centre
        return True


def control(**overrides):
    """One entry of the page's control reading, usable unless overridden."""

    entry = {
        "rank": 0, "index": 0, "onScreen": True,
        "display": "inline-block", "visibility": "visible", "opacity": "1",
        "layerDisplay": "block", "layerVisibility": "visible", "layerOpacity": "1",
    }
    entry.update(overrides)
    return entry


#: The four screens the lobby stacks, bottom first, and where their back
#: controls sit: the menus back out through one icon, the list through
#: another. This is the structure that killed three probe runs.
FOUR_SCREENS = ("mode", "online", "versions", "tables")
FOUR_SCREEN_BACKS = {"#tool-back": ["online", "versions"], "#tool-close": ["tables"]}


class TestUsable:
    def test_a_control_on_the_screen_shown_is_usable(self):
        from contrai_scraper.browser import _usable

        assert _usable(control()) is True

    @pytest.mark.parametrize("overrides", [
        {"onScreen": False},
        {"display": "none"},
        {"visibility": "hidden"},
        {"layerDisplay": "none"},
        {"layerVisibility": "hidden"},
        {"layerOpacity": "0"},
        {"opacity": "0.05"},
    ], ids=["off-screen", "not-displayed", "hidden", "screen-not-displayed",
            "screen-hidden", "screen-faded", "faded"])
    def test_a_control_a_click_could_not_reach_is_not(self, overrides):
        from contrai_scraper.browser import _usable

        assert _usable(control(**overrides)) is False

    def test_an_unreadable_opacity_is_not_evidence_of_fading(self):
        from contrai_scraper.browser import _usable

        assert _usable(control(opacity="auto", layerOpacity=None)) is True

    def test_a_control_on_no_layer_is_judged_by_its_own_style(self):
        from contrai_scraper.browser import _usable

        loose = control(layerDisplay=None, layerVisibility=None, layerOpacity=None)
        assert _usable(loose) is True


class TestBackControl:
    def test_the_screen_shown_decides_not_the_ranking(self, profile):
        # The better-ranked control exists twice over, but on the two menu
        # screens behind the list: clicking it is what timed the runs out.
        page = ScreenPage(screens=FOUR_SCREENS, layout=FOUR_SCREEN_BACKS)

        async def scenario():
            await Spectator(page, profile).back_once()

        drain(scenario)
        assert (page.nth_clicks, page.screens[-1]) == ([("#tool-close", 0)], "versions")

    def test_two_steps_back_take_two_different_controls(self, profile):
        page = ScreenPage(screens=FOUR_SCREENS, layout=FOUR_SCREEN_BACKS)

        async def scenario():
            spectator = Spectator(page, profile)
            await spectator.back_once()
            await spectator.back_once()

        drain(scenario)
        assert page.nth_clicks == [("#tool-close", 0), ("#tool-back", 1)]

    def test_the_best_ranked_usable_control_wins(self, profile):
        page = ScreenPage(screens=("tables",),
                          layout={"#tool-back": ["tables"], "#tool-close": ["tables"]})

        async def scenario():
            await Spectator(page, profile).back_once()

        drain(scenario)
        assert page.nth_clicks == [("#tool-back", 0)]

    def test_no_control_on_the_screen_shown_is_counted_never_quoted(self, profile):
        page = ScreenPage(screens=(*FOUR_SCREENS, "table-view"), layout=FOUR_SCREEN_BACKS)

        async def scenario():
            await Spectator(page, profile).back_once()

        with pytest.raises(BrowserError) as raised:
            drain(scenario)
        message = str(raised.value)
        assert ("lobby_back matched 3 control(s)" in message, "#tool" in message) == (
            True, False)

    def test_a_chosen_control_that_takes_no_click_is_a_browser_error(self, profile):
        page = ScreenPage(screens=FOUR_SCREENS, layout=FOUR_SCREEN_BACKS)
        page.stuck = True

        async def scenario():
            await Spectator(page, profile).back_once()

        with pytest.raises(BrowserError, match="lobby_back"):
            drain(scenario)

    def test_a_selector_the_pages_script_cannot_read_names_its_key(self, profile):
        page = ScreenPage(screens=FOUR_SCREENS, layout=FOUR_SCREEN_BACKS)
        page.script_fails = True

        async def scenario():
            await Spectator(page, profile).back_once()

        with pytest.raises(BrowserError, match="lobby_back.*plain CSS"):
            drain(scenario)


class TestOverlay:
    @pytest.mark.parametrize("centre", [
        {"x": 640, "y": 360, "found": False, "inList": False},
        {"x": 640, "y": 360, "found": True, "inList": True},
    ], ids=["nothing-under-the-centre", "the-list-under-the-centre"])
    def test_no_overlay_means_no_click(self, profile, centre):
        # Without the overlay, a click at the centre lands on a table slot
        # and sits the account down: a click that changes the site.
        page = ScreenPage(centre=centre)

        async def scenario():
            return await Spectator(page, profile).dismiss_overlay()

        assert (drain(scenario), page.mouse_clicks) == (False, [])

    def test_an_overlay_over_the_list_is_clicked_away(self, profile):
        page = ScreenPage(centre={"x": 640, "y": 360, "found": True, "inList": False})

        async def scenario():
            return await Spectator(page, profile).dismiss_overlay()

        assert (drain(scenario), page.mouse_clicks) == (True, [(640, 360)])

    def test_the_overlay_check_asks_about_the_list(self, profile):
        from contrai_scraper.browser import READ_CENTRE

        page = ScreenPage()

        async def scenario():
            await Spectator(page, profile).dismiss_overlay()

        drain(scenario)
        assert page.evaluated == [(READ_CENTRE, ".slot-list")]


class TestLobbyWalk:
    def test_entering_the_lobby_walks_the_menu(self, profile):
        page = ScreenPage({"#online": ["Online"], "#new-games": ["New"],
                           "#new-variant": ["Contree"]})

        async def scenario():
            return await Spectator(page, profile).enter_lobby()

        assert (drain(scenario), page.clicks) == (
            False, ["#online", "#new-games", "#new-variant"])

    def test_a_pledge_drawn_late_is_answered_and_the_lobby_retried(self, profile):
        class LatePledgeLobby(ScreenPage):
            def __init__(self):
                super().__init__({"#online": ["Online"], "#pledge-ok": ["OK"],
                                  "#new-variant": ["Contree"]})
                self.probes = 0

            def locator(self, selector):
                answered = "#pledge-ok" in self.clicks
                if selector == "#pledge":
                    self.probes += 1
                    drawn = self.probes > 1 and not answered
                    return FakeLocator(self, selector, ["Fair play"] if drawn else [])
                if selector == "#new-games":
                    return FakeLocator(self, selector, ["New"] if answered else [])
                return super().locator(selector)

        page = LatePledgeLobby()

        async def scenario():
            return await Spectator(page, profile).enter_lobby()

        assert (drain(scenario), page.clicks) == (
            True, ["#online", "#pledge-ok", "#new-games", "#new-variant"])

    def test_a_blocked_lobby_menu_with_no_pledge_names_the_key(self, profile):
        page = ScreenPage({"#online": ["Online"]})

        async def scenario():
            await Spectator(page, profile).enter_lobby()

        with pytest.raises(BrowserError, match="mode_new_games"):
            drain(scenario)

    def test_the_hash_is_read_off_the_tournament_row(self, profile):
        tournament = Match(attrs={"data-key": "cfg-42"})
        page = ScreenPage({".slot-list": [Match(children={
            ":scope > .cup-row": [tournament]})]})

        async def scenario():
            return await Spectator(page, profile).read_tournament_hash()

        assert drain(scenario) == "cfg-42"

    def test_a_list_with_no_tournament_row_has_no_hash(self, profile):
        page = ScreenPage({".slot-list": [Match()]})

        async def scenario():
            return await Spectator(page, profile).read_tournament_hash()

        assert drain(scenario) is None

    def test_the_way_to_a_table_backs_out_until_observe_is_in_reach(self, profile):
        # Out of the list, out of the picker, then observe and the variant:
        # the round trip the chase probe timed at about three seconds.
        from contrai_scraper.browser import BACK_SETTLE_MS

        page = ScreenPage({"#observe": ["Watch"], "#variant": ["Contree"]},
                          screens=FOUR_SCREENS,
                          layout={**FOUR_SCREEN_BACKS, "#observe": ["online"]})

        async def scenario():
            await Spectator(page, profile).enter_table_from_lobby()

        drain(scenario)
        assert (page.nth_clicks, page.clicks, page.waited) == (
            [("#tool-close", 0), ("#tool-back", 1)],
            ["#observe", "#variant"],
            [BACK_SETTLE_MS, BACK_SETTLE_MS],
        )

    def test_the_way_back_takes_no_step_when_the_menu_already_shows(self, profile):
        # Where the site has already put an idle spectator back on the menu.
        page = ScreenPage({"#new-games": ["New"], "#new-variant": ["Contree"]},
                          screens=("mode", "online"),
                          layout={**FOUR_SCREEN_BACKS, "#new-games": ["online"]})

        async def scenario():
            await Spectator(page, profile).return_to_lobby()

        drain(scenario)
        assert (page.nth_clicks, page.clicks) == ([], ["#new-games", "#new-variant"])

    def test_the_way_back_gives_up_after_the_last_step(self, profile):
        # A route nobody has measured fails fast, so the caller can rebuild.
        deep = ("online", "a", "b", "c", "d", "e")
        page = ScreenPage({"#new-games": ["New"]}, screens=deep,
                          layout={"#tool-back": list(deep), "#new-games": ["online"]})

        async def scenario():
            await Spectator(page, profile).return_to_lobby()

        with pytest.raises(BrowserError, match="mode_new_games.*4 steps back"):
            drain(scenario)
        assert len(page.nth_clicks) == 4

    def test_a_profile_without_a_lobby_refuses_every_lobby_step(self, profile):
        from contrai_scraper import ProfileError

        bare = dataclasses.replace(
            profile,
            selectors=dataclasses.replace(
                profile.selectors, mode_new_games=None, lobby_variant=None,
                lobby_tables=None, lobby_back=None, lobby_layer=None,
                lobby_row_tournament_class=None, lobby_row_hash_attr=None,
            ),
        )
        spectator = Spectator(ScreenPage(), bare)
        steps = (spectator.enter_lobby, spectator.read_tournament_hash,
                 spectator.back_once, spectator.dismiss_overlay,
                 spectator.enter_table_from_lobby, spectator.return_to_lobby)
        refused = 0
        for step in steps:
            with pytest.raises(ProfileError, match="describes no lobby"):
                asyncio.run(step())
            refused += 1
        assert refused == len(steps)
