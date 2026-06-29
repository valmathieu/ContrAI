# Card-legality rules — the pure oracle for "what may this player play?"
# and its companion "why was that card illegal?" classifier.
#
# Both read only ``(player, contract, current_trick)`` and mutate
# nothing. They are co-located here because the classifier's branch order
# must mirror the oracle's, card-for-card, until a future ``Ruleset``
# unifies the two into a single rule-walk (CLAUDE.md §10). Keeping them
# side by side makes that coupling visible and maintainable. The rules
# come from contree-domain.md §6.2-§6.3.

from typing import TYPE_CHECKING

from contrai_core.exceptions import PlayRuleViolation

if TYPE_CHECKING:
    from contrai_core.contract import Contract
    from contrai_core.trick import Trick
    from ..player import Player


def get_playable_cards(player: 'Player', contract, current_trick):
    """
    Determine which cards a player can legally play.

    Implements the rules from contree-domain.md §6.2-§6.3:
        1. Follow the led suit if you can.
        2. When trump is led, you must additionally over-trump if you
           hold a higher trump than the highest already on the table
           (§6.3).
        3. When you cannot follow suit and your partner is *not*
           currently master of the trick, you must trump. If an
           opponent has already trumped, you must over-trump if able;
           otherwise play any trump.
        4. Partner-master exception: if your partner is currently
           winning the trick, you may discard freely — no obligation
           to trump or over-trump (§6.2 rule 4).
        5. Otherwise (no trump in your hand, or no trump suit) any
           card may be discarded.

    Args:
        player: The player whose playable cards we want to determine.
        contract: The established :class:`Contract` (provides the trump
            suit), or ``None`` before one is set.
        current_trick: The partial :class:`Trick` in progress, or
            ``None`` when the player leads.

    Returns:
        list: List of cards that can be legally played.
    """
    if not player.hand:
        return []

    trump_suit = contract.suit if contract else None
    if not current_trick or not hasattr(current_trick, 'get_plays'):
        return player.hand.copy()

    plays = current_trick.get_plays()
    if not plays:
        # First to play in this trick — anything goes.
        return player.hand.copy()

    lead_suit = plays[0][1].suit
    lead_suit_cards = player.hand.cards_of_suit(lead_suit)
    trump_cards = (
        player.hand.cards_of_suit(trump_suit) if trump_suit else []
    )

    # Rule 1 — follow suit. Special-case rule 2 (over-trump when trump
    # is led): the player MUST go higher than the best trump on the
    # table if they hold one; only fall back to lower trumps when no
    # higher trump exists.
    if lead_suit_cards:
        if trump_suit and lead_suit == trump_suit:
            higher = _higher_trumps_than_played(lead_suit_cards, plays, trump_suit)
            return higher if higher else lead_suit_cards
        return lead_suit_cards

    # Rule 4 — partner-master exemption per contree-domain.md §6.2
    # rule 4. The exemption applies only when the partner is
    # *currently winning* the partial trick, not just whoever led:
    # a partner who has since been over-trumped by an opponent no
    # longer protects you from the trump obligation.
    current_master = current_trick.get_current_winner(trump_suit)
    if current_master is not None and current_master.team == player.team:
        return player.hand.copy()

    # No trump suit, or led suit is trump (and we have none — already
    # handled above when we have some): nothing to over-trump, free discard.
    if not trump_suit or lead_suit == trump_suit:
        return player.hand.copy()

    # Trump obligations apply. If any opponent trumped, must beat them.
    highest_opponent_trump = _highest_opponent_trump(plays, player.team, trump_suit)
    if highest_opponent_trump is not None:
        higher_trumps = [
            card for card in trump_cards
            if card.get_order(trump_suit) > highest_opponent_trump.get_order(trump_suit)
        ]
        if higher_trumps:
            return higher_trumps
        if trump_cards:
            return trump_cards
        return player.hand.copy()

    # No opponent trump yet but partner is not master either → must
    # trump if able.
    if trump_cards:
        return trump_cards
    return player.hand.copy()


def classify_play_violation(
    player: 'Player', card, contract, current_trick
) -> PlayRuleViolation:
    """Classify *why* an in-hand card is illegal for ``player`` to play.

    Called only when ``card`` is genuinely illegal — held in hand but
    absent from :func:`get_playable_cards`'s legal set, with the current
    trick already holding at least one play. The branch order mirrors
    :func:`get_playable_cards` and **must stay in sync** with it
    until the deferred ``Ruleset`` unifies the two (CLAUDE.md §10).

    Args:
        player: The player whose illegal play we are explaining.
        card: The illegal card they attempted to play.
        contract: The established :class:`Contract` (provides the trump
            suit), or ``None``.
        current_trick: The partial :class:`Trick` in progress.

    Returns:
        The :class:`PlayRuleViolation` describing the broken
        obligation.
    """
    trump_suit = contract.suit if contract else None
    plays = current_trick.get_plays()
    lead_suit = plays[0][1].suit
    lead_suit_cards = player.hand.cards_of_suit(lead_suit)

    # Rule 1/2 — held the led suit. Trump led + a too-low trump is an
    # over-trump failure; anything else off-suit is a follow failure.
    if lead_suit_cards:
        if trump_suit and lead_suit == trump_suit and card.suit == trump_suit:
            return PlayRuleViolation.MUST_OVERTRUMP
        return PlayRuleViolation.MUST_FOLLOW_SUIT

    # Void in the led suit (partner-master plays are legal, so never
    # reach here). An opponent already trumped and we under-trumped →
    # over-trump failure; otherwise we discarded instead of trumping.
    highest_opponent_trump = _highest_opponent_trump(
        plays, player.team, trump_suit
    )
    if highest_opponent_trump is not None and card.suit == trump_suit:
        return PlayRuleViolation.MUST_OVERTRUMP
    return PlayRuleViolation.MUST_TRUMP


def _higher_trumps_than_played(trumps_in_hand, plays, trump_suit):
    """Return the subset of *trumps_in_hand* that beat every trump in *plays*.

    Used by the over-trump rule when the led suit is itself trump.
    Returns an empty list if no trump has been played to the trick
    yet (logically impossible here, but kept defensive) or if no
    trump in hand beats the current best.
    """
    best_so_far = None
    for _, card in plays:
        if card.suit != trump_suit:
            continue
        if best_so_far is None or card.get_order(trump_suit) > best_so_far.get_order(trump_suit):
            best_so_far = card
    if best_so_far is None:
        return []
    return [
        c for c in trumps_in_hand
        if c.get_order(trump_suit) > best_so_far.get_order(trump_suit)
    ]


def _highest_opponent_trump(plays, player_team, trump_suit):
    """Return the highest trump played by an opponent of *player_team*, or None."""
    highest = None
    for trick_player, card in plays:
        if card.suit != trump_suit or trick_player.team == player_team:
            continue
        if highest is None or card.get_order(trump_suit) > highest.get_order(trump_suit):
            highest = card
    return highest
