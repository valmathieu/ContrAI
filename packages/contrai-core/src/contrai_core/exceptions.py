# Custom exceptions for the contrée game.

class InvalidPlayerCountError(ValueError):
    """
    Raised when an operation requires a specific number of players but receives a different count.

    This exception is typically raised in contexts where the game rules require exactly 4 players
    (for dealing cards, forming teams, etc.) or exactly 2 players (for forming a team).
    """

    def __init__(self, expected_count, actual_count, context=""):
        """
        Initialize the InvalidPlayerCountError.

        Args:
            expected_count (int): The expected number of players
            actual_count (int): The actual number of players received
            context (str, optional): Additional context about where the error occurred
        """
        if context:
            message = f"{context}: Expected {expected_count} players, got {actual_count}"
        else:
            message = f"Expected {expected_count} players, got {actual_count}"

        super().__init__(message)
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.context = context

class InvalidCardCountError(ValueError):
    """
    Raised when an operation requires a specific number of cards but receives a different count.

    This exception is typically raised in contexts where the cards have to be dealt.
    """

    def __init__(self, expected_count, actual_count, context=""):
        """
        Initialize the InvalidCardCountError.

        Args:
            expected_count (int): The expected number of cards
            actual_count (int): The actual number of cards received
            context (str, optional): Additional context about where the error occurred
        """
        if context:
            message = f"{context}: Expected {expected_count} cards, got {actual_count}"
        else:
            message = f"Expected {expected_count} cards, got {actual_count}"

        super().__init__(message)
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.context = context


class IllegalBidError(ValueError):
    """Raised when a bid is applied to an :class:`Auction` in which it is illegal.

    The auction state machine surfaces illegal bids as a loud failure
    rather than silently downgrading them to a Pass. The offending bid
    and the prior bid history are attached so callers can render
    diagnostics — engine wiring bugs around bidding should be obvious
    rather than swallowed.
    """

    def __init__(self, bid, bids, context=""):
        """Initialize the IllegalBidError.

        Args:
            bid: The bid that was rejected. Typed as ``Any`` to avoid a
                circular import on :class:`contrai_core.bid.Bid` here.
            bids: The chronological iterable of prior bids the bid was
                applied against. Coerced to a tuple for diagnostics.
            context: Optional free-form context (e.g. originating call
                site or player position) appended to the message.
        """
        bids_tuple = tuple(bids)
        base = (
            f"Illegal bid {bid!r} for auction with "
            f"{len(bids_tuple)} prior bid(s)"
        )
        message = f"{context}: {base}" if context else base
        super().__init__(message)
        self.bid = bid
        self.bids = bids_tuple
        self.context = context
