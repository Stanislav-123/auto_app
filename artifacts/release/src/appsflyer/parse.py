from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

BUNDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
PARTNER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,63}$")
TIKTOK_ID_RE = re.compile(r"^\d{16,20}$")
UNITY_GAME_RE = re.compile(r"^\d{4,14}$")
IOS_DIGITS_RE = re.compile(r"^\d{9,12}$")
IOS_PREFIX_RE = re.compile(r"^id(\d{9,12})$", re.IGNORECASE)
APPLE_HOSTS = ("apps.apple.com", "itunes.apple.com")
SNAP_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_COMMAND_RE = re.compile(r"^/(share_partner|share_tiktok|share_unity|share_snapchat)(?:@\w+)?(?:\s+(.*))?$", re.I)


class ParseError(Exception):
    """Кривой аргумент команды расшарки."""


@dataclass
class ParsedShare:
    partner_id: str | None = None
    ids: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    pairs: list[tuple[str, str]] = field(default_factory=list)
    missing: str | None = None
    default_game: str | None = None


def strip_share_command(text: str) -> str:
    """Убирает /share_* в первой строке, остальное оставляет с переносами."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = raw.splitlines()
    match = _COMMAND_RE.match(lines[0].strip())
    if not match:
        return raw
    first = (match.group(2) or "").strip()
    tail = "\n".join(lines[1:]).strip()
    if first and tail:
        return f"{first}\n{tail}"
    return first or tail


def tokenize(text: str) -> list[str]:
    return [item for item in re.split(r"[\s,;]+", (text or "").strip()) if item]


def bundle_from_token(token: str) -> str | None:
    """Android bundle или ссылка Google Play. iOS сюда не входит."""
    raw = (token or "").strip().strip("<>")
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        return _android_from_url(raw)
    if BUNDLE_RE.match(raw):
        return raw
    return None


def app_from_token(token: str, *, bare_number: bool = True) -> str | None:
    """Android bundle или iOS id вида id1234567890."""
    raw = (token or "").strip().strip("<>")
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        return _android_from_url(raw) or _ios_from_url(raw)
    if BUNDLE_RE.match(raw):
        return raw
    prefixed = IOS_PREFIX_RE.match(raw)
    if prefixed:
        return f"id{prefixed.group(1)}"
    if bare_number and IOS_DIGITS_RE.match(raw):
        return f"id{raw}"
    return None


def partner_from_token(token: str) -> str | None:
    raw = (token or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        path = [part for part in urllib.parse.urlparse(raw).path.split("/") if part]
        if "integrated-partners" in path:
            tail = path[path.index("integrated-partners") + 1 :]
            if len(tail) >= 2 and app_from_token(tail[0], bare_number=False):
                return _clean_partner(tail[1])
            if tail:
                return _clean_partner(tail[0])
        if "partner" in path:
            tail = path[path.index("partner") + 1 :]
            if tail:
                return _clean_partner(tail[0])
        return None
    if app_from_token(raw):
        return None
    if PARTNER_RE.match(raw):
        return raw
    return None


def parse_apps(text: str) -> list[str]:
    apps: list[str] = []
    seen: set[str] = set()
    for token in tokenize(text):
        app = app_from_token(token)
        if not app or app in seen:
            continue
        seen.add(app)
        apps.append(app)
    return apps


def parse_share(kind: str, text: str) -> ParsedShare:
    body = strip_share_command(text)
    if kind == "agency":
        return _parse_agency(body)
    if kind == "tiktok":
        return _parse_tagged(body, TIKTOK_ID_RE, "TikTok App ID")
    if kind == "snapchat":
        return _parse_tagged(body, SNAP_ID_RE, "Snap App ID", lower=True)
    if kind == "unity":
        return _parse_unity(body)
    raise ParseError(f"Неизвестный тип расшарки: {kind}")


def _parse_agency(body: str) -> ParsedShare:
    if not body.strip():
        return ParsedShare(missing="partner")
    tokens = tokenize(body)
    partner = partner_from_token(tokens[0])
    if not partner:
        raise ParseError("Сначала нужен id агентства или ссылка AppsFlyer на партнёра, потом приложения.")
    unknown = []
    apps: list[str] = []
    seen: set[str] = set()
    for token in tokens[1:]:
        app = app_from_token(token)
        if app:
            if app not in seen:
                seen.add(app)
                apps.append(app)
            continue
        unknown.append(token)
    if unknown:
        raise ParseError("Не разобрал: " + ", ".join(unknown))
    if not apps:
        return ParsedShare(partner_id=partner, missing="apps")
    return ParsedShare(partner_id=partner, apps=apps)


def _parse_tagged(body: str, pattern: re.Pattern[str], title: str, *, lower: bool = False) -> ParsedShare:
    if not body.strip():
        return ParsedShare(missing="ids")
    ids: list[str] = []
    apps: list[str] = []
    unknown: list[str] = []
    seen_ids: set[str] = set()
    seen_apps: set[str] = set()
    for token in tokenize(body):
        app = app_from_token(token)
        if app:
            if app not in seen_apps:
                seen_apps.add(app)
                apps.append(app)
            continue
        value = token.lower() if lower else token
        if pattern.match(value):
            if value not in seen_ids:
                seen_ids.add(value)
                ids.append(value)
            continue
        unknown.append(token)
    if unknown:
        raise ParseError("Не разобрал: " + ", ".join(unknown))
    if not ids:
        if apps:
            raise ParseError(f"Нет {title}.")
        return ParsedShare(missing="ids")
    if not apps:
        return ParsedShare(ids=ids, missing="apps")
    return ParsedShare(ids=ids, apps=apps)


def _parse_unity(body: str) -> ParsedShare:
    if not body.strip():
        return ParsedShare(missing="pairs")
    pairs: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    default_game: str | None = None
    pending: list[str] = []
    unknown: list[str] = []

    def take_app(app: str, game: str) -> None:
        previous = seen.get(app)
        if previous is not None and previous != game:
            raise ParseError(f"У {app} два разных Unity game id.")
        if previous == game:
            return
        seen[app] = game
        pairs.append((app, game))

    for line in [item.strip() for item in body.splitlines() if item.strip()]:
        explicit: list[str] = []
        ambiguous: list[str] = []
        games: list[str] = []
        for token in tokenize(line):
            app = app_from_token(token, bare_number=False)
            if app:
                explicit.append(app)
                continue
            if IOS_DIGITS_RE.match(token):
                ambiguous.append(token)
                continue
            if UNITY_GAME_RE.match(token) and not TIKTOK_ID_RE.match(token):
                games.append(token)
                continue
            unknown.append(token)
        if unknown:
            raise ParseError("Не разобрал: " + ", ".join(unknown))
        if len(games) > 1:
            raise ParseError("В строке Unity должен быть один game id.")
        game: str | None = games[0] if games else None
        apps = list(explicit)
        if game:
            apps.extend(f"id{item}" for item in ambiguous)
        elif explicit or len(ambiguous) > 1:
            if not ambiguous:
                pass
            else:
                game = ambiguous[0]
                apps.extend(f"id{item}" for item in ambiguous[1:])
        elif len(ambiguous) == 1 and default_game:
            apps.append(f"id{ambiguous[0]}")
        elif len(ambiguous) == 1:
            game = ambiguous[0]
        if game and apps:
            for app in apps:
                take_app(app, game)
            continue
        if game:
            default_game = game
            for app in pending:
                take_app(app, default_game)
            pending = []
            continue
        if apps and default_game:
            for app in apps:
                take_app(app, default_game)
            continue
        if apps:
            pending.extend(apps)
            continue
    if pending:
        raise ParseError("Для приложений не хватает Unity game id.")
    if not pairs:
        return ParsedShare(missing="pairs", default_game=default_game)
    return ParsedShare(pairs=pairs, default_game=default_game)


def _android_from_url(raw: str) -> str | None:
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.netloc or "").lower()
    if "play.google.com" not in host:
        return None
    found = urllib.parse.parse_qs(parsed.query).get("id") or []
    candidate = found[0] if found else ""
    if BUNDLE_RE.match(candidate):
        return candidate
    return None


def _ios_from_url(raw: str) -> str | None:
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in APPLE_HOSTS:
        return None
    match = re.search(r"/id(\d+)", parsed.path, re.IGNORECASE)
    if not match:
        return None
    return f"id{match.group(1)}"


def _clean_partner(value: str) -> str | None:
    value = urllib.parse.unquote(value).strip()
    if PARTNER_RE.match(value):
        return value
    return None
