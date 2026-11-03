from __future__ import annotations

import copy
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from src.http_errors import transport_failure
from src.workflow.http_log import log_request, log_response

from .keys import extract_binom_campaign_key


class BinomError(Exception):
    """Ошибка при работе с Binom API."""


@dataclass
class ClonedCampaign:
    id: int
    name: str
    url: str
    key: str


_CAMPAIGN_FIELDS_TO_DROP = (
    "id",
    "rotation_id",
    "keyword",
    "date_cr",
    "base_url",
    "url",
    "click_key",
)


class BinomClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        template_campaign_id: int = 2,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.template_campaign_id = template_campaign_id

    def _call(self, action: str, payload: Optional[dict[str, Any]] = None) -> Any:
        body: dict[str, Any] = {"api_key": self.api_key, "action": action}
        if payload is not None:
            body["payload"] = payload

        log_request("binom", "POST", self.api_url, {"action": action, "payload": payload})
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            log_response("binom", "POST", self.api_url, exc.code, details)
            raise BinomError(f"HTTP {exc.code}: {details}") from exc
        except Exception as exc:
            failure = transport_failure(exc)
            if failure is None:
                raise
            log_response("binom", "POST", self.api_url, "error", failure)
            raise BinomError(failure) from exc

        if not raw:
            log_response("binom", "POST", self.api_url, status, "")
            raise BinomError("Пустой ответ от Binom API")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_response("binom", "POST", self.api_url, status, raw)
            raise BinomError(f"Некорректный JSON от Binom: {raw[:200]}") from exc

        log_response("binom", "POST", self.api_url, status, data)

        if isinstance(data, dict) and data.get("status") == "error":
            message = data.get("message", "Неизвестная ошибка")
            raise BinomError(message)

        return data

    def get_campaign_full(self, campaign_id: int) -> dict[str, Any]:
        return self._call("campaign@get_full", {"id": campaign_id})

    def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        return self._call("campaign@get", {"id": campaign_id})

    def find_campaign_by_name(self, name: str) -> Optional[ClonedCampaign]:
        """Ищет кампанию по точному имени через отчёт Campaigns (search_name)."""
        rows = self.list_campaigns(name)
        want = name.strip()
        exact = [
            row
            for row in rows
            if str(row.get("name") or "").strip() == want and str(row.get("status", "1")) != "0"
        ]
        if not exact:
            return None
        exact.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
        row = exact[0]
        url = str(row.get("url") or row.get("link") or "")
        key = extract_binom_campaign_key(key=str(row.get("keyword") or row.get("key") or ""), url=url)
        return ClonedCampaign(
            id=int(row["id"]),
            name=str(row.get("name") or want),
            url=url,
            key=key,
        )

    def list_campaigns(self, search_name: str) -> list[dict[str, Any]]:
        panel_url = _panel_campaigns_url(self.api_url)
        query = urllib.parse.urlencode(
            {
                "page": "Campaigns",
                "status": "all",
                "group": "all",
                "traffic_source": "all",
                "date": "9",
                "val_page": "all",
                "api_key": self.api_key,
                "search_name": search_name,
            }
        )
        url = f"{panel_url}?{query}"
        log_request("binom", "GET", url)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            log_response("binom", "GET", url, exc.code, details)
            raise BinomError(f"HTTP {exc.code}: {details}") from exc
        except Exception as exc:
            failure = transport_failure(exc)
            if failure is None:
                raise
            log_response("binom", "GET", url, "error", failure)
            raise BinomError(failure) from exc

        if not raw:
            log_response("binom", "GET", url, status, "")
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_response("binom", "GET", url, status, raw[:500])
            raise BinomError(f"Некорректный JSON списка кампаний: {raw[:200]}") from exc

        log_response("binom", "GET", url, status, data if not isinstance(data, list) else f"list:{len(data)}")
        if isinstance(data, dict) and data.get("status") == "error":
            raise BinomError(str(data.get("message") or "Ошибка списка кампаний"))
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in ("campaigns", "data", "rows"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
        return []

    def clone_campaign(self, name: str, template_id: Optional[int] = None) -> ClonedCampaign:
        source_id = template_id or self.template_campaign_id
        full = self.get_campaign_full(source_id)

        payload = {
            "campaign": self._prepare_campaign_payload(full["campaign"], name),
            "routing": copy.deepcopy(full["routing"]),
        }

        result = self._call("campaign@add", payload)
        if result.get("status") != "added":
            raise BinomError(f"Не удалось создать кампанию: {result}")

        campaign_id = int(result["id"])
        campaign = self.get_campaign(campaign_id)
        url = campaign.get("url", "")
        key = extract_binom_campaign_key(key=campaign.get("keyword", ""), url=url)

        return ClonedCampaign(
            id=campaign_id,
            name=campaign.get("name", name),
            url=url,
            key=key,
        )

    @staticmethod
    def _prepare_campaign_payload(campaign: dict[str, Any], name: str) -> dict[str, Any]:
        payload = copy.deepcopy(campaign)
        payload["name"] = name
        for field in _CAMPAIGN_FIELDS_TO_DROP:
            payload.pop(field, None)
        return payload


def _panel_campaigns_url(api_url: str) -> str:
    """arm.php того же трекера → panel.php, откуда Binom отдаёт список кампаний."""
    parsed = urlparse(api_url)
    path = parsed.path or "/"
    if path.endswith("arm.php"):
        path = path[: -len("arm.php")] + "panel.php"
    elif not path.endswith("panel.php"):
        path = path.rstrip("/") + "/panel.php"
    return urlunparse(parsed._replace(path=path, query="", fragment=""))
