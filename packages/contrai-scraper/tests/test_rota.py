"""Pins the rota: at most N watchers, M spares, everyone else logged out."""

import asyncio
import json

from contrai_scraper import HealthLog, Role, Rota


class Clock:
    """A clock moved by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def rota(*, watchers=2, spares=1, stagger_s=0.0, stopping=lambda: False, clock=None,
         bootstrap=False):
    """A rota writing to a list, and the list."""

    lines: list[str] = []
    made = Rota(watchers=watchers, spares=spares, stagger_s=stagger_s,
                monotonic=clock if clock is not None else Clock(), stopping=stopping,
                health=HealthLog(write=lines.append), bootstrap=bootstrap)
    return made, lines


def labels(count):
    return [f"bot{index:02}" for index in range(1, count + 1)]


def roles(made, count):
    return [made.role(label).value for label in labels(count)]


def changes(lines):
    return [(entry["worker"], entry["was"], entry["now"], entry["why"])
            for entry in map(json.loads, lines) if entry["event"] == "role"]


class TestOpening:
    def test_two_watchers_and_a_spare_the_rest_logged_out(self):
        made, lines = rota()
        made.open(labels(7))
        assert roles(made, 7) == ["hall", "hall", "spare", "out", "out", "out", "out"]
        assert changes(lines) == [("bot01", "out", "hall", "watcher_wanted"),
                                  ("bot02", "out", "hall", "watcher_wanted"),
                                  ("bot03", "out", "spare", "spare_wanted")]

    def test_a_fleet_of_one_has_one_watcher_and_no_spare(self):
        made, _ = rota()
        made.open(labels(1))
        assert roles(made, 1) == ["hall"]

    def test_a_fleet_of_two_is_all_watchers(self):
        made, _ = rota()
        made.open(labels(2))
        assert roles(made, 2) == ["hall", "hall"]

    def test_the_window_opens_on_its_opening_wave(self):
        made, _ = rota()
        made.open(labels(3))
        opening = made.opening
        made.chasing("bot01")
        assert (opening, made.opening) == (True, False)

    def test_a_stopping_fleet_wakes_nobody(self):
        made, lines = rota(stopping=lambda: True)
        made.open(labels(3))
        assert (roles(made, 3), lines) == (["out", "out", "out"], [])


class TestChases:
    def test_a_chase_sends_the_spare_in_and_wakes_the_next_as_spare(self):
        made, lines = rota()
        made.open(labels(5))
        made.chasing("bot01")
        assert roles(made, 5) == ["chase", "hall", "hall", "spare", "out"]
        assert changes(lines)[-3:] == [("bot01", "hall", "chase", "chase"),
                                       ("bot03", "spare", "hall", "promoted"),
                                       ("bot04", "out", "spare", "spare_wanted")]

    def test_a_chaser_back_to_a_full_lobby_and_a_spare_is_parked(self):
        made, _ = rota()
        made.open(labels(4))
        made.chasing("bot01")
        assert (made.done("bot01"), roles(made, 4)) == (
            Role.OUT, ["out", "hall", "hall", "spare"])

    def test_a_chaser_whose_spare_was_not_replaced_comes_back_as_the_spare(self):
        # Three workers: the spare walked in and nobody was queued behind it.
        made, _ = rota()
        made.open(labels(3))
        made.chasing("bot01")
        assert (made.done("bot01"), roles(made, 3)) == (
            Role.SPARE, ["spare", "hall", "hall"])

    def test_a_chaser_goes_back_to_the_lobby_before_anyone_logs_in(self):
        made, _ = rota()
        made.open(labels(2))
        made.chasing("bot01")
        assert (made.done("bot01"), roles(made, 2)) == (Role.HALL, ["hall", "hall"])

    def test_two_starts_in_a_row_promote_the_spare_still_logging_in(self):
        made, _ = rota()
        made.open(labels(5))
        made.chasing("bot01")
        made.chasing("bot02")
        assert roles(made, 5) == ["chase", "chase", "hall", "hall", "spare"]

    def test_with_nobody_left_to_send_the_lobby_waits_short(self):
        made, _ = rota(watchers=1, spares=0)
        made.open(labels(1))
        made.chasing("bot01")
        assert roles(made, 1) == ["chase"]


class TestQueue:
    def test_a_worker_logged_out_asks_and_waits_its_turn(self):
        made, _ = rota()
        made.open(labels(4))
        made.vacate("bot02", "session_failed")
        # The spare takes bot02's place and bot04, queued at the opening, the
        # spare's; bot02 joins the back of the queue by asking again.
        asked = made.ask("bot02")
        assert (asked, roles(made, 4)) == (Role.OUT, ["hall", "out", "hall", "spare"])

    def test_asking_twice_queues_once(self):
        made, _ = rota()
        made.open(labels(5))
        made.ask("bot04")
        made.ask("bot04")
        made.chasing("bot01")
        made.chasing("bot02")
        # bot04 then bot05, each once: had bot04 been queued twice, bot05
        # would still be out.
        assert roles(made, 5) == ["chase", "chase", "hall", "hall", "spare"]

    def test_a_worker_with_a_role_is_answered_its_role(self):
        made, _ = rota()
        made.open(labels(3))
        assert made.ask("bot03") is Role.SPARE

    def test_a_vacated_place_goes_to_the_spare_and_the_queue(self):
        made, lines = rota()
        made.open(labels(4))
        made.vacate("bot01", "egress_blocked")
        assert roles(made, 4) == ["out", "hall", "hall", "spare"]
        assert changes(lines)[3] == ("bot01", "hall", "out", "egress_blocked")

    def test_a_queued_worker_that_leaves_leaves_the_queue(self):
        made, _ = rota()
        made.open(labels(4))
        made.vacate("bot04", "stopped")
        made.chasing("bot01")
        assert roles(made, 4) == ["chase", "hall", "hall", "out"]


class TestDown:
    def test_a_worker_down_is_dropped_and_the_numbers_clamped_again(self):
        made, lines = rota()
        made.open(labels(3))
        made.drop("bot01")
        # Two left: both watch, and the spare's place is gone with the third.
        assert ([made.role(label).value for label in ("bot02", "bot03")],
                changes(lines)[-2:]) == (
            ["hall", "hall"],
            [("bot01", "hall", "out", "down"), ("bot03", "spare", "hall", "promoted")])

    def test_a_queued_worker_can_go_down(self):
        made, _ = rota()
        made.open(labels(4))
        made.drop("bot04")
        assert made.counts()["out"] == 0

    def test_a_worker_already_down_is_let_be(self):
        made, lines = rota()
        made.drop("bot01")
        made.vacate("bot01", "stopped")
        assert lines == []


class TestLogins:
    def test_logins_take_turns_a_stagger_apart(self):
        clock = Clock()
        made, _ = rota(stagger_s=20.0, clock=clock)
        made.open(labels(3))
        assert [made.login_delay() for _ in range(3)] == [0.0, 20.0, 40.0]

    def test_a_login_long_after_the_last_waits_for_nothing(self):
        clock = Clock()
        made, _ = rota(stagger_s=20.0, clock=clock)
        made.open(labels(3))
        made.login_delay()
        clock.now = 300.0
        assert made.login_delay() == 0.0

    def test_the_counts_add_up_the_roles_and_the_logins(self):
        made, _ = rota()
        made.open(labels(5))
        made.logged_in()
        made.chasing("bot01")
        assert made.counts() == {"out": 1, "hall": 2, "spare": 1, "chase": 1, "boot": 0,
                                 "logins": 1}


def phase(lines):
    """The startup phase's own lines, as ``(event, reason, sent)``."""

    return [(entry["event"], entry.get("reason"), entry.get("sent"))
            for entry in map(json.loads, lines)
            if entry["event"] in ("bootstrap_started", "bootstrap_done")]


class TestStartup:
    def test_one_watcher_then_one_startup_worker_at_a_time(self):
        made, lines = rota(bootstrap=True)
        made.open(labels(7))
        opened = roles(made, 7)
        made.seated("bot02")
        assert (opened, roles(made, 7)) == (
            ["hall", "boot", "out", "out", "out", "out", "out"],
            ["hall", "boot", "boot", "out", "out", "out", "out"])

    def test_the_phase_stops_short_of_leaving_the_lobby_thin(self):
        # Seven workers: four startup workers, then the second watcher and
        # the spare — not six startup workers and an empty lobby behind them.
        made, lines = rota(bootstrap=True)
        made.open(labels(7))
        for label in ("bot02", "bot03", "bot04", "bot05"):
            made.seated(label)
        assert (roles(made, 7), phase(lines)) == (
            ["hall", "boot", "boot", "boot", "boot", "hall", "spare"],
            [("bootstrap_started", None, None), ("bootstrap_done", "exhausted", 4)])

    def test_a_startup_worker_that_finds_no_table_ends_the_phase(self):
        made, lines = rota(bootstrap=True)
        made.open(labels(7))
        back = made.done("bot02")
        # It goes back to the lobby as the second watcher; the spare is woken.
        assert (back, roles(made, 7)[:3], phase(lines)[-1]) == (
            Role.HALL, ["hall", "hall", "spare"], ("bootstrap_done", "empty", 1))

    def test_a_startup_worker_that_fails_ends_the_phase(self):
        made, lines = rota(bootstrap=True)
        made.open(labels(7))
        made.vacate("bot02", "session_failed")
        assert (roles(made, 7)[:4], phase(lines)[-1]) == (
            ["hall", "out", "hall", "spare"], ("bootstrap_done", "failed", 1))

    def test_a_stopping_fleet_ends_the_phase(self):
        stopped = [False]
        made, lines = rota(bootstrap=True, stopping=lambda: stopped[0])
        made.open(labels(7))
        stopped[0] = True
        made.ask("bot07")
        assert phase(lines)[-1] == ("bootstrap_done", "stopping", 1)

    def test_a_small_fleet_sends_nobody(self):
        # Three workers are two watchers and a spare: nobody is left over.
        made, lines = rota(bootstrap=True)
        made.open(labels(3))
        assert (roles(made, 3), phase(lines)) == (
            ["hall", "hall", "spare"], [("bootstrap_done", "exhausted", 0)])

    def test_only_the_scanning_worker_sends_the_next(self):
        made, _ = rota(bootstrap=True)
        made.open(labels(7))
        made.seated("bot01")
        assert roles(made, 7)[2] == "out"

    def test_a_startup_worker_back_from_its_game_takes_what_is_open(self):
        made, _ = rota(bootstrap=True)
        made.open(labels(5))
        made.seated("bot02")
        made.seated("bot03")
        # Five workers send two; the phase closed on bot03's taking its table,
        # the lobby and the spare are full, so bot02 is parked after its game.
        assert (made.done("bot02"), roles(made, 5)) == (
            Role.OUT, ["hall", "out", "boot", "hall", "spare"])


class TestWaiting:
    def test_a_waiting_worker_is_woken_by_its_new_role(self):
        made, _ = rota()

        async def scenario():
            made.open(labels(4))
            waiting = asyncio.create_task(made.wait("bot04", 60.0))
            await asyncio.sleep(0)
            made.chasing("bot01")
            await asyncio.wait_for(waiting, 1.0)
            return made.role("bot04")

        assert asyncio.run(scenario()) is Role.SPARE

    def test_a_wait_nobody_ends_times_out(self):
        made, _ = rota()

        async def scenario():
            made.open(labels(4))
            await made.wait("bot04", 0.01)
            return made.role("bot04")

        assert asyncio.run(scenario()) is Role.OUT
