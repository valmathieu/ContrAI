"""Whether traffic leaves through the tunnel — asked before the site is.

The confinement is structural: in the deployment the scraper has no network of
its own, only the VPN container's, whose firewall lets nothing out but the
tunnel. So this gate is not what keeps the home address off the site. It is
what makes a broken confinement *visible* — a mis-mounted network, a tunnel to
the wrong country, a laptop whose desktop VPN dropped — instead of merely
unlikely.

Three questions, cheapest-to-leak first. The echo service is asked for the
exit address before anything else, so a setup that is leaking is refused
before it has so much as resolved the site's name. Only then is the site's
address resolved and the route table asked which device it leaves through —
a question with no answer on a machine without ``ip``, which is why that
check is optional per profile rather than skipped silently.

The home address is compared and never repeated: a refusal because the exit
*is* home carries no address at all, so it cannot end up in a log line.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from .profile import EgressSection

#: Seconds an echo service gets to answer.
PROBE_TIMEOUT_S: Final[float] = 10.0

#: Seconds ``ip route get`` gets.
ROUTE_TIMEOUT_S: Final[float] = 5.0

#: What a probe may raise that means "no answer", rather than a bug.
_PROBE_ERRORS: Final = (OSError, ValueError, http.client.HTTPException)


class EgressRefusal(StrEnum):
    """Why an egress check refused."""

    PROBE_FAILED = "probe_failed"
    EXIT_IS_HOME = "exit_is_home"
    WRONG_COUNTRY = "wrong_country"
    ROUTE_UNKNOWN = "route_unknown"
    ROUTE_OFF_TUNNEL = "route_off_tunnel"


@dataclass(frozen=True, slots=True)
class EgressReading:
    """One egress check's answer."""

    refusal: EgressRefusal | None
    """Why it refused, or ``None`` when nothing did."""

    exit_ip: str | None
    """The observed exit address; ``None`` when it was the home address."""

    country: str | None
    """The country the echo service reported, when it reported one."""

    route_device: str | None
    """The device the site's route leaves through, when it was asked."""

    @property
    def ok(self) -> bool:
        """Whether traffic may go to the site."""

        return self.refusal is None

    def fields(self) -> dict[str, str | None]:
        """The reading as health-log fields. Never includes the home address.

        Returns:
            The exit address, country and device, plus a reason when refused.
        """

        payload: dict[str, str | None] = {
            "exit_ip": self.exit_ip,
            "country": self.country,
            "route_device": self.route_device,
        }
        if self.refusal is not None:
            payload["reason"] = str(self.refusal)
        return payload


class EgressGate:
    """Asks, in order: what is my exit, is it home, where is it, which device."""

    __slots__ = ("_section", "_host", "_fetch", "_resolve", "_route")

    def __init__(
        self,
        section: EgressSection,
        site_url: str,
        *,
        fetch: Callable[[str], Any] | None = None,
        resolve: Callable[[str], str] | None = None,
        route: Callable[[str], str] | None = None,
    ) -> None:
        """Bind the gate to a profile's section and the site it guards.

        Args:
            section: The profile's ``[egress]``.
            site_url: ``[site].url``; only its host name is used.
            fetch: Reads the echo service's JSON. Injected in tests.
            resolve: Host name to an IPv4 address. Injected in tests.
            route: Address to the device its route leaves through.
        """

        self._section = section
        self._host = urlsplit(site_url).hostname or ""
        self._fetch = fetch if fetch is not None else fetch_json
        self._resolve = resolve if resolve is not None else resolve_ipv4
        self._route = route if route is not None else route_device

    async def check(self) -> EgressReading:
        """:meth:`check_now` on a worker thread, so the event loop keeps turning.

        Returns:
            The reading.
        """

        return await asyncio.to_thread(self.check_now)

    def check_now(self) -> EgressReading:
        """Run the three checks, stopping at the first refusal.

        Returns:
            The reading.
        """

        section = self._section
        try:
            payload = self._fetch(section.probe_url)
        except _PROBE_ERRORS:
            return EgressReading(EgressRefusal.PROBE_FAILED, None, None, None)

        exit_ip = _text(payload, section.probe_ip_field)
        country = _text(payload, section.probe_country_field)
        if exit_ip is None:
            return EgressReading(EgressRefusal.PROBE_FAILED, None, country, None)
        if exit_ip == section.home_ip:
            # Deliberately without the address: this reading is logged, and
            # the home address is the one value that must never reach a line.
            return EgressReading(EgressRefusal.EXIT_IS_HOME, None, country, None)
        if (country or "").casefold() != section.expected_country.casefold():
            return EgressReading(EgressRefusal.WRONG_COUNTRY, exit_ip, country, None)
        if section.tunnel_interface is None:
            return EgressReading(None, exit_ip, country, None)

        try:
            device = self._route(self._resolve(self._host))
        except (OSError, ValueError, subprocess.SubprocessError):
            # A configured tunnel whose route cannot be read fails closed: not
            # knowing where traffic leaves is not a reason to send any.
            return EgressReading(EgressRefusal.ROUTE_UNKNOWN, exit_ip, country, None)
        if device != section.tunnel_interface:
            return EgressReading(EgressRefusal.ROUTE_OFF_TUNNEL, exit_ip, country, device)
        return EgressReading(None, exit_ip, country, device)


class SharedEgressGate:
    """One egress gate answering a whole fleet, one probe at a time.

    A recorder asks before every hop, and a chase is mostly hops: ten workers
    scanning at once would send the echo service fifty requests in twenty
    seconds, all from the one exit address, which is how a free-tier service
    starts answering ``429`` — and a gate reads that as a refusal. Two rules
    bring the cost down to one probe without changing what an answer means.

    **Asks that overlap share a probe.** A caller that arrives while a probe
    is in flight waits for it and takes its answer, good or bad, instead of
    queueing a second one behind it: the answer is about the same instant.

    **A good answer is reused for a while; a refusal never is.** Within
    ``max_age_s`` of a probe that passed, a fresh ask gets that reading back.
    A refusal only ever answers the asks that were waiting on it, so the next
    caller probes again — fail-closed is untouched, since what is cached is
    only ever evidence that traffic *was* leaving through the tunnel.

    The window is also what bounds a missed outage, and it is short next to
    what it guards: a table is written ``abandoned`` only after
    ``stale_after_s`` of silence, far longer than any reading kept here, so a
    tunnel that died before the table went quiet has no good reading left to
    vouch for it.
    """

    __slots__ = ("_gate", "_max_age_s", "_monotonic", "_lock", "_good", "_good_at",
                 "_last", "_last_at", "probes")

    def __init__(
        self,
        gate: Any,
        *,
        max_age_s: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap a gate.

        Args:
            gate: Anything with ``async check() -> EgressReading`` — an
                :class:`EgressGate` outside tests.
            max_age_s: How long a passing reading answers later asks.
            monotonic: The clock ages are measured on.
        """

        self._gate = gate
        self._max_age_s = max_age_s
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._good: EgressReading | None = None
        self._good_at = 0.0
        self._last: EgressReading | None = None
        self._last_at = 0.0
        self.probes = 0
        """How many probes actually went out, for the health log."""

    async def check(self) -> EgressReading:
        """The egress reading, probed only when nothing current can answer.

        Returns:
            A probe's reading: the one this ask was waiting on, a recent
            passing one, or a new one.
        """

        asked = self._monotonic()
        async with self._lock:
            if self._last is not None and self._last_at >= asked:
                # Finished while this ask was queued behind it.
                return self._last
            now = self._monotonic()
            if self._good is not None and now - self._good_at < self._max_age_s:
                return self._good
            reading = await self._gate.check()
            self.probes += 1
            self._last, self._last_at = reading, self._monotonic()
            if reading.ok:
                self._good, self._good_at = reading, self._last_at
            return reading


def _text(payload: Any, field: str) -> str | None:
    """One non-empty string field of a JSON object, or ``None``."""

    value = payload.get(field) if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None


def route_device_from(output: str) -> str:
    """The device ``ip -json route get`` names.

    Args:
        output: The command's stdout.

    Returns:
        The device name.

    Raises:
        ValueError: The output is not a route list naming a device.
    """

    try:
        routes = json.loads(output)
        device = routes[0]["dev"]
    except (json.JSONDecodeError, LookupError, TypeError) as error:
        raise ValueError("the route table named no device") from error
    if not isinstance(device, str) or not device:
        raise ValueError("the route table named no device")
    return device


def fetch_json(url: str) -> Any:  # pragma: no cover - real network
    """GET a JSON document."""

    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "contrai-scraper"}
    )
    with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:
        return json.load(response)


def resolve_ipv4(host: str) -> str:  # pragma: no cover - real DNS
    """The first IPv4 address a host name resolves to."""

    return socket.getaddrinfo(
        host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
    )[0][4][0]


def route_device(address: str) -> str:  # pragma: no cover - needs `ip`
    """The device the kernel would send ``address`` through, policy rules included."""

    result = subprocess.run(
        ["ip", "-json", "route", "get", address],
        capture_output=True,
        text=True,
        check=True,
        timeout=ROUTE_TIMEOUT_S,
    )
    return route_device_from(result.stdout)
