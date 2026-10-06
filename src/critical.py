try:
    from models import MoveAnalysis
except ImportError:
    from src.models import MoveAnalysis


def classify_move(centipawn_loss):

    if centipawn_loss is None:
        return "unknown"

    if centipawn_loss < 30:
        return "good"

    if centipawn_loss < 75:
        return "inaccuracy"

    if centipawn_loss < 150:
        return "mistake"

    if centipawn_loss < 250:
        return "serious_mistake"

    return "blunder"


def find_critical_positions(
    analyses,
    minimum_cpl=75
):

    critical = []

    for analysis in analyses:

        if (
            analysis.centipawn_loss is not None
            and analysis.centipawn_loss >= minimum_cpl
        ):

            critical.append(
                analysis
            )

    return critical