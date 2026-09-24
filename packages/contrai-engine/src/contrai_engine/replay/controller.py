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
from contrai_data import SeatKind

from ..model.game import Game
from ..model.player.levels import AI_LEVELS
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
        explained: The seats whose actions are explained by the strategy
            the record names for them — empty unless ``explain`` was
            asked for, and empty for a record that seats no engine AI.
    """

    def __init__(
        self,
        record: "GameRecord",
        view: Any | None = None,
        *,
        explain: bool = False,
    ) -> None:
        """Build the table a record will be replayed at.

        Args:
            record: The record to replay.
            view: An optional observer, driven through exactly the hooks a
                live game drives — which is what lets the verifier be the
                recorder's mirror, and what will let the replay screens
                render an old game with no new rendering code.
            explain: Rebuild the strategy of every seat the record marks
                as an engine AI of a known level, and have it explain
                that seat's recorded actions. Off by default: the
                verifier wants the record replayed, not commented on.
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
            RecordedPlayer(
                self._name(seat),
                seat,
                strategies=self._strategies(seat) if explain else None,
            )
            for seat in Position
        ]
        self.explained: tuple[Position, ...] = tuple(
            player.position
            for player in self.players
            if player.cardplay is not None
        )
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

    def _strategies(self, seat: Position) -> tuple[Any, Any] | None:
        """The strategy pair that played ``seat``, if the record says one did.

        Only a seat the record marks :attr:`~contrai_data.SeatKind.AI`
        *and* whose level this engine registers can be explained. A human
        or observed seat has no strategy to ask, and a level this engine
        does not know — a hand-mixed ``custom`` pair, or a level from a
        newer engine — has none to rebuild; guessing one would put
        another strategy's words in that seat's mouth.

        Args:
            seat: The seat to look up.

        Returns:
            The ``(bidding, cardplay)`` factories, or ``None``.
        """

        occupant = self.record.seats.get(seat)
        if occupant is None or occupant.kind is not SeatKind.AI:
            return None
        return AI_LEVELS.get(occupant.level)

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

    def clear_hands(self) -> None:
        """Empty every seat's hand, so the next round deals into a clean table.

        A round played to the end empties the hands itself; one that
        stopped part-way — an illegal action, a script that ran out — did
        not. ``Deck.deal`` *extends* a hand rather than replacing it, so
        the next round would otherwise seat a player holding sixteen
        cards, and every round after the first failure would look wrong
        for a reason that has nothing to do with the record.
        """

        for player in self.players:
            player.hand.clear()

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
