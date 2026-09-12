"""Every Playwright call the scraper makes, and no others.

This is the only module that touches a page. That is not tidiness: the
decisions the browser half makes — is this dialog showing, does this table
play our ruleset, is this score panel saying what the wire said — are the ones
a site change breaks, and each of them is a few lines of logic wrapped around
a lot of I/O. Keeping the I/O in one class lets the logic be driven by a
hand-written fake page, so the tests exercise the reasoning without the site.

Two facts from the live measurement are built into the surface rather than
documented beside it.

**There is no table list.** The server decides where a spectator sits, so the
walk ends at the variant and the only way to see another table is to ask for
one. Nothing here browses.

**There is no way back in.** The site's exit control leaves *spectating*
rather than the table, and the documented route back is what breaks
afterwards — three attempts, zero recoveries. So there is no ``leave``
operation at all: a session that cannot go on rebuilds its browser context.

Nothing in this module spells the site. Every selector, every URL and every
token is read from the profile, and an error names the **profile key** that
failed rather than the string behind it — which is both the anonymisation
rule and the only thing that makes a timeout diagnosable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from contrai_core import Position

from .exceptions import BrowserError
from .frames import PlaywrightFrameSource
from .parse.translate import Translator
from .profile import Profile, Selector

#: How long one step of the walk waits for its element. The profile's
#: ``slow_mo_ms`` paces the run; this bounds a step that is simply not there.
STEP_TIMEOUT_MS: Final[int] = 10_000

#: How long the landing page is given before the walk looks at it. The
#: first-visit tutorial is probed rather than waited for, and a probe made the
#: moment navigation returns misses an offer the page draws a beat later —
#: which then covers the login entry. The flow that last logged in waited this
#: long.
PAGE_SETTLE_MS: Final[int] = 5_000

#: The attribute a class-membership test reads. Playwright's locator has no
#: class list of its own, so the attribute is read and split.
_CLASS_ATTR: Final[str] = "class"

#: Wraps the page's WebSocket constructor so a frame can be sent on a socket
#: the page owns. Playwright cannot send on one; this keeps the instances.
#: Site-free by construction: it names no URL and inspects no payload.
INIT_SCRIPT: Final[str] = """
(() => {
  const Native = window.WebSocket;
  const live = [];
  window.__contrai_sockets = live;
  window.WebSocket = function (...args) {
    const socket = new Native(...args);
    live.push(socket);
    return socket;
  };
  window.WebSocket.prototype = Native.prototype;
  Object.assign(window.WebSocket, Native);
})();
"""

#: Sends one envelope on the newest open socket. Returns whether it went.
SEND_SCRIPT: Final[str] = """
(envelope) => {
  const live = (window.__contrai_sockets || []).filter(s => s.readyState === 1);
  if (!live.length) return false;
  live[live.length - 1].send(JSON.stringify(envelope));
  return true;
}
"""


@dataclass(frozen=True, slots=True)
class OptionsReading:
    """What the table's options panel said, against what was expected."""

    observed: Mapping[str, bool]
    missing: tuple[str, ...]
    """Expected ids the panel did not show."""

    extra: tuple[str, ...]
    differing: tuple[str, ...]

    @property
    def matches(self) -> bool:
        """Whether the table plays the expected ruleset.

        All three differences count, ``extra`` included. An option the
        profile has never seen may well change the rules, and a record
        naming the expected preset would then be wrong about them in a way
        no later check could see — so an unknown id refuses the table and
        says so, which is how the operator learns the site has changed.
        """

        return not (self.missing or self.extra or self.differing)


@dataclass(frozen=True, slots=True)
class ScoreboardReading:
    """The scoreboard panel, spectator-relative."""

    rows: tuple[tuple[int, int], ...]
    """``(us, them)`` per scored round, oldest first. ``us`` is the south
    seat's side — asserted at seating, never assumed per read."""

    text: str
    """The rows as rendered, for the raw log.

    Kept verbatim and joined by newlines, so a row this reader declined to
    parse is still recoverable from the evidence rather than lost.
    """


class Spectator:
    """Every Playwright call the scraper makes, and no others.

    There is deliberately no ``leave`` operation. The site's exit control
    leaves *spectating* rather than the table, and the documented way back in
    is what breaks afterwards — three attempts, zero recoveries. A session
    that cannot reseat must rebuild its browser context, not retry.
    """

    __slots__ = ("_page", "_profile", "_selectors", "_translator", "_requests")

    def __init__(self, page: Any, profile: Profile) -> None:
        """Bind to one page.

        Args:
            page: A Playwright page, or anything with the same surface.
            profile: The loaded profile, which owns every string used here.

        Raises:
            ProfileError: If the profile's seat map does not preserve the
                table's rotation.
        """

        self._page = page
        self._profile = profile
        self._selectors = profile.selectors
        self._translator = Translator(profile)
        self._requests = 0

    async def log_in(self) -> None:
        """Navigate to the lobby and sign in with the spectator account.

        Raises:
            BrowserError: If a step of the login form is not there.
        """

        await self._page.goto(self._profile.site.url)
        await self._page.wait_for_timeout(PAGE_SETTLE_MS)
        await self._dismiss("dismiss_tutorial")
        # The site offers several ways in, and the address form appears only
        # once its e-mail entry is chosen.
        await self._click("login_start")
        await self._fill("login_email", self._profile.account.email)
        await self._click("login_continue")
        await self._fill("code_input", self._profile.account.verification_code)
        await self._click("code_submit")

    async def answer_pledge(self) -> bool:
        """Answer the first-use moderation dialog if it is showing.

        Returns:
            Whether a dialog was there to answer. It appears once per account,
            between the menu steps, and blocks the spectator menu until
            answered — which is why it is probed rather than waited for.

        Raises:
            BrowserError: If the dialog is showing and its accept control is
                not.
        """

        if await self._showing("pledge_dialog") is None:
            return False
        await self._click("pledge_accept")
        return True

    async def enter_variant(self) -> None:
        """Walk to the variant. The server chooses the table.

        Raises:
            BrowserError: If a menu step is not there.
        """

        await self._click("mode_online")
        await self.answer_pledge()
        try:
            await self._click("mode_observe")
        except BrowserError:
            # The pledge can be drawn a moment after the probe above looked
            # for it, and then covers this menu. One answer and one retry is
            # the pattern the browser-flow probe measured; a menu that is
            # blocked by anything else still fails, naming its key.
            if not await self.answer_pledge():
                raise
            await self._click("mode_observe")
        await self._click("variant")

    async def next_table(self) -> None:
        """Ask the server for another table.

        Raises:
            BrowserError: If the table control is not there.
        """

        await self._click("next_table")

    async def read_tournament_marker(self) -> bool:
        """Whether the rendered marker names a tournament.

        A cross-check only: the recorder's gate reads the join snapshot,
        which agreed with this on 23 of 23 table visits including 7 negatives.

        Returns:
            Whether the marker is showing and says what a tournament says. An
            absent marker is a plain table, not an error.
        """

        marker = await self._showing("tournament_marker")
        if marker is None:
            return False
        text = await marker.inner_text(timeout=STEP_TIMEOUT_MS)
        return self._selectors.tournament_marker_text.casefold() in text.casefold()

    async def read_options(self, expected: Mapping[str, bool]) -> OptionsReading:
        """Open the options panel, read every option row's id and switch, and diff it.

        Args:
            expected: Option id to whether it should be switched on, as
                ``[rules.options]`` states it.

        Returns:
            What the panel showed and how it differed.

        Raises:
            BrowserError: If the options control is not there.
        """

        await self._click("options_button")
        observed: dict[str, bool] = {}
        for row in await self._rows("options_row"):
            # The id and the switch are two different children of the row, so
            # each is resolved inside it. A row lacking either — a group
            # heading, the objective selector — is not an option.
            identity_node = row.locator(self._selectors.options_id_element)
            state_node = row.locator(self._selectors.options_state_element)
            if not await identity_node.count() or not await state_node.count():
                continue
            identity = await identity_node.get_attribute(self._selectors.options_id_attr)
            if identity is None:
                # Keying a row on ``None`` would collide with the next such row.
                continue
            classes = (await state_node.get_attribute(_CLASS_ATTR)) or ""
            observed[identity] = self._selectors.options_on_class in classes.split()
        await self._close_panel()

        return OptionsReading(
            observed=observed,
            missing=tuple(sorted(set(expected) - set(observed))),
            extra=tuple(sorted(set(observed) - set(expected))),
            differing=tuple(
                sorted(
                    name
                    for name, value in expected.items()
                    if name in observed and observed[name] != value
                )
            ),
        )

    async def read_scoreboard(self) -> ScoreboardReading:
        """Open the score panel and read every scored round off it.

        Returns:
            The rows, oldest first, and the text they were read from. A panel
            with no rows is a correct answer — it is what a game with no
            scored round looks like — and never an error.

        Raises:
            BrowserError: If the scoreboard control is not there.
        """

        await self._click("scoreboard_button")
        rows: list[tuple[int, int]] = []
        lines: list[str] = []
        for row in await self._rows("scoreboard_row"):
            lines.append(await row.inner_text(timeout=STEP_TIMEOUT_MS))
            pair = await self._pair(row)
            if pair is not None:
                rows.append(pair)
        await self._close_panel()
        return ScoreboardReading(rows=tuple(rows), text="\n".join(lines))

    async def read_player_id(self, seat: Position) -> str | None:
        """The seat's panel id, or ``None`` when the text holds no number.

        Args:
            seat: Which seat to open.

        Returns:
            The id as the panel spells it, digits only.

        Raises:
            BrowserError: If the seat element or the panel's title is not
                there.
        """

        token = self._translator.seat_name(seat)
        await self._click_one(
            "seat_element", self._selectors.seat_element.format(seat=token)
        )
        title = await self._require("player_id_title")
        text = await title.inner_text(timeout=STEP_TIMEOUT_MS)
        await self._close_panel()
        return _number_in(text, self._selectors.player_id_prefix)

    async def request_state(self, table_id: str, last_event_id: str) -> bool:
        """Ask for a fresh state snapshot without leaving the table.

        The parameter naming the last frame seen is what makes this work: the
        bare re-send is acknowledged and ignored, 0 snapshots in 4 attempts.

        Args:
            table_id: The table's own id, as the join snapshot gave it.
            last_event_id: The id of the newest frame seen on the socket.

        Returns:
            Whether the request was sent. The answer arrives on the socket as
            a join snapshot, so the caller reads it off the frame source.
        """

        wire = self._profile.wire
        self._requests += 1
        envelope = {
            "id": f"contrai-{self._requests}",
            "action": wire.resume_action,
            "data": {
                "room": f"{wire.resume_room_prefix}{table_id}",
                "params": {wire.resume_param: last_event_id},
            },
        }
        return bool(await self._page.evaluate(SEND_SCRIPT, envelope))

    # -- the page, behind the profile ------------------------------------

    def _candidates(self, key: str) -> tuple[str, ...]:
        """The selectors one profile key offers, in the order to try them."""

        value: Selector = getattr(self._selectors, key)
        return (value,) if isinstance(value, str) else value

    async def _click(self, key: str) -> None:
        """Click the first candidate that is actionable.

        Raises:
            BrowserError: If none of them is, naming the key and never the
                selector.
        """

        for candidate in self._candidates(key):
            if await self._click_quietly(candidate):
                return
        raise BrowserError(f"[selectors].{key} matched nothing that could be clicked")

    async def _click_one(self, key: str, selector: str) -> None:
        """Click one already-resolved selector, reporting it as ``key``.

        Used where the selector is built rather than read — the per-seat
        element, whose placeholder is filled with a seat token.

        Raises:
            BrowserError: If it is not actionable.
        """

        if not await self._click_quietly(selector):
            raise BrowserError(
                f"[selectors].{key} matched nothing that could be clicked"
            )

    async def _click_quietly(self, selector: str) -> bool:
        """Try one selector.

        Returns:
            Whether the click landed. Playwright's click auto-waits, so a
            ``False`` here means the element never became actionable — which
            is the same answer for a missing element and a covered one.
        """

        try:
            await self._page.locator(selector).click(timeout=STEP_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - Playwright's timeout is its own type
            return False
        return True

    async def _fill(self, key: str, value: str) -> None:
        """Type into the first candidate that accepts it.

        Raises:
            BrowserError: If none of them does.
        """

        for candidate in self._candidates(key):
            try:
                await self._page.fill(candidate, value, timeout=STEP_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - Playwright's timeout is its own type
                continue
            return
        raise BrowserError(f"[selectors].{key} matched no field to type into")

    async def _showing(self, key: str) -> Any | None:
        """The first candidate that resolves to something, or ``None``.

        A probe, not a wait: it answers "is this on screen now". Used where
        absence is an ordinary answer — the pledge that appears once per
        account, the marker a plain table does not carry.
        """

        for candidate in self._candidates(key):
            locator = self._page.locator(candidate)
            if await locator.count():
                return locator
        return None

    async def _require(self, key: str) -> Any:
        """The first candidate that resolves to something.

        Raises:
            BrowserError: If none does.
        """

        locator = await self._showing(key)
        if locator is None:
            raise BrowserError(f"[selectors].{key} matched nothing on the page")
        return locator

    async def _rows(self, key: str) -> list[Any]:
        """Every element one key resolves to, or none at all.

        A panel with no rows is a reading, not a failure, so this never
        raises: the caller decides what an empty panel means.
        """

        locator = await self._showing(key)
        return [] if locator is None else await locator.all()

    async def _pair(self, row: Any) -> tuple[int, int] | None:
        """The first two cells of one scoreboard row, as numbers.

        Returns:
            ``(us, them)``, or ``None`` when the row is not a pair of numbers
            — a header row is the usual case, and skipping it is safer than
            reading a column title as a score.
        """

        cells = await row.locator(self._one("scoreboard_cell")).all()
        if len(cells) < 2:
            return None
        try:
            return (
                int((await cells[0].inner_text(timeout=STEP_TIMEOUT_MS)).strip()),
                int((await cells[1].inner_text(timeout=STEP_TIMEOUT_MS)).strip()),
            )
        except ValueError:
            return None

    def _one(self, key: str) -> str:
        """The first selector a key offers, for a scoped lookup."""

        return self._candidates(key)[0]

    async def _close_panel(self) -> None:
        """Close whichever panel is open.

        Raises:
            BrowserError: If the close control is not there. A panel left
                open covers the next step, so this is a failure and not a
                tidy-up.
        """

        await self._click("panel_close")

    async def _dismiss(self, key: str) -> None:
        """Click something if it is showing, and shrug if it is not."""

        if await self._showing(key) is not None:
            await self._click(key)


def _number_in(text: str, prefix: str) -> str | None:
    """The digit run a panel title carries, after its prefix.

    Args:
        text: The title as rendered.
        prefix: What the profile says precedes the number.

    Returns:
        The digits, or ``None`` when there are none — a guest seat carries a
        label and no account, and the label is not an identity.
    """

    stripped = text.strip()
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix):]
    digits = "".join(character for character in stripped if character.isdigit())
    return digits or None


@asynccontextmanager
async def open_spectator(  # pragma: no cover - needs a real browser
    profile: Profile, *, headless: bool | None = None
) -> AsyncIterator[tuple[Spectator, PlaywrightFrameSource]]:
    """Launch a browser, attach a frame source, yield the pair.

    The frame source is built **before any navigation**: the first socket
    opens during the walk, and a source attached afterwards misses the join
    snapshot that is the only description of the table's opening state.

    Args:
        profile: The loaded profile.
        headless: Override for ``[browser].headless``; ``None`` takes it.

    Yields:
        The spectator and the frames its page will produce.
    """

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=profile.browser.headless if headless is None else headless,
            slow_mo=profile.browser.slow_mo_ms,
        )
        try:
            page = await browser.new_page()
            frames = PlaywrightFrameSource(page, profile.wire)
            await page.add_init_script(INIT_SCRIPT)
            yield Spectator(page, profile), frames
        finally:
            await browser.close()
