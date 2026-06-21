"""Per-screen rendering for the Rich terminal UI.

One module per screen of the five-screen design (landing, bidding,
mid-trick / trick-won, round recap, game-over). Each exposes pure
``(data) -> Panel/Text`` builders that ``RichView`` composes and prints;
the screens hold no state and do no I/O.
"""
