from __future__ import annotations

import random

TLDS = ("top", "sbs")

PREFIXES = (
    "backend",
    "back",
    "dev",
    "api",
    "srv",
    "app",
    "node",
    "edge",
    "gate",
    "core",
    "web",
    "svc",
    "host",
    "cdn",
)


def generate_candidate(rng: random.Random | None = None) -> str:
    """Правдоподобное имя только в .top или .sbs: backend42.top, dev7back.sbs."""
    rng = rng or random.Random()
    tld = rng.choice(TLDS)
    left = rng.choice(PREFIXES)
    digits = str(rng.randint(1, 999))
    style = rng.randrange(3)
    if style == 0:
        label = f"{left}{digits}"
    elif style == 1:
        right = rng.choice(PREFIXES)
        label = f"{left}{digits}{right}"
    else:
        right = rng.choice(PREFIXES)
        label = f"{left}{right}{digits}"
    return f"{label}.{tld}"


def generate_candidates(count: int, rng: random.Random | None = None, *, avoid: set[str] | None = None) -> list[str]:
    rng = rng or random.Random()
    seen = set(avoid or ())
    out: list[str] = []
    guard = 0
    while len(out) < count and guard < count * 20:
        guard += 1
        name = generate_candidate(rng)
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
