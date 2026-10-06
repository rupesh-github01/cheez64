import chess


def compare_material(
    before,
    after
):

    before_material = before["material"]
    after_material = after["material"]

    return {
        "white": (
            after_material["white"]
            - before_material["white"]
        ),

        "black": (
            after_material["black"]
            - before_material["black"]
        ),

        "difference": (
            after_material["difference"]
            - before_material["difference"]
        ),
    }


def compare_checks(
    before,
    after
):

    return {
        "checks_before":
            before["checks_available"],

        "checks_after":
            after["checks_available"],
    }


def compare_positions(
    before,
    after
):

    return {

        "material_change":
            compare_material(
                before,
                after
            ),

        "checks":
            compare_checks(
                before,
                after
            ),

        "in_check_before":
            before["in_check"],

        "in_check_after":
            after["in_check"],
    }