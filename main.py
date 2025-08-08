import copy
from contree.controller.game_controller import GameController
from contree.model import Player
#from contree.view.cli_view import CliView
from contree.model.game import Game
from contree.model.player import HumanPlayer


def main():
    #view = CliView()
    player1 = HumanPlayer('Player1','North')
    player2 = HumanPlayer('Player2','South')
    player3 = HumanPlayer('Player3','East')
    player4 = HumanPlayer('Player4','West')
    players = [player1, player2, player3, player4]
    game = Game(players)
    game.start_new_round()
    a=1
    #controller = GameController(game, view)
    #controller.run_game()

if __name__ == "__main__":
    main()