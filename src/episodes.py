from models import MoveAnalysis


def group_critical_positions(
    analyses,
    minimum_cpl=75,
    max_gap=2
):

    episodes = []

    current_episode = []

    last_critical_index = None

    for index, analysis in enumerate(analyses):

        is_critical = (
            analysis.centipawn_loss is not None
            and analysis.centipawn_loss >= minimum_cpl
        )

        if is_critical:

            if (
                last_critical_index is None
                or index - last_critical_index
                <= max_gap
            ):

                current_episode.append(
                    analysis
                )

            else:

                if current_episode:
                    episodes.append(
                        current_episode
                    )

                current_episode = [
                    analysis
                ]

            last_critical_index = index

    if current_episode:
        episodes.append(
            current_episode
        )

    return episodes