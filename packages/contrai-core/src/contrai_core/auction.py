"""Auction: immutable bidding-phase state and rule oracle.

The :class:`Auction` owns the chronological :class:`Bid` history and
knows the rules of contrée bidding. It exposes the questions callers
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
    def empty(cls) -> Auction:
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

    def legal_actions(self, player: BasePlayer) -> tuple[Bid, ...]:
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

        # Contract legality is suit-agnostic and monotonic in
        # :attr:`ContractBid.VALID_VALUES` order: once a value clears,
        # every later one does too. Probe until the first hit, then fan
        # the rest over every suit. See ``TestLegalActionsMonotonicity``.
        found_legal = False
        for value in ContractBid.VALID_VALUES:
            if not found_legal:
                if not self._is_contract_value_legal(value):
                    continue
                found_legal = True
            for suit in ContractBid.VALID_SUITS:
                actions.append(ContractBid(player, value, suit))

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

    def apply(self, bid: Bid) -> Auction:
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
           first-round all-pass wipe.
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
            with the doubling / redoubling players recorded (which is what
            marks it doubled / redoubled), or ``None`` if the auction
            concluded without any numeric bid.
        """

        cb = self.last_contract_bid
        if cb is None:
            return None
        return Contract(
            cb,
            double_player=self.double_player,
            redouble_player=self.redouble_player,
        )

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def last_contract_bid(self) -> Optional[ContractBid]:
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
    def double_player(self) -> Optional[BasePlayer]:
        """The player whose standing :class:`DoubleBid` doubled the contract.

        Mirrors :attr:`has_double`: walking backwards, this is the
        :class:`DoubleBid`'s player iff that Double appears before any
        :class:`ContractBid`. ``None`` when no Double currently stands.
        """

        for bid in reversed(self.bids):
            if isinstance(bid, ContractBid):
                return None
            if isinstance(bid, DoubleBid):
                return bid.player
        return None

    @property
    def redouble_player(self) -> Optional[BasePlayer]:
        """The player whose standing :class:`RedoubleBid` redoubled.

        Mirrors :attr:`has_redouble`: the :class:`RedoubleBid`'s player
        iff a Redouble was played after the most recent
        :class:`DoubleBid`. ``None`` when no Redouble currently stands.
        """

        for bid in reversed(self.bids):
            if isinstance(bid, DoubleBid):
                return None
            if isinstance(bid, RedoubleBid):
                return bid.player
        return None

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

    def partner_bid(self, player: BasePlayer) -> Optional[Bid]:
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

        for bid in reversed(self.bids):
            if isinstance(bid, PassBid):
                continue
            if bid.player is not player and bid.player.team is player.team:
                return bid
        return None

    # ------------------------------------------------------------------
    # Rule helpers (private)
    # ------------------------------------------------------------------

    def _is_contract_legal(self, bid: ContractBid) -> bool:
        """A new :class:`ContractBid` must strictly outrank the prior
        numeric contract, and the auction must not be frozen by a
        :class:`DoubleBid` or :class:`RedoubleBid`.

        A *double* freezes the auction at the current contract — no 
        more numeric bids are legal until the auction completes.

        Contract legality is a function of the bid's *value* alone —
        the suit never matters to precedence or to the freeze rule.
        This method is a thin wrapper around
        :meth:`_is_contract_value_legal`; suit-agnostic callers (notably
        :meth:`legal_actions`) should use that helper directly to avoid
        re-asking the same question for each suit.
        """

        return self._is_contract_value_legal(bid.value)

    def _is_contract_value_legal(self, value: int | str) -> bool:
        """Whether a contract at ``value`` would be legal regardless of suit.

        Factored out of :meth:`_is_contract_legal` so :meth:`legal_actions`
        can enumerate suits without re-running an identical legality
        probe six times per value. The split also documents the rule:
        contract legality in contrée depends on value precedence and the
        Double/Redouble freeze state, never on the suit announced.

        Args:
            value: A numeric step (80, 90, …, 180) or the ``"Slam"`` /
                ``"SoloSlam"`` sentinels. No validation is performed
                here — ``ContractBid.__post_init__`` enforces the
                domain of valid values.

        Returns:
            ``True`` if a :class:`ContractBid` at ``value`` (with any
            suit) would be a legal next action, ``False`` otherwise.
        """

        last_contract_bid = None
        for prev in reversed(self.bids):
            if isinstance(prev, ContractBid):
                last_contract_bid = prev
                break
            if isinstance(prev, (DoubleBid, RedoubleBid)):
                # Auction is frozen; new numeric bids cannot reopen it.
                return False
        if last_contract_bid is None:
            return True
        # Once a Slam or SoloSlam has been announced, no further contract
        # bid is legal. This is asymmetric for the Slam → SoloSlam
        # progression: Slam (500) blocks SoloSlam (1000) even though the
        # latter outranks it numerically, per the user-confirmed rule.
        if last_contract_bid.value in ("Slam", "SoloSlam"):
            return False
        # Slam and SoloSlam outrank every numeric contract (80–180).
        if value in ("Slam", "SoloSlam"):
            return True
        return value > last_contract_bid.value

    def _is_double_legal(self, bid: DoubleBid) -> bool:
        """A :class:`DoubleBid` requires a live :class:`ContractBid`
        by the opposing team and no prior :class:`DoubleBid` /
        :class:`RedoubleBid`.

        Intervening passes since the contract bid do **not** close the
        Coinche window — opposing players may come back and Coinche at
        any point before the auction ends. The auction's natural
        terminator (three consecutive passes after a non-pass bid)
        closes the window on its own.
        """

        if not self.bids:
            return False
        last_contract_bid = None
        for prev in reversed(self.bids):
            if isinstance(prev, ContractBid):
                last_contract_bid = prev
                break
            elif isinstance(prev, (DoubleBid, RedoubleBid)):
                return False
        if last_contract_bid is None:
            return False
        if last_contract_bid.player.team is bid.player.team:
            return False
        return True

    def _is_redouble_legal(self, bid: RedoubleBid) -> bool:
        """A :class:`RedoubleBid` requires a live :class:`DoubleBid`
        against the bidder's team and no prior :class:`RedoubleBid`.

        Symmetrically with :meth:`_is_double_legal`, intervening passes
        between the Double and the Redouble do **not** close the
        Surcoinche window — the contracting team may come back and
        Surcoinche at any point before the auction's three-consecutive-
        passes terminator fires.
        """

        if not self.bids:
            return False
        contract_player = None
        has_double = False
        has_redouble = False
        for prev in reversed(self.bids):
            if isinstance(prev, RedoubleBid):
                has_redouble = True
                break
            elif isinstance(prev, DoubleBid):
                has_double = True
            elif isinstance(prev, ContractBid):
                contract_player = prev.player
                break
        if not has_double or has_redouble or contract_player is None:
            return False
        if contract_player.team is not bid.player.team:
            return False
        return True
