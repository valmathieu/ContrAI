"""Pins the egress gate: the order of its checks, its refusals, what it logs."""

import asyncio
import dataclasses
import http.client
import json

import pytest

from contrai_scraper import EgressGate, EgressRefusal, route_device_from

HOME = "198.51.100.1"
EXIT = "203.0.113.7"
SITE = "https://example.invalid/lobby"


def build(profile, *, payload=None, fetch_error=None, device="tun0",
          route_error=None, tunnel="tun0"):
    """A gate over scripted I/O, and the list of calls it made, in order."""

    calls: list[str] = []

    def fetch(url):
        calls.append("fetch")
        if fetch_error is not None:
            raise fetch_error
        return {"addr": EXIT, "land": "XX"} if payload is None else payload

    def resolve(host):
        calls.append(f"resolve {host}")
        return "192.0.2.10"

    def route(address):
        calls.append(f"route {address}")
        if route_error is not None:
            raise route_error
        return device

    section = dataclasses.replace(profile.egress, tunnel_interface=tunnel)
    return EgressGate(section, SITE, fetch=fetch, resolve=resolve, route=route), calls


class TestOrder:
    def test_an_open_egress_passes_every_check(self, profile):
        gate, calls = build(profile)
        reading = gate.check_now()
        assert (reading.ok, reading.exit_ip, reading.route_device, calls) == (
            True, EXIT, "tun0",
            ["fetch", "resolve example.invalid", "route 192.0.2.10"],
        )

    def test_the_home_address_is_refused_before_the_site_is_resolved(self, profile):
        # A leaking setup must not even look the site's name up.
        gate, calls = build(profile, payload={"addr": HOME, "land": "XX"})
        reading = gate.check_now()
        assert (reading.refusal, calls) == (EgressRefusal.EXIT_IS_HOME, ["fetch"])

    def test_without_a_tunnel_interface_the_route_is_not_asked(self, profile):
        # Windows has no `ip route get`, so the laptop run skips that check
        # rather than failing a gate it cannot answer.
        gate, calls = build(profile, tunnel=None)
        reading = gate.check_now()
        assert (reading.ok, reading.route_device, calls) == (True, None, ["fetch"])


class TestRefusals:
    @pytest.mark.parametrize("error", [OSError("down"), ValueError("not json"),
                                       http.client.IncompleteRead(b"")])
    def test_a_probe_that_fails_refuses(self, profile, error):
        gate, _ = build(profile, fetch_error=error)
        assert gate.check_now().refusal is EgressRefusal.PROBE_FAILED

    def test_an_answer_without_an_address_refuses(self, profile):
        gate, _ = build(profile, payload={"land": "XX"})
        assert gate.check_now().refusal is EgressRefusal.PROBE_FAILED

    def test_an_answer_that_is_not_an_object_refuses(self, profile):
        gate, _ = build(profile, payload=[EXIT])
        assert gate.check_now().refusal is EgressRefusal.PROBE_FAILED

    def test_another_country_refuses(self, profile):
        gate, calls = build(profile, payload={"addr": EXIT, "land": "YY"})
        reading = gate.check_now()
        assert (reading.refusal, calls) == (EgressRefusal.WRONG_COUNTRY, ["fetch"])

    def test_the_country_is_compared_without_case(self, profile):
        gate, _ = build(profile, payload={"addr": EXIT, "land": "xx"})
        assert gate.check_now().ok is True

    def test_a_route_off_the_tunnel_refuses(self, profile):
        gate, _ = build(profile, device="eth0")
        reading = gate.check_now()
        assert (reading.refusal, reading.route_device) == (
            EgressRefusal.ROUTE_OFF_TUNNEL, "eth0"
        )

    def test_a_route_that_cannot_be_asked_refuses(self, profile):
        # A machine with no route table but a configured tunnel fails closed.
        gate, _ = build(profile, route_error=FileNotFoundError("ip"))
        assert gate.check_now().refusal is EgressRefusal.ROUTE_UNKNOWN


class TestLogging:
    def test_a_home_refusal_never_carries_the_address(self, profile):
        gate, _ = build(profile, payload={"addr": HOME, "land": "XX"})
        assert HOME not in json.dumps(gate.check_now().fields())

    def test_an_open_reading_names_exit_country_and_device(self, profile):
        gate, _ = build(profile)
        assert gate.check_now().fields() == {
            "exit_ip": EXIT, "country": "XX", "route_device": "tun0"
        }

    def test_a_refusal_names_its_reason(self, profile):
        gate, _ = build(profile, payload={"addr": EXIT, "land": "YY"})
        assert gate.check_now().fields()["reason"] == "wrong_country"


class TestRouteOutput:
    def test_the_device_is_read_from_the_route_table(self):
        output = '[{"dst":"192.0.2.10","dev":"tun0","prefsrc":"10.2.0.2"}]'
        assert route_device_from(output) == "tun0"

    @pytest.mark.parametrize("output", ["", "not json", "[]", "{}", '[{"dst":"192.0.2.10"}]',
                                        '[{"dev":""}]', '[{"dev":3}]'])
    def test_output_without_a_device_is_refused(self, output):
        with pytest.raises(ValueError):
            route_device_from(output)


class TestAsync:
    def test_the_check_runs_off_the_event_loop(self, profile):
        gate, _ = build(profile)
        assert asyncio.run(gate.check()).ok is True


class ScriptedGate:
    """A gate whose probe waits for a signal, answering from a script."""

    def __init__(self, *readings, hold=False):
        self.readings = list(readings)
        self.probes = 0
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def check(self):
        self.probes += 1
        await self.release.wait()
        return self.readings.pop(0)


def _reading(refusal=None):
    from contrai_scraper import EgressReading

    return EgressReading(refusal=refusal, exit_ip=None if refusal else EXIT,
                         country="XX", route_device=None)


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestShared:
    def test_ten_overlapping_asks_cost_one_probe(self):
        from contrai_scraper import SharedEgressGate

        inner = ScriptedGate(_reading(), hold=True)
        shared = SharedEgressGate(inner, max_age_s=60, monotonic=Clock())

        async def scenario():
            asks = [asyncio.ensure_future(shared.check()) for _ in range(10)]
            await asyncio.sleep(0)
            inner.release.set()
            return await asyncio.gather(*asks)

        readings = asyncio.run(scenario())
        assert (inner.probes, shared.probes, all(r.ok for r in readings)) == (1, 1, True)

    def test_asks_waiting_on_a_refusal_share_it_and_stay_refused(self):
        # Coalescing is not caching: every ask that was waiting on the refused
        # probe is refused by it, which is the answer about that instant.
        from contrai_scraper import SharedEgressGate

        inner = ScriptedGate(_reading(EgressRefusal.PROBE_FAILED), hold=True)
        shared = SharedEgressGate(inner, max_age_s=60, monotonic=Clock())

        async def scenario():
            asks = [asyncio.ensure_future(shared.check()) for _ in range(4)]
            await asyncio.sleep(0)
            inner.release.set()
            return await asyncio.gather(*asks)

        readings = asyncio.run(scenario())
        assert (inner.probes, {r.ok for r in readings}) == (1, {False})

    def test_a_passing_reading_answers_later_asks_within_the_window(self):
        from contrai_scraper import SharedEgressGate

        clock = Clock()
        inner = ScriptedGate(_reading(), _reading())
        shared = SharedEgressGate(inner, max_age_s=60, monotonic=clock)

        async def scenario():
            await shared.check()
            clock.now += 59
            return await shared.check()

        assert (asyncio.run(scenario()).ok, inner.probes) == (True, 1)

    def test_a_passing_reading_past_the_window_is_probed_again(self):
        from contrai_scraper import SharedEgressGate

        clock = Clock()
        inner = ScriptedGate(_reading(), _reading(EgressRefusal.WRONG_COUNTRY))
        shared = SharedEgressGate(inner, max_age_s=60, monotonic=clock)

        async def scenario():
            await shared.check()
            clock.now += 60
            return await shared.check()

        reading = asyncio.run(scenario())
        assert (reading.refusal, inner.probes) == (EgressRefusal.WRONG_COUNTRY, 2)

    def test_a_refusal_never_answers_a_later_ask(self):
        # Fail closed means a refusal is always re-asked, never remembered:
        # a tunnel that came back is let through by the very next probe.
        from contrai_scraper import SharedEgressGate

        clock = Clock()
        inner = ScriptedGate(_reading(EgressRefusal.PROBE_FAILED), _reading())
        shared = SharedEgressGate(inner, max_age_s=60, monotonic=clock)

        async def scenario():
            first = await shared.check()
            clock.now += 1
            return first, await shared.check()

        first, second = asyncio.run(scenario())
        assert (first.ok, second.ok, inner.probes) == (False, True, 2)
