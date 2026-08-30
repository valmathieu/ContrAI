"""Tests for the ``Game`` orchestrator.

Covers construction (player count/position guards, team formation,
seat sorting), anticlockwise dealer rotation, playing-order derivation,
round start (shuffle-then-cut policy, dealing), game-over detection,
and the ``manage_round`` lifecycle driven through a ``Round`` double
(completed contract, score accumulation, all-pass redeal).
"""

import logging
import random

import pytest
from contrai_engine.debug_state import round_result_lines
from contrai_engine.model import game as game_module
from contrai_engine.model.game import Game
from contrai_engine.model.player import (
    AiPlayer,
    BidDecision,
    CardDecision,
    Rationale,
)
from contrai_engine.model.round import Mark, RoundScore
from contrai_core.deck import Deck
from contrai_core.team_side import TeamSide
from contrai_core.exceptions import InvalidPlayerCountError
from contrai_core.bid import ContractBid, PassBid
from contrai_core.card import Card
from contrai_core.contract import Contract
from contrai_core.hand import Hand
from contrai_core.position import Position
from contrai_core.rule_config import RuleConfig, TurnDirection
from contrai_core.types import Suit

#: The rationale the scripted AI doubles below attach. ``Round`` unwraps
#: the decision; what a stub says about its reasoning is irrelevant here.
_STUB = Rationale("stub", "test double")


class DummyPlayer:
    """Minimal player stand-in: name, seat position, and an empty hand.

    The seat defaults to ``None`` exactly as ``BasePlayer``'s does, so an
    unseated roster can be built here too.
    """

    def __init__(self, name, position=None):
        self.name = name
        self.position = position
        self.hand = Hand()


class FakeRound:
    """Test double standing in for :class:`Round` in ``manage_round`` tests.

    The real ``Round`` runs a full bidding/trick/scoring lifecycle that needs
    AI players with hands. ``Game.manage_round`` only orchestrates those calls,
    so we swap in this double to drive the orchestration deterministically and
    record which lifecycle hooks fired.
    """

    # Per-test configuration: what bidding resolves to and the scores each
    # outcome reports back to the Game.
    bidding_contract = None
    play_scores: dict[str, int] = {}
    failed_scores: dict[str, int] = {}
    contract_made = True
    #: The belote the scripted round credits each side, which ``Game``
    #: lifts off ``round_score`` to feed its win gate.
    play_belote: dict[TeamSide, int] = {}

    def __init__(self, players_order, dealer, deck, round_number, rules=None):
        self.players_order = players_order
        self.dealer = dealer
        self.deck = deck
        self.round_number = round_number
        # The real Round takes the game's RuleConfig; record it so the
        # threading test can assert the Game handed its own down.
        self.rules = rules
        self.calls: list[str] = []
        # Mirrors the attributes the real Round exposes once bidding/scoring
        # runs, so debug_state.round_result_lines (read by Game's debug
        # logging) has something valid to read off this double too.
        self.contract = None
        self.round_scores: dict[str, int] = {}
        self.round_score: RoundScore | None = None

    def deal_cards(self):
        """Record the call; no cards actually move."""
        self.calls.append("deal_cards")

    def manage_bidding(self, view=None):
        """Record the call, resolve to the scripted contract, and mirror it
        onto ``self.contract`` the way the real ``Round`` does."""
        self.calls.append("manage_bidding")
        self.contract = self.bidding_contract
        return self.bidding_contract

    def play_all_tricks(self, view=None):
        """Record the call; no tricks are actually played."""
        self.calls.append("play_all_tricks")
        return {}

    def calculate_round_scores(self):
        """Record the call and report the scripted completed-round scores,
        publishing a ``RoundScore`` and mirroring its totals onto
        ``self.round_scores`` the way the real ``Round`` does."""
        self.calls.append("calculate_round_scores")
        self.round_score = RoundScore(
            scores=dict(self.play_scores),
            contract_made=self.contract_made,
            unannounced_slam=None,
            marks={side: Mark(0, 0) for side in TeamSide},
            belote_points={
                side: self.play_belote.get(side, 0) for side in TeamSide
            },
            card_points={side: 0 for side in TeamSide},
            last_trick_side=None,
            multiplier=1,
        )
        self.round_scores = dict(self.play_scores)
        return dict(self.play_scores)

    def handle_failed_contract(self):
        """Record the call and report the scripted all-pass scores.

        Publishes the contractless ``RoundScore`` the real ``Round``
        publishes — zero belote, ``contract_made`` left ``None``.
        """
        self.calls.append("handle_failed_contract")
        self.round_score = RoundScore(
            scores=dict(self.failed_scores),
            contract_made=None,
            unannounced_slam=None,
            marks={side: Mark(0, 0) for side in TeamSide},
            belote_points={side: 0 for side in TeamSide},
            card_points={side: 0 for side in TeamSide},
            last_trick_side=None,
            multiplier=1,
        )
        return dict(self.failed_scores)


class RecordingView:
    """View double recording the lifecycle callbacks ``manage_round`` fires."""

    def __init__(self):
        self.dealt = []
        self.redeal_count = 0

    def on_round_dealt(self, round_obj):
        """Record which round object the deal callback announced."""
        self.dealt.append(round_obj)

    def on_all_pass_redeal(self):
        """Count how many times the redeal callback fired."""
        self.redeal_count += 1

@pytest.fixture
def players():
    """
    Fixture that returns 4 positioned players for testing.
    """
    return [
        DummyPlayer("North Player", Position.NORTH),
        DummyPlayer("East Player", Position.EAST),
        DummyPlayer("South Player", Position.SOUTH),
        DummyPlayer("West Player", Position.WEST)
    ]

@pytest.fixture
def game(players):
    """
    Fixture that returns a Game instance with 4 players.
    """
    return Game(players) # type: ignore

@pytest.fixture
def game_to_1500(players):
    """A game played to 1500 — the target the score tests below are written
    against, now that the ruleset's own default is §9.1's 2000.
    """
    return Game(players, rules=RuleConfig(target_score=1500))  # type: ignore

def test_game_initialization(game, players):
    """
    Test that a game is correctly initialized with players, teams, and initial state.
    """
    assert len(game.players) == 4
    assert len(game.teams) == 2
    assert game.round_number == 0
    assert game.dealer is None
    assert game.current_contract is None
    assert game.deck is not None
    
    # Check team formation
    team_names = {team.name for team in game.teams}
    assert team_names == {"East-West", "North-South"}

def test_game_requires_exactly_four_players():
    """
    Test that creating a game with wrong number of players raises InvalidPlayerCountError.
    """
    # Test with too few players
    players = [DummyPlayer("Player1", Position.NORTH)]
    with pytest.raises(InvalidPlayerCountError):
        Game(players) # type: ignore

    # Test with too many players
    players = [DummyPlayer(f"Player{i}", Position.NORTH) for i in range(5)]
    with pytest.raises(InvalidPlayerCountError):
        Game(players) # type: ignore

def test_game_requires_correct_positions():
    """
    Test that creating a game without all required positions raises ValueError.
    """
    # Missing West position
    players = [
        DummyPlayer("Player1", Position.NORTH),
        DummyPlayer("Player2", Position.EAST),
        DummyPlayer("Player3", Position.SOUTH),
        DummyPlayer("Player4", Position.SOUTH)  # Duplicate South, missing West
    ]
    
    with pytest.raises(ValueError, match="Players must have positions"):
        Game(players) # type: ignore

def test_players_are_sorted_by_position(players):
    """
    Test that players are sorted in the correct position order: North, West, South, East.
    """
    # Shuffle players to test sorting
    shuffled_players = [players[2], players[0], players[3], players[1]]  # Different order
    game = Game(shuffled_players) # type: ignore
    
    expected_positions = list(Position)
    actual_positions = [player.position for player in game.players]
    assert actual_positions == expected_positions

def test_unseated_players_are_seated_in_list_order():
    """Four players naming no seat are seated from the list order.

    The order is ``list(Position)`` — the anticlockwise turn order, not
    the compass order — so the list reads as "these four sit down around
    the table in this order".
    """
    players = [DummyPlayer(f"Player{i}") for i in range(4)]
    game = Game(players)  # type: ignore

    assert [p.position for p in players] == list(Position)
    # And the Game agrees: its seat index maps each seat to that player.
    assert [game.players_by_position[seat].name for seat in Position] == [
        "Player0", "Player1", "Player2", "Player3"
    ]


def test_unseated_seating_partners_the_first_and_third_players():
    """List-order seating makes players 0/2 partners, and 1/3.

    A consequence of seating in turn order rather than compass order, and
    the reason the ordering is spelled out rather than left implicit: a
    caller reading N-E-S-W into the list would get the partnerships it
    did not ask for.
    """
    players = [DummyPlayer(f"Player{i}") for i in range(4)]
    game = Game(players)  # type: ignore

    rosters = {
        frozenset(player.name for player in team.players) for team in game.teams
    }
    assert rosters == {
        frozenset({"Player0", "Player2"}),
        frozenset({"Player1", "Player3"}),
    }


def test_half_seated_roster_is_rejected():
    """A list mixing seated and unseated players raises.

    Completing the gaps would decide partnerships the caller only
    specified half of, so the ambiguity is refused outright.
    """
    players = [
        DummyPlayer("Seated", Position.NORTH),
        DummyPlayer("Unseated1"),
        DummyPlayer("Unseated2"),
        DummyPlayer("Unseated3"),
    ]

    with pytest.raises(ValueError, match="all have a position or all have none"):
        Game(players)  # type: ignore


def test_unseated_ai_players_build_a_playable_game():
    """The real engine player type seats itself too.

    ``AiPlayer`` is what a simulation or training harness constructs, and
    its strategies read the seat off the player at decision time — so
    seating assigned by ``Game`` after construction is what they see.
    """
    players = [AiPlayer(f"Bot{i}") for i in range(4)]
    game = Game(players)

    assert {p.position for p in game.players} == set(Position)
    assert all(p.team is not None for p in game.players)
    assert game.players[0].cardplay.position is game.players[0].position


def test_teams_are_created_correctly(game):
    """
    Test that teams are correctly formed with North-South and East-West partnerships.
    """
    ns_team = next(team for team in game.teams if team.name == "North-South")
    ew_team = next(team for team in game.teams if team.name == "East-West")

    # Check North-South team
    ns_positions = {player.position for player in ns_team.players}
    assert ns_positions == {Position.NORTH, Position.SOUTH}

    # Check East-West team
    ew_positions = {player.position for player in ew_team.players}
    assert ew_positions == {Position.EAST, Position.WEST}

def test_players_by_position_maps_each_seat_to_its_player(game, players):
    """
    Test that players_by_position exposes an O(1) lookup from each seat
    to the player occupying it.
    """
    assert game.players_by_position == {
        Position.NORTH: players[0],
        Position.EAST: players[1],
        Position.SOUTH: players[2],
        Position.WEST: players[3],
    }

def test_next_dealer_anticlockwise_rotation(game):
    """
    Test that dealer rotation follows anticlockwise order: North → West → South → East.
    """
    # Set initial dealer manually to North
    game.dealer = game.players[0]  # North (index 0)
    assert game.dealer.position == Position.NORTH

    # Test the rotation sequence
    expected_sequence = [Position.WEST, Position.SOUTH, Position.EAST, Position.NORTH]
    
    for expected_position in expected_sequence:
        game.next_dealer()
        assert game.dealer.position == expected_position

def test_start_new_round_increments_round_number(game):
    """
    Test that starting a new round increments the round number.
    """
    assert game.round_number == 0
    
    game.start_new_round()
    assert game.round_number == 1

    # Reset deck for next round
    game.deck = Deck()
    
    game.start_new_round()
    assert game.round_number == 2

def test_start_new_round_sets_dealer_if_none(game):
    """
    Test that starting the first round sets a dealer if none exists.
    """
    assert game.dealer is None
    
    game.start_new_round()
    assert game.dealer in game.players

def test_start_new_round_deals_cards(game):
    """
    Test that starting a new round deals cards to all players.
    """
    game.start_new_round()

    # Each player should have 8 cards
    for player in game.players:
        assert len(player.hand) == 8

    # Deck should be empty after dealing
    assert game.deck.is_empty()

def test_game_defaults_to_the_classic_ruleset(game):
    """
    Test that a Game built without a ruleset plays the §9 defaults.
    """
    assert game.rules == RuleConfig()

def test_game_records_an_explicit_ruleset(players):
    """
    Test that an explicit RuleConfig is kept as-is, not copied.
    """
    rules = RuleConfig(target_score=1000)
    assert Game(players, rules=rules).rules is rules  # type: ignore

def test_start_new_round_hands_the_ruleset_to_the_round(players, monkeypatch):
    """
    Test that the Game's ruleset reaches the Round it builds.
    """
    rules = RuleConfig(target_score=1000)
    game = Game(players, rules=rules)  # type: ignore
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    game.start_new_round()

    assert game.current_round.rules is rules

def test_check_game_over_not_finished(game_to_1500):
    """
    Test check_game_over when no team has reached target score.
    """
    game_to_1500.scores = {TeamSide.NS: 1200, TeamSide.EW: 800}

    result = game_to_1500.check_game_over()

    assert result.game_over is False
    assert result.winner is None
    assert result.tied_teams is None
    assert result.final_scores == {TeamSide.NS: 1200, TeamSide.EW: 800}

def test_check_game_over_winner(game_to_1500):
    """
    Test check_game_over when a team has won.
    """
    game_to_1500.scores = {TeamSide.NS: 1600, TeamSide.EW: 1200}

    result = game_to_1500.check_game_over()

    assert result.game_over is True
    assert result.winner == TeamSide.NS
    assert result.tied_teams is None
    assert result.final_scores == {TeamSide.NS: 1600, TeamSide.EW: 1200}

def test_check_game_over_tie_continues_game(game_to_1500):
    """
    Test that a tie at/above the target does not end the game.

    Both teams level at or above the target means sudden death: the
    game continues with tiebreaker rounds until one team leads, so
    ``game_over`` stays False while ``tied_teams`` flags the state.
    """
    game_to_1500.scores = {TeamSide.NS: 1600, TeamSide.EW: 1600}

    result = game_to_1500.check_game_over()

    assert result.game_over is False
    assert result.winner is None
    assert result.tied_teams == [TeamSide.NS, TeamSide.EW]
    assert result.final_scores == {TeamSide.NS: 1600, TeamSide.EW: 1600}


def test_check_game_over_tie_below_target_not_flagged(game_to_1500):
    """
    Test that a tie below the target is not reported as a tiebreaker.

    ``tied_teams`` only signals the sudden-death state — equal scores
    short of the target are just an unfinished game.
    """
    game_to_1500.scores = {TeamSide.NS: 1200, TeamSide.EW: 1200}

    result = game_to_1500.check_game_over()

    assert result.game_over is False
    assert result.winner is None
    assert result.tied_teams is None


def test_check_game_over_tie_resolved_by_next_round(game_to_1500):
    """
    Test that the game ends once a tiebreaker round breaks the tie.

    After sudden death, both teams sit above the target but one now
    leads — that team wins.
    """
    game_to_1500.scores = {TeamSide.NS: 1760, TeamSide.EW: 1600}

    result = game_to_1500.check_game_over()

    assert result.game_over is True
    assert result.winner == TeamSide.NS
    assert result.tied_teams is None


def test_check_game_over_reads_the_rulesets_target(players):
    game = Game(players, rules=RuleConfig(target_score=1000))  # type: ignore
    game.scores[TeamSide.NS] = 1000
    assert game.check_game_over().game_over is True


def test_check_game_over_defaults_to_two_thousand(game):
    # §9.1's default target, and no longer the view's 1500.
    assert game.rules.target_score == 2000
    game.scores[TeamSide.NS] = 1999
    assert game.check_game_over().game_over is False
    game.scores[TeamSide.NS] = 2000
    assert game.check_game_over().game_over is True


class TestWinOnBelotePointsAlone:
    """§8 — whether a Belote can carry a side past the target.

    Switched off, only the *crossing round's* Belote is discounted:
    Belote banked in earlier rounds is ordinary score by then. The gate
    is therefore a question about one round, which is why ``Game`` keeps
    ``last_round_belote`` rather than a running Belote total.
    """

    @staticmethod
    def _game(players, *, scores, last_round_belote=None, rules=None):
        """A game sitting on the given scores and last-round belote."""
        game = Game(players, rules=rules)  # type: ignore[arg-type]
        game.scores = dict(scores)
        if last_round_belote is not None:
            game.last_round_belote = {
                side: last_round_belote.get(side, 0) for side in TeamSide
            }
        return game

    def test_belote_can_cross_the_target_by_default(self, players):
        game = self._game(players,
                          scores={TeamSide.NS: 2010, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 20})
        status = game.check_game_over()
        assert status.game_over is True
        assert status.winner is TeamSide.NS

    def test_off_a_belote_only_crossing_does_not_win(self, players):
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2010, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 20})
        status = game.check_game_over()
        assert status.game_over is False
        assert status.winner is None
        assert status.tied_teams is None

    def test_off_points_from_play_confirm_the_win(self, players):
        # The same lead, but this round's belote was not what carried it.
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2010, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 0})
        assert game.check_game_over().game_over is True

    def test_off_earlier_rounds_belote_still_counts(self, players):
        # Only the crossing round's credit is discounted: a side 5 points
        # over the target with no belote this round has won, however much
        # belote it banked earlier.
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2005, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 0})
        assert game.check_game_over().game_over is True

    def test_off_four_all_trump_belotes_are_discounted_together(self, players):
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2050, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 80})
        assert game.check_game_over().game_over is False

    def test_off_the_gate_only_looks_at_the_leader(self, players):
        # The trailing side's belote is irrelevant — it is not crossing.
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2010, TeamSide.EW: 1200},
                          last_round_belote={TeamSide.NS: 0,
                                             TeamSide.EW: 80})
        assert game.check_game_over().game_over is True

    def test_a_tie_at_the_target_is_still_sudden_death(self, players):
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 2010, TeamSide.EW: 2010},
                          last_round_belote={TeamSide.NS: 20,
                                             TeamSide.EW: 20})
        status = game.check_game_over()
        assert status.game_over is False
        assert status.tied_teams == [TeamSide.NS, TeamSide.EW]

    def test_the_gate_tolerates_an_unscored_game(self, players):
        # cli.py checks before the first round is ever dealt.
        game = self._game(players,
                          rules=RuleConfig(win_on_belote_points_alone=False),
                          scores={TeamSide.NS: 0, TeamSide.EW: 0})
        assert game.check_game_over().game_over is False

    def test_a_fresh_game_starts_with_no_belote_credit(self, players):
        game = Game(players)  # type: ignore[arg-type]
        assert game.last_round_belote == {TeamSide.NS: 0, TeamSide.EW: 0}

    def test_manage_round_records_the_rounds_belote(self, game, monkeypatch):
        # The credit the gate reads is lifted off the scored round, so
        # each round replaces it rather than adding to it.
        FakeRound.bidding_contract = object()
        FakeRound.play_scores = {TeamSide.NS: 180, TeamSide.EW: 0}
        FakeRound.play_belote = {TeamSide.NS: 20}
        monkeypatch.setattr(game_module, 'Round', FakeRound)

        game.manage_round()
        assert game.last_round_belote == {TeamSide.NS: 20, TeamSide.EW: 0}

        FakeRound.play_belote = {}
        game.manage_round()
        assert game.last_round_belote == {TeamSide.NS: 0, TeamSide.EW: 0}
        assert game.scores[TeamSide.NS] == 360

    def test_an_all_pass_round_leaves_no_belote_credit(
        self, game, monkeypatch
    ):
        # A redeal never reaches ``calculate_round_scores``; the credit
        # from the previous round must not linger and gate a later win.
        FakeRound.bidding_contract = object()
        FakeRound.play_scores = {TeamSide.NS: 180, TeamSide.EW: 0}
        FakeRound.play_belote = {TeamSide.NS: 20}
        monkeypatch.setattr(game_module, 'Round', FakeRound)
        game.manage_round()
        assert game.last_round_belote[TeamSide.NS] == 20

        FakeRound.bidding_contract = None
        FakeRound.failed_scores = {TeamSide.NS: 0, TeamSide.EW: 0}
        game.manage_round()
        assert game.last_round_belote == {TeamSide.NS: 0, TeamSide.EW: 0}


def test_next_dealer_picks_random_when_none(game, monkeypatch):
    """
    Test that the first call to next_dealer picks a player at random.
    """
    assert game.dealer is None

    # Force the "random" choice to be deterministic for the assertion.
    monkeypatch.setattr(game_module.random, 'choice', lambda seq: seq[2])

    game.next_dealer()

    assert game.dealer is game.players[2]


def test_set_players_order_starts_after_dealer(game):
    """
    Test that the playing order begins with the player after the dealer and
    proceeds anticlockwise (North, West, South, East).
    """
    # Players are sorted as [North, West, South, East]; dealer is North.
    game.dealer = game.players[0]

    game.set_players_order()

    positions = [player.position for player in game.players_order]
    assert positions == [Position.WEST, Position.SOUTH, Position.EAST, Position.NORTH]


def test_set_players_order_wraps_around(game):
    """
    Test that the playing order wraps past the end of the player list when the
    dealer sits last in position order.
    """
    # Dealer is East (last in [North, West, South, East]).
    game.dealer = game.players[3]

    game.set_players_order()

    positions = [player.position for player in game.players_order]
    assert positions == [Position.NORTH, Position.WEST, Position.SOUTH, Position.EAST]


def test_start_new_round_shuffles_first_round_then_cuts(game, monkeypatch):
    """
    Test that the deck is shuffled on the first round and cut on later rounds.
    """
    calls = []
    monkeypatch.setattr(game.deck, 'shuffle', lambda: calls.append('shuffle'))
    monkeypatch.setattr(game.deck, 'cut', lambda: calls.append('cut'))
    # Swap in the round double so dealing does not exhaust the (un-shuffled) deck.
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    game.start_new_round()
    assert calls == ['shuffle']

    game.start_new_round()
    assert calls == ['shuffle', 'cut']


def test_start_new_round_reshuffles_every_round_when_asked(players, monkeypatch):
    """§9.3 — the table may shuffle before every deal instead of cutting."""
    game = Game(
        players,  # type: ignore[arg-type]
        rules=RuleConfig(reshuffle_every_round=True),
    )
    calls: list[str] = []
    monkeypatch.setattr(game.deck, "shuffle", lambda: calls.append("shuffle"))
    monkeypatch.setattr(game.deck, "cut", lambda: calls.append("cut"))
    monkeypatch.setattr(game.deck, "deal", lambda players_order: None)

    game.start_new_round()
    game.start_new_round()

    assert calls == ["shuffle", "shuffle"]


def test_manage_round_completed(game, monkeypatch):
    """
    Test the happy path of manage_round: a contract is won, per-round scores are
    accumulated into the totals, and the deal callback fires on the view.
    """
    contract = object()
    FakeRound.bidding_contract = contract
    FakeRound.play_scores = {TeamSide.NS: 160, TeamSide.EW: 0}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    view = RecordingView()
    game.manage_round(view)

    # manage_round mutates game state in place and returns nothing: the contract
    # is recorded and the round's points are folded into the running totals.
    assert game.current_contract is contract
    assert game.scores == {TeamSide.NS: 160, TeamSide.EW: 0}

    # The full lifecycle ran, in order.
    assert game.current_round.calls == [
        'deal_cards', 'manage_bidding', 'play_all_tricks', 'calculate_round_scores'
    ]
    # The view was told a fresh round was dealt, and never asked to redeal.
    assert view.dealt == [game.current_round]
    assert view.redeal_count == 0


def test_manage_round_accumulates_scores_across_rounds(game, monkeypatch):
    """
    Test that manage_round adds each round's scores onto the running totals.
    """
    FakeRound.bidding_contract = object()
    FakeRound.play_scores = {TeamSide.NS: 90, TeamSide.EW: 70}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    game.manage_round()
    assert game.scores == {TeamSide.NS: 90, TeamSide.EW: 70}

    game.manage_round()
    assert game.scores == {TeamSide.NS: 180, TeamSide.EW: 140}


def test_manage_round_all_pass_redeals(game, monkeypatch):
    """
    Test the all-pass path of manage_round: with no contract, tricks are never
    played, the failed-contract branch redistributes cards, and the redeal
    callback fires on the view.
    """
    FakeRound.bidding_contract = None
    FakeRound.failed_scores = {TeamSide.NS: 0, TeamSide.EW: 0}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    view = RecordingView()
    game.manage_round(view)

    # No contract was recorded for the passed-out round.
    assert game.current_contract is None

    # No trick play or scoring happened; the failed-contract branch ran instead.
    assert 'play_all_tricks' not in game.current_round.calls
    assert 'handle_failed_contract' in game.current_round.calls
    # The view was asked to redeal, and the totals were left untouched.
    assert view.redeal_count == 1
    assert game.scores == {TeamSide.NS: 0, TeamSide.EW: 0}


# ---------------------------------------------------------------------------
# Debug-logging diagnostics (stdlib logging, model-side)
# ---------------------------------------------------------------------------


def test_start_new_round_logs_deal_snapshot_at_debug(game, caplog):
    """
    Test that starting a new round emits a single DEBUG record holding the
    round header and every seat's freshly dealt hand.
    """
    with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
        game.start_new_round()

    game_records = [
        record for record in caplog.records
        if record.name == "contrai_engine.model.game"
    ]
    assert len(game_records) == 1
    assert game_records[0].levelno == logging.DEBUG

    message = game_records[0].getMessage()
    assert f"Round #{game.round_number} dealt" in message
    for player in game.players:
        assert f"{player.position.name[0]}: " in message


def test_start_new_round_does_not_log_without_debug_level(game, caplog):
    """
    Test that no deal-snapshot record is captured at the default log
    level. A caplog assertion only observes the emitted record, so this
    can't distinguish "the guard skipped building the snapshot" from "the
    snapshot was built but the disabled logger call no-opped it" — it
    confirms the observable, back-compat-relevant behavior: nothing is
    emitted for contrai_engine.model.game when DEBUG is not active.
    """
    game.start_new_round()

    assert not any(
        record.name == "contrai_engine.model.game" for record in caplog.records
    )


def test_manage_round_completed_logs_round_result_at_debug(
    game, players, monkeypatch, caplog
):
    """
    Test that a completed round logs its contract outcome and the running
    totals at DEBUG, after the round's points are folded into game.scores.
    """
    contract = Contract(ContractBid(players[0], 100, Suit.HEARTS))
    FakeRound.bidding_contract = contract
    FakeRound.play_scores = {TeamSide.NS: 160, TeamSide.EW: 0}
    FakeRound.contract_made = True
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
        game.manage_round()

    game_records = [
        record for record in caplog.records
        if record.name == "contrai_engine.model.game"
    ]
    result_messages = [
        record.getMessage() for record in game_records
        if record.getMessage().startswith(f"Round #{game.round_number}: contract")
    ]
    assert len(result_messages) == 1
    assert result_messages[0].splitlines() == [
        f"Round #{game.round_number}: contract 100 ♥ by N — made.",
        "Round points: NS 160 · EW 0",
        "Totals: NS 160 · EW 0",
    ]


def test_manage_round_all_pass_logs_redeal_at_debug(game, monkeypatch, caplog):
    """
    Test that an all-passed round logs the redeal outcome at DEBUG, joined
    as a single multi-line record (header line + running totals), with the
    totals left untouched.
    """
    FakeRound.bidding_contract = None
    FakeRound.failed_scores = {TeamSide.NS: 0, TeamSide.EW: 0}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
        game.manage_round()

    game_records = [
        record for record in caplog.records
        if record.name == "contrai_engine.model.game"
    ]
    result_messages = [
        record.getMessage() for record in game_records
        if "all passed" in record.getMessage()
    ]
    assert len(result_messages) == 1
    assert result_messages[0].splitlines() == [
        f"Round #{game.round_number}: all passed — redeal.",
        "Totals: NS 0 · EW 0",
    ]


def test_manage_round_completed_logs_round_result_against_a_genuine_round(
    caplog
):
    """
    Test that the round-result DEBUG log reflects a *genuine* Round's
    outcome end to end through the public ``manage_round`` API — real
    ``Auction``/``PlayState``/``score_round`` machinery, never a
    hand-scripted double — so a regression in any of those (e.g. a change
    to ``score_round``'s None-branch reachability, or to ``Contract``'s
    ``.player`` invariant) would surface here instead of being masked by
    ``FakeRound``'s fabricated ``contract_made``/``round_scores``.
    """
    north = AiPlayer("North", Position.NORTH)
    east = AiPlayer("East", Position.EAST)
    south = AiPlayer("South", Position.SOUTH)
    west = AiPlayer("West", Position.WEST)
    game = Game([north, east, south, west])

    # Real bidding: North contracts 80 Hearts, everyone else passes —
    # legal regardless of what the real shuffle actually deals, since bid
    # legality never depends on hand contents. Whoever the (randomly
    # chosen) dealer puts first in the cycle, North's single scripted
    # ContractBid fires on its own first turn and every other seat always
    # passes (scripted, then falls back to passing once its queue is
    # empty), so this always converges on "80 Hearts by North".
    scripted = {
        north: [ContractBid(north, 80, Suit.HEARTS)],
        east: [PassBid(east)],
        south: [PassBid(south)],
        west: [PassBid(west)],
    }
    for ai, choices in scripted.items():
        queue = list(choices)
        ai.choose_bid = lambda _auction, _p=ai, _q=queue: BidDecision(
            _q.pop(0) if _q else PassBid(_p), _STUB
        )
    # Real play: every seat always plays its first legal card — the same
    # deal-content-agnostic strategy
    # TestPlayThroughReachesTerminal uses in test_round.py.
    for ai in (north, east, south, west):
        ai.choose_card = lambda observation: CardDecision(
            observation.legal_cards[0], _STUB
        )

    with caplog.at_level(logging.DEBUG, logger="contrai_engine"):
        game.manage_round()

    # Sanity: the scripted bid landed a real contract and real scoring ran
    # (rather than both sides of the comparison below silently reading the
    # same un-set None).
    assert game.current_contract is not None
    assert game.current_round.contract_made is not None

    game_records = [
        record for record in caplog.records
        if record.name == "contrai_engine.model.game"
    ]
    result_messages = [
        record.getMessage() for record in game_records
        if record.getMessage().startswith(f"Round #{game.round_number}: contract")
    ]
    assert len(result_messages) == 1
    # The log must equal what the real round_result_lines projection
    # produces from this *same* genuine, just-played round and these
    # *same* running totals — proving Game._log_round_result wires the
    # real Round through rather than a stand-in.
    assert result_messages[0] == "\n".join(
        round_result_lines(game.current_round, game.scores)
    )


# ---------------------------------------------------------------------------
# §9.1 — turn direction
# ---------------------------------------------------------------------------


class TestTurnDirection:
    """§9.1 — one setting governs dealer rotation and the playing order."""

    def _game(self, players, direction):
        return Game(
            players,  # type: ignore[arg-type]
            rules=RuleConfig(turn_direction=direction),
        )

    def test_dealer_rotates_anticlockwise_by_default(self, players):
        game = self._game(players, TurnDirection.ANTICLOCKWISE)
        game.dealer = game.players_by_position[Position.NORTH]
        game.next_dealer()
        assert game.dealer.position is Position.WEST

    def test_dealer_rotates_clockwise_when_the_table_asks(self, players):
        game = self._game(players, TurnDirection.CLOCKWISE)
        game.dealer = game.players_by_position[Position.NORTH]
        game.next_dealer()
        assert game.dealer.position is Position.EAST

    def test_dealer_rotation_visits_every_seat_once_per_lap(self, players):
        for direction in TurnDirection:
            game = self._game(players, direction)
            game.dealer = game.players_by_position[Position.NORTH]
            seen = []
            for _ in range(4):
                game.next_dealer()
                seen.append(game.dealer.position)
            assert len(set(seen)) == 4
            assert seen[-1] is Position.NORTH

    def test_playing_order_starts_after_the_dealer_anticlockwise(self, players):
        game = self._game(players, TurnDirection.ANTICLOCKWISE)
        game.dealer = game.players_by_position[Position.NORTH]
        game.set_players_order()
        assert [p.position for p in game.players_order] == [
            Position.WEST, Position.SOUTH, Position.EAST, Position.NORTH
        ]

    def test_playing_order_starts_after_the_dealer_clockwise(self, players):
        game = self._game(players, TurnDirection.CLOCKWISE)
        game.dealer = game.players_by_position[Position.NORTH]
        game.set_players_order()
        assert [p.position for p in game.players_order] == [
            Position.EAST, Position.SOUTH, Position.WEST, Position.NORTH
        ]

    def test_the_dealer_speaks_last_either_way(self, players):
        for direction in TurnDirection:
            game = self._game(players, direction)
            game.dealer = game.players_by_position[Position.SOUTH]
            game.set_players_order()
            assert game.players_order[-1] is game.dealer
            assert len({p.position for p in game.players_order}) == 4

    def test_partners_still_alternate_in_a_clockwise_order(self, players):
        # Seats alternate sides around the table whichever way play runs,
        # so index 0 and index 2 of the playing order are always partners —
        # which is what ``PlayState``'s +1 stepping relies on.
        game = self._game(players, TurnDirection.CLOCKWISE)
        game.dealer = game.players_by_position[Position.NORTH]
        game.set_players_order()
        order = [p.position for p in game.players_order]
        assert order[0].is_teammate(order[2])
        assert not order[0].is_teammate(order[1])


class TestClockwiseRoundPlaysOut:
    """A clockwise table runs a full round with no rule broken."""

    def test_a_clockwise_game_plays_a_round_end_to_end(self):
        random.seed(4242)
        game = Game(
            [AiPlayer(seat.value, position=seat) for seat in Position],
            rules=RuleConfig(turn_direction=TurnDirection.CLOCKWISE),
        )
        game.manage_round()
        if game.current_contract is not None:
            state = game.current_round.play_state
            assert state.is_terminal()
            assert sum(state.card_points_by_side.values()) == 152
            # Every trick was played in the table's own direction: each
            # seat's successor within a trick is its clockwise neighbour.
            seats = [p.position for p in state.players]
            for index, seat in enumerate(seats):
                assert seats[(index + 1) % 4] is seat.next_in(
                    TurnDirection.CLOCKWISE
                )
