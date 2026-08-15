"""Team: the two-player roster sharing a side of the table.

A ``Team`` says *who plays with whom* and what that pairing is called on
screen. It deliberately carries no identity and no score: the identity
is :class:`~contrai_core.team_side.TeamSide`, and cumulative scoring is
the engine's business (``Game.scores``, keyed by that enum).
"""

from __future__ import annotations

from .exceptions import InvalidPlayerCountError


class Team:
    """A pair of players seated across from each other.

    Team identity lives in :class:`~contrai_core.team_side.TeamSide`, not
    here: :attr:`name` is a display label, so two teams sharing a name
    are not thereby the same team, and nothing may key a dictionary by
    it. The seat arithmetic that answers "who is my partner?" and "are we
    on the same side?" belongs to
    :class:`~contrai_core.position.Position` (:attr:`~Position.partner`,
    :meth:`~Position.is_teammate`, :attr:`~Position.team_side`), which
    derives all of it from one seating order.

    Attributes:
        name: Display name of the team, e.g. ``"North-South"``.
        players: The two players forming the team.
    """

    def __init__(self, name, players):
        """Initialize a team with a display name and two players.

        Args:
            name: Display name of the team.
            players: List of exactly 2 players.

        Raises:
            InvalidPlayerCountError: If the number of players is not
                exactly 2.
        """

        if len(players) != 2:
            raise InvalidPlayerCountError(2, len(players), "Creating team")

        self.name = name
        self.players = players

    def __str__(self):
        """Human-readable roster: ``"North-South: Alice & Bob"``."""

        player_names = [player.name for player in self.players]
        return f"{self.name}: {' & '.join(player_names)}"

    def __repr__(self):
        """Developer representation of the team."""

        return f"Team('{self.name}', {len(self.players)} players)"
