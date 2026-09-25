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
one. Nothing here browses. The lobby a fleet waits in is not a list of running
games either: it lists table *slots*, one of which is the tournament's, filling
and recycling for the next four players — so it says when a game starts and
who is in it, never where to find it.

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
from pathlib import Path
from typing import Any, Final

from contrai_core import Position

from .exceptions import BrowserError, ProfileError
from .frames import PlaywrightFrameSource
from .health import HealthLog
from .parse.translate import Translator
from .profile import LOBBY_SELECTOR_KEYS, Profile, Selector

#: How long one step of the walk waits for its element. The profile's
#: ``slow_mo_ms`` paces the run; this bounds a step that is simply not there.
STEP_TIMEOUT_MS: Final[int] = 10_000

#: How long the landing page is given before the walk looks at it. The
#: first-visit tutorial is probed rather than waited for, and a probe made the
#: moment navigation returns misses an offer the page draws a beat later —
#: which then covers the login entry. The flow that last logged in waited this
#: long.
PAGE_SETTLE_MS: Final[int] = 5_000

#: How many times a rail control is offered a click before it is a failure.
#:
#: Playwright already retries a click for its whole timeout, so a control that
#: is merely covered for a moment needs no help from us. What it cannot
#: recover from is the rails sliding away *while* it waits: the table draws
#: transient dialogs over its own controls, and a click blocked long enough
#: for the rails to leave is then waiting on a button that has gone
#: off-screen and will never come back on its own. Splitting the budget into
#: attempts is what lets the rails be revealed again between them.
PANEL_ATTEMPTS: Final[int] = 4

#: What one of those attempts waits. Derived, so the attempts together still
#: cost what a single step costs and a panel read cannot outlast the rest of
#: the walk.
PANEL_ATTEMPT_TIMEOUT_MS: Final[int] = STEP_TIMEOUT_MS // PANEL_ATTEMPTS

#: The attribute a class-membership test reads. Playwright's locator has no
#: class list of its own, so the attribute is read and split.
_CLASS_ATTR: Final[str] = "class"

#: How many back steps a walk takes looking for a control before giving up.
#: The page stacks four screens, so four steps reach the bottom of any of them.
LOBBY_BACK_STEPS: Final[int] = 4

#: How long a back step is given to land before the page is read again — the
#: pause the chase probe measured its round trips with.
BACK_SETTLE_MS: Final[int] = 600

#: Reads every control a list of candidates matches, with the geometry and the
#: *layer* style that decide whether a click on it could land. Read-only.
#:
#: The layer is the point. The page stacks its screens, each keeping its
#: controls in the DOM with a real box, so Playwright's ``:visible`` — which
#: asks about the element — cannot tell the screen being shown from the three
#: behind it. What is hidden is the screen, so its computed style is asked.
READ_CONTROLS: Final[str] = """
([selectors, layer]) => {
  const found = [];
  selectors.forEach((selector, rank) => {
    document.querySelectorAll(selector).forEach((el, index) => {
      const own = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      const host = el.closest(layer);
      const shown = host ? getComputedStyle(host) : null;
      found.push({
        rank, index,
        display: own.display, visibility: own.visibility, opacity: own.opacity,
        layerDisplay: shown ? shown.display : null,
        layerVisibility: shown ? shown.visibility : null,
        layerOpacity: shown ? shown.opacity : null,
        onScreen: box.width > 0 && box.height > 0 && box.right > 0 &&
                  box.left < innerWidth && box.bottom > 0 && box.top < innerHeight,
      });
    });
  });
  return found;
}
"""

#: What a click at the screen's centre would land on. Read-only.
READ_CENTRE: Final[str] = """
(list) => {
  const x = innerWidth / 2, y = innerHeight / 2;
  const at = document.elementFromPoint(x, y);
  return {x, y, found: Boolean(at), inList: Boolean(at && at.closest(list))};
}
"""

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
        try:
            await self._click("login_start")
        except BrowserError:
            # The tutorial can be drawn after the probe above looked for it —
            # a cold browser loads the landing page more slowly than the
            # settle allows for — and then covers the login entry. One
            # dismissal and one retry, the pattern the pledge has on the
            # menu; an entry blocked by anything else still fails, naming
            # its key.
            if await self._showing("dismiss_tutorial") is None:
                raise
            await self._click("dismiss_tutorial")
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

    async def enter_variant(self) -> bool:
        """Walk to the variant. The server chooses the table.

        Returns:
            Whether the first-use pledge was showing and answered on the way.
            Only this walk can tell: the dialog is drawn between the menu
            steps, so a probe made before them finds nothing.

        Raises:
            BrowserError: If a menu step is not there.
        """

        await self._click("mode_online")
        answered = await self.answer_pledge()
        try:
            await self._click("mode_observe")
        except BrowserError:
            # The pledge can be drawn a moment after the probe above looked
            # for it, and then covers this menu. One answer and one retry is
            # the pattern the browser-flow probe measured; a menu that is
            # blocked by anything else still fails, naming its key.
            if not await self.answer_pledge():
                raise
            answered = True
            await self._click("mode_observe")
        await self._click("variant")
        return answered

    async def next_table(self) -> None:
        """Ask the server for another table.

        The hop control sits on a rail, exactly like the panel buttons, so it
        is reached the same way. A recorder that hops promptly mostly finds
        the rails still out and escapes the difference; one that watches a
        table to its end does not.

        Raises:
            BrowserError: If the table control is not there.
        """

        await self._click_panel("next_table")

    # -- the lobby -------------------------------------------------------

    async def enter_lobby(self) -> bool:
        """Walk to the list of games, where a fleet waits for one to start.

        The route of :meth:`enter_variant` up to the online menu, then the
        action beside the observe one. The first-use pledge is met the same
        way and for the same reason: it is drawn between the menu steps, and
        can arrive a moment after it was looked for.

        Returns:
            Whether the pledge was showing and answered on the way.

        Raises:
            ProfileError: If the profile describes no lobby.
            BrowserError: If a menu step is not there.
        """

        self._require_lobby()
        await self._click("mode_online")
        answered = await self.answer_pledge()
        try:
            await self._click("mode_new_games")
        except BrowserError:
            # One answer and one retry, the pattern `enter_variant` carries on
            # the step beside this one.
            if not await self.answer_pledge():
                raise
            answered = True
            await self._click("mode_new_games")
        await self._click("lobby_variant")
        await self.dismiss_overlay()
        return answered

    async def read_tournament_hash(self) -> str | None:
        """The hash the tournament row's socket events are keyed by.

        The lobby's socket says everything about a row except which one is the
        tournament's, so that is read off the page once, on arrival, and the
        socket is followed from then on. The hash is a configuration's
        fingerprint, not a table's id: it outlives every game played at the
        slot, which is exactly what makes it worth keeping.

        Returns:
            The hash, or ``None`` when the list shows no tournament row.

        Raises:
            ProfileError: If the profile describes no lobby.
        """

        self._require_lobby()
        selectors = self._selectors
        row = (
            self._page.locator(selectors.lobby_tables)
            .locator(f":scope > .{selectors.lobby_row_tournament_class}")
            .first
        )
        if not await row.count():
            return None
        return await row.get_attribute(
            selectors.lobby_row_hash_attr, timeout=STEP_TIMEOUT_MS
        )

    async def back_once(self) -> None:
        """Back out of whichever screen is showing, choosing the control by layer.

        There is no single back control. Each stacked screen carries its own —
        the list's is one icon, the menus' another — and every screen keeps
        its controls in the DOM with a real box, so a selector filtered on
        visibility matches the ones behind as readily as the one in front.
        Guessing cost the chase probe three live runs, each dying on a control
        that was right for a screen other than the one shown. So every
        candidate is read with its screen's computed style, the ones a click
        could land on are kept, and the best-ranked of those is clicked.

        Raises:
            ProfileError: If the profile describes no lobby.
            BrowserError: If no candidate is on the screen shown, or the one
                chosen would not take the click. The message counts what was
                matched and never quotes a selector.
        """

        self._require_lobby()
        candidates = self._selectors.lobby_back
        controls = await self._controls("lobby_back", candidates)
        usable = [control for control in controls if _usable(control)]
        if not usable:
            raise BrowserError(
                f"[selectors].lobby_back matched {len(controls)} control(s), "
                "none of them on the screen shown"
            )
        chosen = min(usable, key=lambda control: (control["rank"], control["index"]))
        target = self._page.locator(candidates[chosen["rank"]]).nth(chosen["index"])
        try:
            await target.click(timeout=STEP_TIMEOUT_MS)
        except Exception as error:  # noqa: BLE001 - Playwright's timeout is its own type
            raise BrowserError(
                "[selectors].lobby_back chose a control that would not take a click"
            ) from error

    async def dismiss_overlay(self) -> bool:
        """Click the lobby's first-visit overlay away — once it is proven to be there.

        "Any click dismisses it" is true only while it is up. Without it, a
        click at the centre of the screen lands on a table slot and sits the
        account down: the one place in the whole walk where a wrong click
        changes something on the site rather than merely failing. So what is
        under the centre is read first, and the click is made only when that
        is not the list.

        Returns:
            Whether a click was made.

        Raises:
            ProfileError: If the profile describes no lobby.
        """

        self._require_lobby()
        centre = await self._page.evaluate(READ_CENTRE, self._selectors.lobby_tables)
        if not centre["found"] or centre["inList"]:
            return False
        await self._page.mouse.click(centre["x"], centre["y"])
        return True

    async def enter_table_from_lobby(self) -> None:
        """Leave the lobby for the observe branch, where the server seats us.

        Backs out of the list and its variant picker until the observe action
        is in reach, then takes it and the variant. Measured at about 3.1 s
        from the first step back to the first join snapshot, across every
        chase the probe ran.

        Raises:
            ProfileError: If the profile describes no lobby.
            BrowserError: If a step is not there.
        """

        self._require_lobby()
        await self._back_until("mode_observe")
        await self._click("variant")

    async def return_to_lobby(self) -> None:
        """Walk from wherever the page stands back to the lobby's list.

        Nothing has measured this route, so it is written to fail fast rather
        than to recover: back out until the new-games action is in reach, at
        most :data:`LOBBY_BACK_STEPS` times, then walk in again. A step that
        does not land raises, and the caller rebuilds the session — the one
        recovery that has been measured to work.

        Raises:
            ProfileError: If the profile describes no lobby.
            BrowserError: If the new-games action never comes within reach,
                or a step after it is not there.
        """

        self._require_lobby()
        await self._back_until("mode_new_games")
        await self._click("lobby_variant")
        await self.dismiss_overlay()

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

        await self._click_panel("options_button")
        observed: dict[str, bool] = {}
        for row in await self._rows("options_row"):
            # The id and the switch are two different children of the row, so
            # each is resolved inside it. A row lacking either — a group
            # heading, the objective selector — is not an option. Each is
            # pinned to ``.first``: a child selector that matches more than
            # once inside the row would otherwise trip Playwright's strict
            # mode and raise, ending the run rather than just this row.
            identity_node = row.locator(self._selectors.options_id_element).first
            state_node = row.locator(self._selectors.options_state_element).first
            if not await identity_node.count() or not await state_node.count():
                continue
            identity = await identity_node.get_attribute(
                self._selectors.options_id_attr, timeout=STEP_TIMEOUT_MS
            )
            if identity is None:
                # Keying a row on ``None`` would collide with the next such row.
                continue
            classes = (
                await state_node.get_attribute(_CLASS_ATTR, timeout=STEP_TIMEOUT_MS)
            ) or ""
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

        await self._click_panel("scoreboard_button")
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
        await self._click_panel(
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

    async def capture(self, stem: Path) -> tuple[Path, ...]:
        """Save what the page looked like, as ``<stem>.png`` and ``<stem>.html``.

        Written for the failure path. A browser step that fails names the
        profile key it was on and nothing else, which says *which* selector
        stopped matching but never *why* — and the why is routinely a thing
        no selector can express: a dialog drawn over the control, a rail that
        slid away, a font that did not load. Two files answer in one run what
        the health log cannot answer in two.

        Args:
            stem: The path to write beside, without a suffix.

        Returns:
            The files that were written, which may be neither: a diagnosis
            that fails must not replace the failure it was taken for, so
            every step here is swallowed rather than raised.
        """

        saved: list[Path] = []
        image = stem.with_suffix(".png")
        try:
            await self._page.screenshot(path=str(image))
        except Exception:  # noqa: BLE001 - a diagnosis never raises
            pass
        else:
            saved.append(image)
        document = stem.with_suffix(".html")
        try:
            document.write_text(await self._page.content(), encoding="utf-8")
        except Exception:  # noqa: BLE001 - a diagnosis never raises
            pass
        else:
            saved.append(document)
        return tuple(saved)

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

    async def _click_panel(self, key: str, selector: str | None = None) -> None:
        """Click a control on a rail, revealing the rails before each attempt.

        A panel control is not like the rest of the walk. It sits on a rail
        that slides away on its own, and the table draws transient dialogs
        over it — a modal host and a decorative layer were both measured
        covering the options button. Either one alone is harmless, because
        Playwright keeps retrying for its whole timeout. Together they are
        fatal: the click waits on the covered button, the rails leave under
        it mid-wait, and no amount of further waiting brings them back.

        So the wait is split, and the rails are revealed again at the top of
        each attempt. That is the whole fix — the reveal itself was never
        wrong, it was simply asked once, in front of a wait it could not
        reach into.

        Args:
            key: The profile key to name in a failure.
            selector: An already-resolved selector to use instead of the
                key's own, for the per-seat element whose placeholder is
                filled with a seat token.

        Raises:
            BrowserError: If no attempt landed, naming the key and never the
                selector.
        """

        candidates = (selector,) if selector is not None else self._candidates(key)
        for _ in range(PANEL_ATTEMPTS):
            await self._reveal_rails()
            for candidate in candidates:
                if await self._click_quietly(
                    candidate, timeout=PANEL_ATTEMPT_TIMEOUT_MS
                ):
                    return
        raise BrowserError(f"[selectors].{key} matched nothing that could be clicked")

    async def _click_quietly(
        self, selector: str, *, timeout: int = STEP_TIMEOUT_MS
    ) -> bool:
        """Try one selector.

        Args:
            selector: What to click.
            timeout: How long to let Playwright wait for it to be
                actionable.

        Returns:
            Whether the click landed. Playwright's click auto-waits, so a
            ``False`` here means the element never became actionable — which
            is the same answer for a missing element and a covered one.
        """

        try:
            await self._page.locator(selector).click(timeout=timeout)
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

    async def _reveal_rails(self) -> None:
        """Bring the table's panel controls back, if they have slid away.

        A table may collapse the rails holding its panel buttons after a few
        seconds of play. Those buttons stay in the DOM with a real box, simply
        translated outside the window, so a click on one waits out its whole
        timeout instead of landing — which ends the session rather than the
        read. The site's own toggle restores them and is shown only while they
        are away, so a selector filtered on visibility is both the question
        and the answer. A profile naming no toggle skips this.
        """

        if self._selectors.rail_show is None:
            return
        await self._dismiss("rail_show")

    def _require_lobby(self) -> None:
        """Refuse a lobby step on a profile that does not describe the lobby.

        Raises:
            ProfileError: Naming the keys a lobby needs.
        """

        if not self._selectors.has_lobby:
            raise ProfileError(
                "the profile describes no lobby; [selectors] needs "
                + ", ".join(LOBBY_SELECTOR_KEYS)
            )

    async def _controls(
        self, key: str, candidates: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Every control the candidates match, with what decides its reach.

        Raises:
            BrowserError: If the page's own script could not run the query —
                it takes plain CSS, and a selector written in Playwright's
                own syntax is the usual reason.
        """

        try:
            return await self._page.evaluate(
                READ_CONTROLS, [list(candidates), self._selectors.lobby_layer]
            )
        except Exception as error:  # noqa: BLE001 - a script error is Playwright's type
            raise BrowserError(
                f"[selectors].{key} could not be read by the page's own script; "
                "a lobby step needs plain CSS"
            ) from error

    async def _reachable(self, key: str) -> bool:
        """Whether one of a key's candidates is on the screen being shown."""

        controls = await self._controls(key, self._candidates(key))
        return any(_usable(control) for control in controls)

    async def _back_until(self, key: str) -> None:
        """Back out screen by screen until a control is in reach, then click it.

        Asked of the page before each step rather than tried with a short
        click: a control on a screen behind the one shown has a real box and
        would hold a click for its whole timeout, while its screen's style
        answers at once.

        Raises:
            BrowserError: If it is still out of reach after
                :data:`LOBBY_BACK_STEPS` steps, or a back step fails.
        """

        for step in range(LOBBY_BACK_STEPS + 1):
            if await self._reachable(key):
                await self._click(key)
                return
            if step < LOBBY_BACK_STEPS:
                await self.back_once()
                await self._page.wait_for_timeout(BACK_SETTLE_MS)
        raise BrowserError(
            f"[selectors].{key} was not in reach after {LOBBY_BACK_STEPS} steps back"
        )


def _usable(control: Mapping[str, Any]) -> bool:
    """Whether a click on one control could land, on the screen being shown.

    Args:
        control: One entry of :data:`READ_CONTROLS`' answer.

    Returns:
        Whether it is on the screen, displayed and visible itself, and sits on
        a layer that is displayed, visible and not faded out. A control with
        no layer is judged by its own style alone.
    """

    if not control["onScreen"]:
        return False
    if control["display"] == "none" or control["visibility"] == "hidden":
        return False
    if control["layerDisplay"] == "none" or control["layerVisibility"] == "hidden":
        return False
    return _opaque(control["opacity"]) and _opaque(control["layerOpacity"])


def _opaque(value: Any) -> bool:
    """Whether a computed opacity leaves an element showing.

    Missing or unreadable counts as showing: the question is whether a screen
    has been faded out, and a value nobody can read is not evidence that it
    has.
    """

    try:
        return float(1 if value is None else value) >= 0.1
    except (TypeError, ValueError):
        return True


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
async def open_browser(  # pragma: no cover - needs a real browser
    profile: Profile, *, headless: bool | None = None
) -> AsyncIterator[Any]:
    """Launch one Chromium that any number of sessions can share.

    A browser is the expensive half — a process, its renderers, most of the
    memory — and a context is the cheap half that holds a login. So a fleet
    launches one of these and opens a session per worker on it, and rebuilding
    one worker's session never costs the others theirs.

    Args:
        profile: The loaded profile; ``[browser]`` paces every session.
        headless: Override for ``[browser].headless``; ``None`` takes it.

    Yields:
        The Playwright browser, closed on the way out.
    """

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=profile.browser.headless if headless is None else headless,
            slow_mo=profile.browser.slow_mo_ms,
        )
        try:
            yield browser
        finally:
            await browser.close()


@asynccontextmanager
async def open_session(
    browser: Any, profile: Profile, *, health: HealthLog | None = None
) -> AsyncIterator[tuple[Spectator, PlaywrightFrameSource]]:
    """Open one isolated session on a shared browser, and yield its pair.

    A session is a browser *context*: its own cookies, so its own login, and
    its own sockets, so its own frames. Two sessions on one browser are two
    spectators as far as the site can tell, which is what lets one Chromium
    carry a whole fleet.

    The order is load-bearing. The script that keeps the page's sockets is
    added to the context before its page exists, so it runs on every document
    the page ever loads. And the frame source is built **before any
    navigation**: the first socket opens during the walk, and a source
    attached afterwards misses the join snapshot that is the only description
    of the table's opening state.

    Args:
        browser: A Playwright browser, or anything with ``new_context``.
        profile: The loaded profile — for a fleet worker, the site's profile
            with that worker's account in ``[account]``.
        health: The session's log, so socket opens and closes are counted.

    Yields:
        The spectator and the frames its page will produce. The context is
        closed on the way out, whatever ended the session.
    """

    context = await browser.new_context()
    try:
        await context.add_init_script(INIT_SCRIPT)
        page = await context.new_page()
        frames = PlaywrightFrameSource(page, profile.wire, health=health)
        yield Spectator(page, profile), frames
    finally:
        await context.close()


@asynccontextmanager
async def open_spectator(  # pragma: no cover - needs a real browser
    profile: Profile,
    *,
    headless: bool | None = None,
    health: HealthLog | None = None,
) -> AsyncIterator[tuple[Spectator, PlaywrightFrameSource]]:
    """Launch a browser and open one session on it: the single-worker path.

    Args:
        profile: The loaded profile.
        headless: Override for ``[browser].headless``; ``None`` takes it.
        health: The session's log, so socket opens and closes are counted.

    Yields:
        The spectator and the frames its page will produce.
    """

    async with open_browser(profile, headless=headless) as browser:
        async with open_session(browser, profile, health=health) as pair:
            yield pair
