from __future__ import annotations

import zipfile
from pathlib import Path

from src.crm.images import image_to_data_uri

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def classify_image_filename(filename: str) -> str | None:
    """
    Map archive entry name to CRM image field.

    Patterns (case-insensitive basename):
    - logo* -> logo
    - zm* -> banner (ZM Apps)
    - ac* -> banner_appcraft (AppCraft)
    - *compressed_banner* -> slider (screenshots)
    """
    basename = Path(filename).name.lower()

    if basename.startswith("logo"):
        return "logo"
    if "compressed_banner" in basename:
        return "slider"
    if basename.startswith("ac"):
        return "banner_appcraft"
    if basename.startswith("zm"):
        return "banner"
    return None


def extract_crm_images_from_zip(zip_path: Path, extract_dir: Path) -> dict[str, str]:
    """Extract CRM images from zip and return data URIs keyed by field name."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}

    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            suffix = Path(info.filename).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                continue

            field = classify_image_filename(info.filename)
            if field is None:
                continue

            target = extract_dir / Path(info.filename).name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            found[field] = target

    required = ("logo", "banner", "banner_appcraft", "slider")
    missing = [name for name in required if name not in found]
    if missing:
        labels = {
            "logo": "logo.png (логотип)",
            "banner": "ZM_* (баннер ZM Apps)",
            "banner_appcraft": "AC_* (баннер AppCraft)",
            "slider": "compressed_banner* (скриншоты)",
        }
        missing_labels = ", ".join(labels[name] for name in missing)
        raise ValueError(f"В архиве не хватает файлов: {missing_labels}")

    return {field: image_to_data_uri(path) for field, path in found.items()}
