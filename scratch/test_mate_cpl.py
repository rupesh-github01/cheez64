"""
Scratch script to test calculate_centipawn_loss edge cases.
"""
from src.analyzer import calculate_centipawn_loss, MATE_THRESHOLD, MAX_CPL

test_cases = [
    # Normal positions
    ("Normal equal", 0, -20, 20),
    ("Normal slight loss", 100, 30, 70),
    ("Normal blunder", 200, -300, 500),
    ("Normal gain (no loss)", -50, 100, 0),

    # Winning mate maintained
    ("Winning mate in 1 to mate in 2", 99999, 99998, 0),
    ("Winning mate in 1 to mate in 10", 99999, 99990, 0),

    # Losing mate already
    ("Losing mate in 1 to mate in 1", -99999, -99999, 0),
    ("Losing mate in 5 to mate in 1", -99995, -99999, 0),
    ("Losing mate in 1 to mate in 5 (staved off)", -99999, -99995, 0),

    # Throwing away mate
    ("Threw away mate to +500", 99995, 500, MAX_CPL),
    ("Threw away mate to 0", 99995, 0, MAX_CPL),
    ("Threw away mate to -500", 99995, -500, MAX_CPL),

    # Blundering into mate
    ("From +100 to mated in 1", 100, -99999, MAX_CPL),
    ("From 0 to mated in 5", 0, -99995, MAX_CPL),
    ("From -500 to mated in 2", -500, -99998, MAX_CPL),

    # Escape from mate
    ("Was in mate in 2, opponent missed it, now +200", -99998, 200, 0),
    ("Was in mate in 2, now -500", -99998, -500, 0),
]

print(f"{'Description':<45} | {'Before':<7} | {'After':<7} | {'Expected':<8} | {'Actual':<8} | {'Match'}")
print("-" * 90)
for desc, b, a, exp in test_cases:
    actual = calculate_centipawn_loss(b, a)
    match = actual == exp
    print(f"{desc:<45} | {b:<7} | {a:<7} | {exp:<8} | {actual:<8} | {match}")
