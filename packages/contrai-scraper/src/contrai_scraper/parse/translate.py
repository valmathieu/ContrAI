"""The vocabulary layer: site tokens in, ``contrai_core`` values out.

Everything above this module works in core types and never sees a site
string; everything below it is the profile. That split is what lets the site
change its wording without a line of parser changing, and — more to the point
— what lets the whole suite run against an invented vocabulary, so no tracked
file ever has to spell the real one.

The seat map deserves its own paragraph, because it is where this layer earns
its keep. It is a placement map — each on-screen seat maps to the compass seat
it shows — and it can be wrong in a way no spot check sees: cross the two side
seats and every name still reads plausibly while the record's table turns
backwards. Every trick winner is then wrong, every legality check is wrong,
and the file looks perfectly well formed throughout.

So the constructor does not check the names. It checks the **invariant**:
walking the site's own rotation through the map must walk core's seats one
step at a time, in the direction the profile's preset plays — clockwise, at
the observed tables. A map with its side seats crossed walks the other way
and is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contrai_core import PRESETS, Card, ContractSuit, Position, SlamLevel, TeamSide

from ..exceptions import ParseError, ProfileError
from ..profile import Profile
from ..wire import dig


class Translator:
    """Resolves the site's tokens through one profile.

    The maps are inverted once, at construction, because a parse walks them
    several thousand times per game.
    """

    __slots__ = ("_profile", "_cards", "_seat_names", "_trumps", "_rotation")

    def __init__(self, profile: Profile) -> None:
        """Build the lookup tables and check the seat map's rotation.

        Args:
            profile: The loaded profile.

        Raises:
            ProfileError: If the seat map does not walk the table in the
                direction the profile's preset plays — see this module's
                docstring; that is the one profile mistake no later check
                could catch.
        """

        self._profile = profile
        tokens = profile.wire.tokens
        self._cards = {
            f"{rank_token}{suit_token}": Card(suit, rank)
            for rank_token, rank in tokens.ranks.items()
            for suit_token, suit in tokens.suits.items()
        }
        self._seat_names = {seat: name for name, seat in tokens.seats.items()}
        # Glyphs and words both name a trump: the live events spell one way,
        # the score rows the other. Neither map may shadow the other, and the
        # profile keeps them apart, so a single merged lookup is safe.
        self._trumps: dict[str, ContractSuit] = {
            **tokens.suits,
            **tokens.suit_words,
        }

        # The direction is the preset's, because that is the ruleset every
        # record of this table is replayed under: a map walking the other way
        # would turn the replayed table backwards.
        direction = PRESETS[profile.rules.preset].turn_direction
        rotation = tuple(tokens.seats[name] for name in tokens.seat_rotation)
        for name, seat, follower in zip(
            tokens.seat_rotation, rotation, rotation[1:] + rotation[:1], strict=True
        ):
            successor = seat.next_in(direction)
            if successor is not follower:
                raise ProfileError(
                    "[wire.tokens].seats does not preserve the table's "
                    f"rotation: {name!r} maps to {seat.value}, whose next seat "
                    f"{direction} is {successor.value}, but seat_rotation goes "
                    f"on to {follower.value}"
                )
        self._rotation = rotation

    @property
    def profile(self) -> Profile:
        """The profile this translator reads through.

        Exposed because the stages above need the *rest* of the document —
        the token constants, the round-state prefix — and threading both a
        profile and a translator through every call would be two handles on
        one thing.
        """

        return self._profile

    @property
    def rotation(self) -> tuple[Position, ...]:
        """The four seats in the site's own order, which is what a deal walks."""

        return self._rotation

    def card(self, glyph: str) -> Card:
        """Read a card token.

        Args:
            glyph: The site's spelling of one card.

        Returns:
            The card.

        Raises:
            ParseError: If the token is not one the profile describes. The
                maps were checked complete at load, so this means the site
                said something new.
        """

        try:
            return self._cards[glyph.strip()]
        except KeyError:
            raise ParseError(f"No card is spelled {glyph!r}") from None

    def position(self, name: str) -> Position:
        """Read a seat token.

        Args:
            name: The site's name for one seat.

        Returns:
            The seat.

        Raises:
            ParseError: If the name is not one the profile maps.
        """

        try:
            return self._profile.wire.tokens.seats[name]
        except KeyError:
            raise ParseError(f"No seat is called {name!r}") from None

    def seat_name(self, position: Position) -> str:
        """The site's name for a seat, for filling a seat selector.

        Args:
            position: The seat.

        Returns:
            The token the profile maps to it.
        """

        return self._seat_names[position]

    def contract_suit(self, token: str) -> ContractSuit:
        """Read a trump token, spelled either as a glyph or as a word.

        Args:
            token: The site's spelling of the contract's trump.

        Returns:
            The trump.

        Raises:
            ParseError: If the token is not one the profile describes. The
                observed tables bid neither no trump nor all trump, so a
                profile that names no token for them is complete, and an
                unrecognised token is a site change worth investigating.
        """

        try:
            return self._trumps[token]
        except (KeyError, TypeError):
            raise ParseError(f"No trump is spelled {token!r}") from None

    def contract_value(self, token: Any) -> int | SlamLevel:
        """Read a contract's value.

        Args:
            token: A number, a numeric string, or one of the two slam tokens.

        Returns:
            The value in points, or the slam level.

        Raises:
            ParseError: If the token is neither a number nor a slam token.
        """

        tokens = self._profile.wire.tokens
        if token == tokens.bid_slam:
            return SlamLevel.SLAM
        if token == tokens.bid_solo_slam:
            return SlamLevel.SOLO_SLAM
        try:
            return int(token)
        except (TypeError, ValueError):
            raise ParseError(f"No contract value is spelled {token!r}") from None

    def side(
        self, letter: str, seat_of_letter: Mapping[str, Position]
    ) -> TeamSide:
        """Read a team letter, through the seat that holds it.

        Which letter is the North-South side changes from game to game — the
        letters are the table's own labels, assigned as players sit down — so
        resolving them by their position in ``team_letters`` would be right
        about half the time and wrong silently the rest.

        Args:
            letter: The site's label for one side.
            seat_of_letter: Which seat each letter was observed on.

        Returns:
            The side of the table.

        Raises:
            ParseError: If no observed seat holds the letter.
        """

        try:
            return seat_of_letter[letter].team_side
        except KeyError:
            raise ParseError(
                f"No observed seat holds the team label {letter!r}"
            ) from None

    def field(self, payload: Any, name: str) -> Any:
        """Read a logical field out of a payload.

        Args:
            payload: The payload to walk.
            name: The logical field name, as ``[wire.fields]`` spells it.

        Returns:
            The value, or ``None`` when the payload does not carry it — which
            is normal, since the same event carries different blocks at
            different points in a game.

        Raises:
            KeyError: If ``name`` is not a logical field. That is a bug in
                this package, not in the profile, so it is left to propagate
                as one.
        """

        return dig(payload, self._profile.wire.fields[name])
