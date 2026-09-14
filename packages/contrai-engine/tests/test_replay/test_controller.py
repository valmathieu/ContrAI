"""Pins ``ReplayController`` against records of games actually played.

The load-bearing assertion in this module is the round trip: play a
seeded game, record it, replay the record, and get the same game back —
same contracts, same trick winners, same score line, same totals. It is
the codec-and-driver regression test spec §5.6 asks for, and it is worth
more than any hand-written record could be, because the engine that
produced the record is the one being asked to reproduce it.
"""

from __future__ import annotations

import pytest
from contrai_core import (
    Card,
    ContractBid,
    PassBid,
    Position,
    Rank,
    RuleConfig,
    Suit,
)
from contrai_core.exceptions import IllegalPlayError
from contrai_data import (
    BidMade,
    CardPlayed,
    EndReason,
    GameEnded,
    GameStarted,
    HandsDerivation,
    Header,
    RecordSource,
    Ruleset,
    Seat,
    SeatKind,
    project,
)

from contrai_engine.model.round import marked_components
from contrai_engine.replay import (
    RecordedPlayer,
    ReplayController,
    RoundExhaustedError,
)

from .conftest import play_and_record


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """A recorded game, replayed, is the same game."""

    def test_every_complete_round_is_replayed(self, recorded_game):
        controller = ReplayController(recorded_game)

        controller.run()

        assert len(controller.rounds) == len(recorded_game.rounds)
        assert controller.skipped == ()
        assert controller.game.round_number == len(recorded_game.rounds)

    def test_the_final_totals_match_the_record(self, recorded_game):
        controller = ReplayController(recorded_game)

        controller.run()

        assert controller.game.scores == recorded_game.ended.totals

    def test_the_replayed_auction_matches_the_record(self, recorded_game):
        # Bids are compared by *seat and value*, not by object: the
        # record seats them on a Position and the replay re-seats them
        # onto a live player.
        seen: list[list[tuple[Position, str]]] = []

        class _Auctions:
            def on_round_complete(self, round_, scores):
                seen.append(
                    [(bid.player.position, str(bid)) for bid in round_.auction.bids]
                )

        ReplayController(recorded_game, view=_Auctions()).run()

        expected = [
            [(bid.player, str(bid)) for bid in round_.auction]
            for round_ in recorded_game.rounds
        ]
        assert seen == expected

    def test_the_replayed_trick_winners_match_the_record(self, recorded_game):
        won: list[tuple[Position, ...]] = []

        class _Winners:
            def on_round_complete(self, round_, scores):
                state = round_.play_state
                won.append(
                    ()
                    if state is None
                    else tuple(winner.position for winner in state.trick_winners)
                )

        ReplayController(recorded_game, view=_Winners()).run()

        expected = [round_.trick_winners for round_ in recorded_game.rounds]
        assert won == expected

    def test_the_replayed_round_scores_match_the_record(self, recorded_game):
        # The two components are compared separately, as the verifier
        # will: a round marking the same total out of a different made /
        # announced split is a scoring-rule regression, not a match. The
        # replay's components are reduced to the figures a score sheet
        # carries, which is what a record holds — a doubled round's are
        # already multiplied there.
        scored: list[tuple] = []

        class _Scores:
            def on_round_complete(self, round_, scores):
                score = round_.round_score
                scored.append(
                    (
                        round_.round_number,
                        {
                            side: marked_components(
                                mark, score.multiplier, round_.rules
                            )
                            for side, mark in score.marks.items()
                        },
                        dict(score.card_points),
                        dict(score.belote_points),
                    )
                )

        ReplayController(recorded_game, view=_Scores()).run()

        assert len(scored) == len(recorded_game.rounds)
        for (number, marks, taken, belote), round_ in zip(
            scored, recorded_game.rounds
        ):
            assert number == round_.number
            expected = round_.score
            assert expected is not None
            assert marks == {
                side: (mark.made, mark.announced)
                for side, mark in expected.marked.items()
            }
            assert taken == expected.taken
            assert belote == expected.belote

    def test_the_deal_comes_from_the_record(self, recorded_game):
        dealt: list[tuple] = []

        class _Deals:
            def on_round_dealt(self, round_):
                dealt.append(
                    (
                        round_.dealer.position,
                        {
                            player.position: frozenset(player.hand.cards)
                            for player in round_.players_order
                        },
                    )
                )

        ReplayController(recorded_game, view=_Deals()).run()

        for (dealer, hands), round_ in zip(dealt, recorded_game.rounds):
            assert dealer is round_.dealer
            assert hands == {
                seat: frozenset(cards) for seat, cards in round_.hands.items()
            }

    def test_a_replay_consumes_no_randomness(self, recorded_game):
        # No shuffle, no cut, no dealer draw, and recorded seats break no
        # ties — so two replays of one record are identical without a
        # seed, which is what makes a verifier's verdict reproducible.
        import random

        random.seed(999)
        first = ReplayController(recorded_game)
        first.run()
        state = random.getstate()

        second = ReplayController(recorded_game)
        second.run()

        assert random.getstate() == state
        assert second.game.scores == first.game.scores

    def test_it_replays_a_tournament_record(self, recorded_tournament_game):
        # ``tournament`` is what observed games carry, and it plays clockwise.
        controller = ReplayController(recorded_tournament_game)

        controller.run()

        assert controller.game.rules == RuleConfig.tournament()
        assert controller.game.scores == recorded_tournament_game.ended.totals

    def test_an_all_pass_round_replays(self, tmp_path):
        # Seed 1's first round is passed out, which is the path that
        # returns every card to the deck: a driver reaching past
        # ``manage_round`` would leave the hands full and the next deal
        # would hand out sixteen cards.
        record = play_and_record(tmp_path, seed=1)
        passed_out = [r for r in record.rounds if r.contract is None]
        assert passed_out, "seed 1 is expected to pass a round out"

        controller = ReplayController(record)
        controller.run()

        assert controller.game.scores == record.ended.totals


# ---------------------------------------------------------------------------
# The table the controller builds
# ---------------------------------------------------------------------------


class TestTable:
    def test_it_seats_four_recorded_players(self, recorded_game):
        controller = ReplayController(recorded_game)

        assert len(controller.players) == 4
        assert all(isinstance(p, RecordedPlayer) for p in controller.players)
        assert {p.position for p in controller.players} == set(Position)

    def test_partners_share_one_team_instance(self, recorded_game):
        # Legality reads seats, but ``Contract.team`` and the rule-based
        # bidding strategy still reach a side through ``Team`` identity, so
        # partners must share one instance. Building the table through
        # ``Game`` is what guarantees it.
        controller = ReplayController(recorded_game)
        seats = controller.game.players_by_position

        assert seats[Position.NORTH].team is seats[Position.SOUTH].team
        assert seats[Position.EAST].team is seats[Position.WEST].team
        assert seats[Position.NORTH].team is not seats[Position.EAST].team

    def test_it_plays_under_the_record_s_ruleset(self, recorded_tournament_game):
        controller = ReplayController(recorded_tournament_game)

        assert controller.game.rules is recorded_tournament_game.ruleset

    def test_it_names_the_seats_from_the_record(self, recorded_game):
        controller = ReplayController(recorded_game)

        for player in controller.players:
            assert player.name == recorded_game.seats[player.position].name


# ---------------------------------------------------------------------------
# Incomplete rounds
# ---------------------------------------------------------------------------


def _round_dealt_of(record, number):
    """The ``round_dealt`` event of ``number`` in ``record``."""

    round_ = next(r for r in record.rounds if r.number == number)
    from contrai_data import RoundDealt

    return RoundDealt(
        round=round_.number,
        dealer=round_.dealer,
        hands=round_.hands,
        hands_derivation=HandsDerivation.SELF_PLAY,
        ts="2026-09-11T00:00:00Z",
    )


class TestIncompleteRounds:
    """A round the record left unfinished is not replayed."""

    @staticmethod
    def _truncated(record):
        """``record`` with its last round cut off after one bid."""

        last = record.rounds[-1]
        events = [
            Header(
                format=record.header.format,
                source=RecordSource.ENGINE,
                generator="test",
                game_id="engine-test",
                created_at="2026-09-11T00:00:00Z",
            ),
            GameStarted(
                ruleset=Ruleset(preset=record.preset, config=record.ruleset),
                seats=record.seats,
                observed_from=None,
                ts="2026-09-11T00:00:00Z",
            ),
        ]
        for round_ in record.rounds[:-1]:
            events.extend(_events_of(round_))
        events.append(_round_dealt_of(record, last.number))
        events.append(
            BidMade(
                round=last.number,
                seq=1,
                position=last.auction[0].player,
                bid=last.auction[0],
                think_ms=None,
                ts="2026-09-11T00:00:00Z",
            )
        )
        events.append(
            GameEnded(
                totals=None,
                winner=None,
                reason=EndReason.INTERRUPTED,
                ts="2026-09-11T00:00:00Z",
            )
        )
        return project(events)

    def test_an_unfinished_round_is_listed_but_not_replayed(
        self, recorded_game
    ):
        record = self._truncated(recorded_game)

        controller = ReplayController(record)
        controller.run()

        assert len(controller.skipped) == 1
        assert controller.skipped[0].number == record.rounds[-1].number
        assert len(controller.rounds) == len(record.rounds) - 1
        assert controller.game.round_number == len(controller.rounds)

    def test_running_past_the_script_is_refused(self, recorded_game):
        # The controller decides how many rounds to run, so this is a
        # driver bug rather than a record defect — and it announces
        # itself instead of dealing something arbitrary.
        controller = ReplayController(recorded_game)
        controller.run()

        with pytest.raises(RoundExhaustedError) as excinfo:
            controller.game.start_new_round()

        assert "round" in str(excinfo.value)


def _events_of(round_):
    """Rebuild one projected round's events, in file order."""

    from contrai_data import RoundDealt

    yield RoundDealt(
        round=round_.number,
        dealer=round_.dealer,
        hands=round_.hands,
        hands_derivation=round_.hands_derivation,
        ts="2026-09-11T00:00:00Z",
    )
    for index, bid in enumerate(round_.auction, start=1):
        yield BidMade(
            round=round_.number,
            seq=index,
            position=bid.player,
            bid=bid,
            think_ms=None,
            ts="2026-09-11T00:00:00Z",
        )
    for number, trick in enumerate(round_.tricks, start=1):
        for play in trick:
            yield CardPlayed(
                round=round_.number,
                trick=number,
                position=play.position,
                card=play.card,
                derived=False,
                think_ms=None,
                ts="2026-09-11T00:00:00Z",
            )


# ---------------------------------------------------------------------------
# Divergence
# ---------------------------------------------------------------------------


class TestDivergence:
    """An action the rules refuse stops the replay, loudly."""

    def test_an_illegal_card_raises_out_of_the_engine(self, recorded_game):
        # Legality is never re-implemented here: the recorded card is
        # handed to the real ``PlayState``, which objects. Swapping one
        # seat's two cards inside a trick is enough, since the second
        # then fails to follow.
        record = self._with_swapped_play(recorded_game)

        controller = ReplayController(record)

        with pytest.raises(IllegalPlayError):
            controller.run()

    @staticmethod
    def _with_swapped_play(record):
        """``record`` with the first contracted round's trick 1 reordered."""

        target = next(r for r in record.rounds if r.contract is not None)
        events = [
            Header(
                format=record.header.format,
                source=RecordSource.ENGINE,
                generator="test",
                game_id="engine-test",
                created_at="2026-09-11T00:00:00Z",
            ),
            GameStarted(
                ruleset=Ruleset(preset=record.preset, config=record.ruleset),
                seats=record.seats,
                observed_from=None,
                ts="2026-09-11T00:00:00Z",
            ),
        ]
        for round_ in record.rounds:
            for event in _events_of(round_):
                if (
                    isinstance(event, CardPlayed)
                    and event.round == target.number
                    and event.trick == 1
                ):
                    # Give every seat the last player's card: three of
                    # the four seats do not hold it.
                    event = CardPlayed(
                        round=event.round,
                        trick=event.trick,
                        position=event.position,
                        card=target.tricks[0][3].card,
                        derived=False,
                        think_ms=None,
                        ts=event.ts,
                    )
                events.append(event)
            if round_.number == target.number:
                break
        events.append(
            GameEnded(
                totals=None,
                winner=None,
                reason=EndReason.INTERRUPTED,
                ts="2026-09-11T00:00:00Z",
            )
        )
        return project(events)
