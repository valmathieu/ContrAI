"""Run a record back through the real engine.

:class:`ReplayController` is the whole driver: four
:class:`~contrai_engine.replay.player.RecordedPlayer` seats, a
:class:`~contrai_engine.model.game.Game` under the record's own
``RuleConfig``, a
:class:`~contrai_engine.replay.deal.ScriptedDealSource`, and one
``manage_round`` per round to replay.

Three things about that are deliberate.

**It goes through ``manage_round``, never ``manage_bidding``.** An
all-pass round only returns its cards to the deck because
``manage_round`` calls ``handle_failed_contract``; a driver reaching past
it would leave eight cards in every hand and the next ``Deck.deal`` would
silently deal sixteen.

**It builds the seats through ``Game``.** Coinching is refused between
players on the same ``Team``, and ``Game.__init__`` is what hands both
partners the same ``Team`` instance — so a driver assembling players by
hand would see every double in the corpus rejected.

**Incomplete rounds are not replayed.** A round whose auction never
closed, or whose eighth trick was never seen, cannot be driven to the end
by any set of actions; the verifier reports it rather than the driver
crashing on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contrai_core.position import Position

from ..model.game import Game
from .deal import ScriptedDealSource
from .player import RecordedPlayer, RoundScript

if TYPE_CHECKING:
    from contrai_data import GameRecord, RoundRecord


class ReplayController:
    """Replays a recorded game through a real :class:`Game`.

    Attributes:
        record: The record being replayed.
        rounds: The rounds that will actually run — the record's, filtered
            to the structurally complete ones.
        skipped: The rounds that will not run, in file order. The verifier
            reports each as ``partial``; nothing here fails because of
            them.
        players: The four seats, in canonical seating order.
        game: The game they play, already built and seated.
    """

    def __init__(
        self, record: "GameRecord", view: Any | None = None
    ) -> None:
        """Build the table a record will be replayed at.

        Args:
            record: The record to replay.
            view: An optional observer, driven through exactly the hooks a
                live game drives — which is what lets the verifier be the
                recorder's mirror, and what will let the replay screens
                render an old game with no new rendering code.
        """

        self.record = record
        self.view = view
        self.rounds: tuple["RoundRecord", ...] = tuple(
            round_ for round_ in record.rounds if round_.complete
        )
        self.skipped: tuple["RoundRecord", ...] = tuple(
            round_ for round_ in record.rounds if not round_.complete
        )
        self.players = [
            RecordedPlayer(self._name(seat), seat) for seat in Position
        ]
        self.game = Game(
            self.players,
            rules=record.ruleset,
            deal_source=ScriptedDealSource(self.rounds),
        )

    def _name(self, seat: Position) -> str:
        """The occupant's name for ``seat``.

        Args:
            seat: The seat to name.

        Returns:
            The record's name for it, or the seat's own name when the
            record does not seat it — which a well-formed record always
            does, but a hand-written one under test need not.
        """

        occupant = self.record.seats.get(seat)
        return occupant.name if occupant is not None else seat.value

    def run(self) -> None:
        """Replay every complete round, in file order.

        The two hooks the CLI issues itself — ``attach`` before the first
        deal and ``on_round_complete`` after each round — are issued here
        too, for the same reason ``main`` issues them: they carry facts no
        model hook does, and an observer that only saw the model's would
        be missing the score line.

        Raises:
            ReplayError: If the engine and the record stop agreeing —
                a seat consulted for an action the record does not hold,
                or one recorded for a different seat.
            IllegalBidError: If a recorded bid is not legal in the
                auction it lands in.
            IllegalPlayError: If a recorded card is not legal in the
                trick it lands in.
        """

        self._notify("attach", self.game, self.game.rules.target_score)
        for round_ in self.rounds:
            self.replay_round(round_)

    def replay_round(self, round_: "RoundRecord") -> None:
        """Replay one round, from its deal to its score.

        The scripted deal source indexes on the game's own round counter,
        so rounds must be replayed in the order they were filtered into
        :attr:`rounds`; this method is public for a caller stepping
        through them, not for picking one out.

        Args:
            round_: The round to replay. Must be the next one due.
        """

        script = RoundScript.of(round_)
        for player in self.players:
            player.script = script
        self.game.manage_round(view=self.view)
        self._notify(
            "on_round_complete", self.game.current_round, self.game.scores
        )

    def _notify(self, hook: str, *args: Any) -> None:
        """Call ``hook`` on the view, if it has one.

        Every notification the engine issues is optional in exactly this
        way, so an observer implements the hooks it cares about and
        nothing else.

        Args:
            hook: The method name.
            *args: Its arguments.
        """

        if self.view is None:
            return
        handler = getattr(self.view, hook, None)
        if handler is not None:
            handler(*args)
