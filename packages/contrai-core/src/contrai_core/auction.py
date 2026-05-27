"""Auction: immutable bidding-phase state and rule oracle.

The :class:`Auction` owns the chronological :class:`Bid` history and
knows the rules of Contrée bidding. It exposes the questions callers
actually need to answer:

- :meth:`Auction.is_legal` / :meth:`Auction.legal_actions` so callers
  can avoid proposing illegal bids in the first place — there is no
  silent "force a Pass on an illegal bid" fallback in this design.
- :meth:`Auction.apply` to produce a new ``Auction`` with the bid
  appended; raises :class:`IllegalBidError` when the bid is illegal.
- :meth:`Auction.is_terminal` and :meth:`Auction.contract` to detect
  bidding completion and materialise the winning :class:`Contract`.

This split is deliberate. Bid variants (see :mod:`contrai_core.bid`)
are dumb value carriers; all knowledge about *what is legal when*
lives here, alongside the auction state itself. This is the same
shape that MCTS / RL game-state interfaces use (``legal_actions``
plus ``apply``), so the bidding phase is ready to drop into a future
imperfect-information game-state object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .bid import Bid, ContractBid, DoubleBid, PassBid, RedoubleBid
from .contract import Contract
from .exceptions import IllegalBidError

if TYPE_CHECKING:
    from .player import BasePlayer


@dataclass(frozen=True, slots=True)
class Auction:
    """Immutable bidding-phase state for one round of contrée.

    ``Auction`` is the canonical view of bidding-so-far: it owns the
    chronological tuple of :class:`Bid` objects, knows the contrée
    bidding rules, and answers questions about what is legal now,
    whether the auction has concluded, and what :class:`Contract` (if
    any) the bids produced.

    The class is intentionally immutable: :meth:`apply` returns a new
    instance instead of mutating ``self``. Frozen + slots keeps copies
    cheap and equality cleanly derived from the bid history.

    Attributes:
        bids: The chronological tuple of bids made so far. Defaults to
            the empty tuple — an :class:`Auction` with no bids is a
            fresh, valid auction state at the start of a round.
    """

    bids: tuple[Bid, ...] = field(default=())

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def empty(cls) -> "Auction":
        """Return a fresh auction with no bids yet."""

        return cls()

    # ------------------------------------------------------------------
    # Legality queries
    # ------------------------------------------------------------------

    def is_legal(self, bid: Bid) -> bool:
        """Return whether ``bid`` is a legal next action in this auction.

        Args:
            bid: The candidate :class:`Bid` (player + type + payload).
                Any concrete ``Bid`` subclass is accepted.

        Returns:
            ``True`` if ``bid`` may legally be played as the next
            action; ``False`` otherwise. Unknown ``Bid`` subclasses
            return ``False`` defensively.
        """

        if isinstance(bid, PassBid):
            return True
        if isinstance(bid, ContractBid):
            return self._is_contract_legal(bid)
        if isinstance(bid, DoubleBid):
            return self._is_double_legal(bid)
        if isinstance(bid, RedoubleBid):
            return self._is_redouble_legal(bid)
        return False

    def legal_actions(self, player: "BasePlayer") -> tuple[Bid, ...]:
        """Enumerate every legal bid ``player`` could make right now.

        Suitable for handing to an MCTS / RL action enumerator or for
        filtering a UI's option list down to only the choices the
        engine will accept.

        Args:
            player: The player whose turn it is.

        Returns:
            A tuple of legal :class:`Bid` instances. Always non-empty
            — :class:`PassBid` is unconditionally legal. The first
            entry is always a :class:`PassBid` so single-action calls
            can deterministically pick ``actions[0]``.
        """

        actions: list[Bid] = [PassBid(player)]

        for value in ContractBid.VALID_VALUES:
            for suit in ContractBid.VALID_SUITS:
                candidate = ContractBid(player, value, suit)
                if self._is_contract_legal(candidate):
                    actions.append(candidate)

        double_candidate = DoubleBid(player)
        if self._is_double_legal(double_candidate):
            actions.append(double_candidate)

        redouble_candidate = RedoubleBid(player)
        if self._is_redouble_legal(redouble_candidate):
            actions.append(redouble_candidate)

        return tuple(actions)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, bid: Bid) -> "Auction":
        """Return a new auction with ``bid`` appended.

        Args:
            bid: The bid to apply. Must be legal in the current state.

        Raises:
            IllegalBidError: If ``bid`` is not a legal next action.

        Returns:
            A new :class:`Auction` whose ``bids`` ends with ``bid``.
            The receiver is left unchanged (frozen dataclass).
        """

        if not self.is_legal(bid):
            raise IllegalBidError(bid, self.bids)
        return Auction(self.bids + (bid,))

    # ------------------------------------------------------------------
    # Termination
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return whether the auction has concluded.

        The auction ends on either of:

        1. Four consecutive passes from the very first bid — the
           first-round all-pass wipe (``contree-domain.md §5.4``,
           "If everyone passes without anyone bidding, the round is
           annulled").
        2. Three consecutive passes after at least one non-pass bid.
           The winning contract is the last non-pass numeric bid.
        """

        if not self.bids:
            return False
        # All-pass wipe — exactly four players, every bid a pass.
        if all(isinstance(b, PassBid) for b in self.bids):
            return len(self.bids) >= 4
        # Three passes after a non-pass.
        return self.consecutive_passes >= 3

    def contract(self) -> Optional[Contract]:
        """Return the :class:`Contract` produced by this auction.

        Returns:
            A :class:`Contract` built from the last :class:`ContractBid`
            with appropriate ``double`` / ``redouble`` flags, or
            ``None`` if the auction concluded without any numeric bid.
        """

        cb = self.last_contract
        if cb is None:
            return None
        return Contract(cb, double=self.has_double, redouble=self.has_redouble)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def last_contract(self) -> Optional[ContractBid]:
        """The most recent :class:`ContractBid` in the history, or ``None``."""

        for bid in reversed(self.bids):
            if isinstance(bid, ContractBid):
                return bid
        return None

    @property
    def has_double(self) -> bool:
        """Whether a :class:`DoubleBid` stands against the current contract.

        Walking backwards from the end of the history, this is ``True``
        iff a :class:`DoubleBid` appears before any :class:`ContractBid`.
        """

        for bid in reversed(self.bids):
            if isinstance(bid, ContractBid):
                return False
            if isinstance(bid, DoubleBid):
                return True
        return False

    @property
    def has_redouble(self) -> bool:
        """Whether a :class:`RedoubleBid` stands against the current double.

        ``True`` iff a :class:`RedoubleBid` was played after the most
        recent :class:`DoubleBid`.
        """

        for bid in reversed(self.bids):
            if isinstance(bid, DoubleBid):
                return False
            if isinstance(bid, RedoubleBid):
                return True
        return False

    @property
    def consecutive_passes(self) -> int:
        """Count of consecutive :class:`PassBid` instances at the tail."""

        count = 0
        for bid in reversed(self.bids):
            if isinstance(bid, PassBid):
                count += 1
            else:
                break
        return count

    def partner_bid(self, player: "BasePlayer") -> Optional[Bid]:
        """The most recent non-pass bid made by ``player``'s partner.

        Useful for AI strategies that condition on a partner's last
        action (raise, support, mirror suit choice, etc.).

        Args:
            player: The player whose partner we want to inspect.

        Returns:
            The partner's most recent non-pass :class:`Bid`, or
            ``None`` if ``player`` has no team, no partner has bid,
            or the partner has only passed.
        """

        if player.team is None:
            return None
        for bid in reversed(self.bids):
            if isinstance(bid, PassBid):
                continue
            if bid.player is not player and bid.player.team is player.team:
                return bid
        return None

    # ------------------------------------------------------------------
    # Rule helpers (private — see contree-domain.md §5.2 and §5.3)
    # ------------------------------------------------------------------

    def _is_contract_legal(self, bid: ContractBid) -> bool:
        """A new :class:`ContractBid` must strictly outrank the prior
        numeric contract, and the auction must not be frozen by a
        :class:`DoubleBid` or :class:`RedoubleBid`.

        Per ``contree-domain.md §5.3`` a *contre* freezes the auction
        at the current contract — no more numeric bids are legal until
        the auction completes.
        """

        last_contract = None
        for prev in reversed(self.bids):
            if isinstance(prev, ContractBid):
                last_contract = prev
                break
            if isinstance(prev, (DoubleBid, RedoubleBid)):
                # Auction is frozen; new numeric bids cannot reopen it.
                return False
        if last_contract is None:
            return True
        # Once a Slam or SoloSlam has been announced, no further contract
        # bid is legal. This is asymmetric for the Slam → SoloSlam
        # progression: Slam (500) blocks SoloSlam (1000) even though the
        # latter outranks it numerically, per the user-confirmed rule.
        if last_contract.value in ("Slam", "SoloSlam"):
            return False
        # Slam and SoloSlam outrank every numeric contract (80–160).
        if bid.value in ("Slam", "SoloSlam"):
            return True
        return bid.value > last_contract.value

    def _is_double_legal(self, bid: DoubleBid) -> bool:
        """A :class:`DoubleBid` requires a live :class:`ContractBid`
        by the opposing team, no intervening pass since that bid, and
        no prior :class:`DoubleBid` / :class:`RedoubleBid`.
        """

        if not self.bids:
            return False
        last_contract = None
        has_double = False
        passes_since_contract = 0
        for prev in reversed(self.bids):
            if isinstance(prev, ContractBid):
                last_contract = prev
                break
            elif isinstance(prev, DoubleBid):
                has_double = True
            elif isinstance(prev, RedoubleBid):
                return False
            elif isinstance(prev, PassBid):
                passes_since_contract += 1
        if last_contract is None or has_double:
            return False
        if last_contract.player.team is bid.player.team:
            return False
        if passes_since_contract > 0:
            return False
        return True

    def _is_redouble_legal(self, bid: RedoubleBid) -> bool:
        """A :class:`RedoubleBid` requires a live :class:`DoubleBid`
        against the bidder's team, no intervening pass since that
        Double, and no prior :class:`RedoubleBid`.
        """

        if not self.bids:
            return False
        contract_player = None
        has_double = False
        has_redouble = False
        passes_since_double = 0
        for prev in reversed(self.bids):
            if isinstance(prev, RedoubleBid):
                has_redouble = True
                break
            elif isinstance(prev, DoubleBid):
                # Found the DoubleBid we'd be redoubling; keep scanning
                # backwards for the underlying ContractBid.
                has_double = True
            elif isinstance(prev, PassBid):
                # Only passes that appear after the double (chronologically)
                # close the redouble window. In reversed iteration those
                # are encountered *before* we see the DoubleBid.
                if not has_double:
                    passes_since_double += 1
            elif isinstance(prev, ContractBid):
                contract_player = prev.player
                break
        if not has_double or has_redouble or contract_player is None:
            return False
        if contract_player.team is not bid.player.team:
            return False
        if passes_since_double > 0:
            return False
        return True
