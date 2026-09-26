"""Pins the registry: one winner per claim, expiry, release, the census."""

import asyncio

import pytest

from contrai_scraper import TableRegistry

ROSTER = frozenset({"095024", "100001", "100002", "100003"})


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def registry(ttl=600.0):
    """An empty registry on a hand-moved clock, and the clock."""

    clock = Clock()
    return TableRegistry(claim_ttl_s=ttl, monotonic=clock), clock


class TestTheRace:
    def test_ten_workers_claiming_one_table_leave_one_winner(self):
        # Every claimant is its own task and yields before claiming, so the
        # claims interleave the way a fleet's do.
        table, _ = registry()

        async def claimant(label):
            await asyncio.sleep(0)
            return table.claim_table("t1", label)

        async def scenario():
            return await asyncio.gather(*(claimant(f"bot{n:02}") for n in range(10)))

        results = asyncio.run(scenario())
        assert (results.count(True), table.table_holder("t1")) == (1, "bot00")

    def test_ten_workers_claiming_one_roster_leave_one_chaser(self):
        table, _ = registry()

        async def claimant(label):
            await asyncio.sleep(0)
            return table.claim_roster(ROSTER, label)

        async def scenario():
            return await asyncio.gather(*(claimant(f"bot{n:02}") for n in range(10)))

        assert asyncio.run(scenario()).count(True) == 1


class TestClaims:
    def test_a_holder_may_claim_again(self):
        table, _ = registry()
        assert (table.claim_table("t1", "bot01"), table.claim_table("t1", "bot01")) == (
            True, True)

    def test_a_live_claim_holds_against_another_worker(self):
        table, clock = registry(ttl=600)
        table.claim_table("t1", "bot01")
        clock.now += 599
        assert (table.claim_table("t1", "bot02"), table.table_holder("t1")) == (
            False, "bot01")

    def test_an_expired_claim_can_be_taken_over(self):
        # The backstop for a worker that wedged holding a table.
        table, clock = registry(ttl=600)
        table.claim_table("t1", "bot01")
        clock.now += 600
        assert (table.table_holder("t1"), table.claim_table("t1", "bot02")) == (
            None, True)

    def test_claiming_again_refreshes_the_claim(self):
        table, clock = registry(ttl=600)
        table.claim_table("t1", "bot01")
        clock.now += 500
        table.claim_table("t1", "bot01")
        clock.now += 500
        assert table.claim_table("t1", "bot02") is False

    def test_a_released_table_is_free(self):
        table, _ = registry()
        table.claim_table("t1", "bot01")
        table.release_table("t1", "bot01")
        assert table.claim_table("t1", "bot02") is True

    def test_a_worker_cannot_release_a_claim_that_is_not_its_own(self):
        # bot01's claim expired and bot02 took the table over; bot01 leaving
        # late must not free it under bot02.
        table, clock = registry(ttl=600)
        table.claim_table("t1", "bot01")
        clock.now += 601
        table.claim_table("t1", "bot02")
        table.release_table("t1", "bot01")
        assert table.table_holder("t1") == "bot02"

    def test_a_released_roster_can_be_chased_by_another(self):
        table, _ = registry()
        table.claim_roster(ROSTER, "bot01")
        refused = table.claim_roster(ROSTER, "bot02")
        table.release_roster(ROSTER, "bot01")
        assert (refused, table.claim_roster(ROSTER, "bot02")) == (False, True)

    def test_rosters_and_tables_are_separate_claims(self):
        # Two keys, two jobs: holding a chase does not hold a table.
        table, _ = registry()
        table.claim_roster(ROSTER, "bot01")
        assert table.claim_table("t1", "bot02") is True


class TestCensus:
    def test_the_last_sighting_of_each_table_is_kept(self):
        table, clock = registry()
        table.note_seen("t1", is_tournament=True, round_index=None)
        clock.now += 30
        table.note_seen("t1", is_tournament=True, round_index=3)
        table.note_seen("t2", is_tournament=False, round_index=1)
        census = table.census()
        assert ((census["t1"].round_index, census["t1"].at), sorted(census)) == (
            (3, 1030.0), ["t1", "t2"])

    def test_the_census_is_a_copy(self):
        table, _ = registry()
        table.census()["t9"] = None
        assert table.census() == {}


class TestPopulationEstimate:
    @staticmethod
    def _expected(population, sightings):
        # The model, restated here rather than imported: uniform draws with
        # replacement leave this many distinct tables on average.
        return population * (1 - (1 - 1 / population) ** sightings)

    @pytest.mark.parametrize(("sightings", "distinct"), [(20, 9), (12, 7), (5, 4)])
    def test_the_estimate_expects_exactly_what_was_seen(self, sightings, distinct):
        from contrai_scraper import estimate_population

        estimate = estimate_population(sightings, distinct)
        assert abs(self._expected(estimate, sightings) - distinct) < 0.05

    def test_more_repeats_mean_a_smaller_population(self):
        from contrai_scraper import estimate_population

        assert estimate_population(20, 6) < estimate_population(20, 12)

    def test_one_table_seen_again_and_again_is_one_table(self):
        from contrai_scraper import estimate_population

        assert estimate_population(8, 1) == 1.0

    @pytest.mark.parametrize(("sightings", "distinct"), [(0, 0), (5, 5)],
                             ids=["nothing-seen", "nothing-seen-twice"])
    def test_without_a_repeat_there_is_no_estimate(self, sightings, distinct):
        from contrai_scraper import estimate_population

        assert estimate_population(sightings, distinct) is None


class TestWorkerClaims:
    def test_claiming_a_new_table_gives_the_old_one_up(self):
        table, _ = registry()
        mine = table.for_worker("bot01")
        mine.claim("t1")
        mine.claim("t2")
        assert (table.table_holder("t1"), table.table_holder("t2")) == (None, "bot01")

    def test_claiming_the_table_already_held_keeps_it(self):
        # A table re-offered after a boundary read is the same table: the
        # claim is refreshed, never dropped and retaken.
        table, _ = registry()
        mine = table.for_worker("bot01")
        mine.claim("t1")
        assert (mine.claim("t1"), table.table_holder("t1")) == (True, "bot01")

    def test_a_table_held_elsewhere_is_refused_and_its_holder_named(self):
        table, _ = registry()
        table.for_worker("bot02").claim("t1")
        mine = table.for_worker("bot01")
        assert (mine.claim("t1"), mine.holder("t1"), mine.worker) == (False, "bot02", "bot01")

    def test_a_table_with_no_id_cannot_be_protected_and_is_let_through(self):
        table, _ = registry()
        mine = table.for_worker("bot01")
        assert (mine.claim(None), mine.holder(None)) == (True, None)

    def test_holding_keeps_the_claim_alive(self):
        table, clock = registry(ttl=600)
        mine = table.for_worker("bot01")
        mine.claim("t1")
        clock.now += 500
        mine.hold()
        clock.now += 500
        assert table.table_holder("t1") == "bot01"

    def test_holding_or_releasing_nothing_is_nothing(self):
        table, _ = registry()
        mine = table.for_worker("bot01")
        mine.hold()
        mine.release()
        assert table.census() == {}

    def test_releasing_frees_the_held_table(self):
        table, _ = registry()
        mine = table.for_worker("bot01")
        mine.claim("t1")
        mine.release()
        assert table.table_holder("t1") is None

    def test_a_sighting_without_an_id_is_not_counted(self):
        table, _ = registry()
        mine = table.for_worker("bot01")
        mine.seen(None, is_tournament=True, round_index=None)
        mine.seen("t1", is_tournament=True, round_index=None)
        assert list(table.census()) == ["t1"]
