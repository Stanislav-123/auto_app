from __future__ import annotations

# theme_id starts at 1 (Plinko), matching ZM Portal select order.
CRM_THEMES: list[tuple[int, str]] = [
    (1, "Plinko"),
    (2, "Aviator"),
    (3, "Bonanza"),
    (4, "Wolf"),
    (5, "Slots"),
    (6, "Sugar"),
    (7, "Egypt"),
    (8, "Zeus"),
    (9, "Joker"),
    (10, "Fish"),
    (11, "Leprechaun"),
    (12, "Live"),
    (13, "Tiger"),
    (14, "Bull"),
    (15, "Muerta"),
    (16, "Monkey"),
    (17, "Betting"),
    (18, "Cards"),
    (19, "Balloons"),
    (20, "Chicken"),
    (21, "Forest"),
    (22, "Football"),
    (23, "Twist"),
    (24, "Volcano"),
    (25, "Rabbit"),
    (26, "Utility"),
    (27, "Dragon"),
    (28, "Ice fishing"),
    (29, "Coin strike"),
    (30, "Tower Rush"),
    (31, "Pengu Sport"),
    (32, "Mine"),
    (33, "Chicken [Alternative]"),
    (34, "Tappy Bird"),
    (35, "Cricket"),
    (36, "PlayMe"),
    (37, "Frog"),
    (38, "Aztec"),
    (39, "InOut Games"),
    (40, "Wheel Out"),
]

THEME_BY_ID: dict[int, str] = {theme_id: name for theme_id, name in CRM_THEMES}


def get_theme_name(theme_id: int) -> str | None:
    return THEME_BY_ID.get(theme_id)
