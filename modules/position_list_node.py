import os
import logging
from collections import namedtuple

import chess

from modules.bcolors import bcolors
from modules.candidate_moves import ambiguous
from modules.analysis import engine
from modules.utils import material_difference, material_count, fullmove_string, normalize_score


CandidateMove = namedtuple("CandidateMove", ["move_uci", "move_san", "evaluation"])

class PositionListNode(object):
    """ Linked list node of positions within a puzzle
    """
    def __init__(self, position, initial_move, player_turn=True, strict=True):
        """ initial_move - the move leading into the position to evaluate
            position - chess.Board instance. The position being evaluated
        """
        self.initial_position = position.copy()
        self.initial_move = initial_move
        self.position = position.copy()
        self.position.push(initial_move)
        self.player_turn = player_turn
        self.best_move = None
        self.evaluation = None
        self.next_position = None  # PositionListNode
        self.candidate_moves = [] # List<chess.uci.Score>
        self.strict = strict

    def move_list(self):
        """ Returns a list of UCI moves starting from this position list node
        """
        if self.next_position is None or self.next_position.ambiguous() or self.next_position.position.is_game_over():
            if self.best_move is not None:
                return [self.best_move.bestmove.uci()]
            else:
                return []
        else:
            return [self.best_move.bestmove.uci()] + self.next_position.move_list()

    def category(self):
        if self.next_position:
            return self.next_position.category()
        elif self.position.is_game_over():
            return 'Mate'
        else:
            return 'Material'

    def generate(self, depth):
        """ Generates the next position in the position list if the current
            position does not have an ambiguous next move
        """
        self._log_position()
        if self.position.legal_moves.count() == 0:
            logging.debug(bcolors.YELLOW + "Not going deeper: no legal moves" + bcolors.ENDC)
            return
        has_best = self.evaluate_best(depth)
        if not has_best:
            logging.debug(bcolors.YELLOW + "Not going deeper: game over" + bcolors.ENDC)
            return
        self.evaluate_candidate_moves(depth)
        if not self.player_turn or (has_best and not self.ambiguous() and not self.game_over()):
            logging.debug(bcolors.DIM + "Going deeper...\n" + bcolors.ENDC)
            self.next_position.generate(depth)
        else:
            log_str = "Not going deeper: "
            if self.ambiguous():
                log_str += "ambiguous"
            elif self.game_over():
                log_str += "game over"
            logging.debug(bcolors.YELLOW + log_str + bcolors.ENDC)

    def _log_position(self):
        move_san = self.initial_position.san(self.initial_move)
        logging.debug(bcolors.BLUE + ("After %s %s" % (fullmove_string(self.initial_position).strip(), move_san)))
        logging.debug(bcolors.BLUE + self.position.fen())
        logging.debug(bcolors.YELLOW + str(self.position) + bcolors.ENDC)
        logging.debug(bcolors.BLUE + ('Material difference:  %d' % self.material_difference()))
        logging.debug(bcolors.BLUE + ("# legal moves:        %d" % self.position.legal_moves.count()) + bcolors.ENDC)

    def _log_move(self, move, score):
        board = self.position
        move_san = board.san(move)
        log_str = bcolors.GREEN
        log_str += ("%s%s (%s)" % (fullmove_string(board), move_san, move.uci())).ljust(22)
        log_str += bcolors.BLUE
        score = normalize_score(self.position, score)
        if score.mate is not None:
            log_str += "   Mate: %d" % score.mate
        else:
            log_str += "   CP: %d" % score.cp
        logging.debug(log_str + bcolors.ENDC)

    def evaluate_best(self, depth):
        logging.debug(bcolors.DIM + ("Evaluating best move (depth %d)..." % depth) + bcolors.ENDC)
        engine.position(self.position)
        self.best_move = engine.go(depth=depth)
        if self.best_move.bestmove is not None:
            self.evaluation = engine.info_handlers[0].info["score"][1]
            self.next_position = PositionListNode(
                self.position.copy(),
                self.best_move.bestmove,
                player_turn=not self.player_turn,
                strict=self.strict
            )
            self._log_move(self.best_move.bestmove, self.evaluation)
            return True
        else:
            logging.debug(bcolors.RED + "No best move!" + bcolors.ENDC)
            return False

    # Analyze the best possible moves from the current position
    def evaluate_candidate_moves(self, depth):
        multipv = min(3, self.position.legal_moves.count())
        if multipv == 0:
            return
        logging.debug(bcolors.DIM + ("Evaluating best %d moves (depth %d)..." % (multipv, depth)) + bcolors.ENDC)
        engine.setoption({ "MultiPV": multipv })
        engine.position(self.position)
        engine.go(depth=depth)
        info = engine.info_handlers[0].info
        for i in range(multipv):
            move = info["pv"].get(i + 1)[0]
            evaluation = info["score"].get(i + 1)
            self._log_move(move, evaluation)
            self.candidate_moves.append(
                CandidateMove(move.uci(), self.position.san(move), evaluation)
            )
        engine.setoption({ "MultiPV": 1 })

    def material_difference(self):
        return material_difference(self.position)

    def is_complete(self, category, white_to_move, initial_material_diff):
        if self.next_position:
            if category == 'Mate' and not self.ambiguous():
                return self.next_position.is_complete(category, white_to_move, initial_material_diff)
            elif category == 'Material' and self.next_position.next_position:
                return self.next_position.is_complete(category, white_to_move, initial_material_diff)

        # if the position was converted into a material advantage
        if category == 'Material':
            num_pieces = material_count(self.position)
            final_material_change = abs(self.material_difference() - initial_material_diff)
            if white_to_move:
                if (self.material_difference() > 0.2 
                    and final_material_change > 0.1
                    and initial_material_diff < 2
                    and self.evaluation.mate is None
                    and num_pieces > 6):
                    return True
                else:
                    return False
            else:
                if (self.material_difference() < -0.2 
                    and final_material_change > 0.1
                    and initial_material_diff > -2
                    and self.evaluation.mate is None
                    and num_pieces > 6):
                    return True
                else:
                    return False
        else:
            # mate puzzles are only complete at checkmate
            # return self.position.is_game_over() and num_pieces > 6:
            return self.position.is_game_over()

    def ambiguous(self):
        """ True if it's unclear whether there's a single best player move
        """
        return ambiguous([move.evaluation for move in self.candidate_moves])

    def game_over(self):
        return self.position.is_game_over() or self.next_position.position.is_game_over()
