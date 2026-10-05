from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    IOS = "ios"
    ANDROID = "android"
    UNKNOWN = "unknown"


BACKEND_URL_NEW = "https://neonflightgold777.cfd/apps"
BACKEND_URL_OLD = (
    "https://docs.google.com/spreadsheets/u/1/d/"
    "1GhYKbFXmnlfx_1YgfCVQjGTzMggpi1zYcGtugtVNeMU/edit?pli=1#gid=867667089"
)
BACKEND_URL_ANDROID = "https://myappadmin.top/qize4nmp/sites"


@dataclass
class ParsedApp:
    platform: Platform
    name: Optional[str] = None
    store_url: Optional[str] = None
    store_id: Optional[str] = None
    bundle: Optional[str] = None
    backend_domain: Optional[str] = None
    appsflyer: Optional[bool] = None
    owner: Optional[str] = None
    pushes: Optional[str] = None
    developer_site: Optional[str] = None
    backend: Optional[str] = None
    firebase_name: Optional[str] = None
    raw_text: str = ""
    missing_fields: list[str] = field(default_factory=list)

    @property
    def backend_url(self) -> Optional[str]:
        if self.platform == Platform.ANDROID:
            return BACKEND_URL_ANDROID

        if not self.backend:
            return None
        normalized = self.backend.strip().lower()
        if normalized == "новый":
            return BACKEND_URL_NEW
        if normalized == "старый":
            return BACKEND_URL_OLD
        return None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform.value,
            "name": self.name,
            "store_url": self.store_url,
            "store_id": self.store_id,
            "bundle": self.bundle,
            "backend_domain": self.backend_domain,
            "appsflyer": self.appsflyer,
            "owner": self.owner,
            "pushes": self.pushes,
            "developer_site": self.developer_site,
            "backend": self.backend,
            "backend_url": self.backend_url,
            "firebase_name": self.firebase_name,
            "missing_fields": self.missing_fields,
            "raw_text": self.raw_text,
        }
