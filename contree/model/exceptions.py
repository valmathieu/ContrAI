# Custom exceptions for the Contrée game.

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
