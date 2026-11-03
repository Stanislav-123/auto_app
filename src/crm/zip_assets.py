from __future__ import annotations

import re
import zipfile
from pathlib import Path

from src.crm.images import image_to_data_uri

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# Распакованный архив с картинками. Больше — отказываем, чтобы не раздувать диск.
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_ZIP_FILES = 40


def safe_upload_filename(filename: str | None, default: str) -> str:
    """Имя файла из Telegram: только basename, без пути и служебных символов."""
    base = Path(filename or default).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (base or default)[:120]


def safe_zip_filename(filename: str | None) -> str:
    base = safe_upload_filename(filename, "assets.zip")
    if not base.lower().endswith(".zip"):
        base = f"{base}.zip"
    return base[:120]


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


def extract_crm_images_from_zip(
    zip_path: Path,
    extract_dir: Path,
    *,
    max_uncompressed: int = MAX_UNCOMPRESSED_BYTES,
) -> dict[str, str]:
    """Extract CRM images from zip and return data URIs keyed by field name."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}

    with zipfile.ZipFile(zip_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"В архиве слишком много файлов (>{MAX_ZIP_FILES})")
        declared = sum(max(info.file_size, 0) for info in infos)
        if declared > max_uncompressed:
            raise ValueError("Архив слишком большой после распаковки")

        written = 0
        for info in infos:
            suffix = Path(info.filename).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                continue

            field = classify_image_filename(info.filename)
            if field is None:
                continue

            target = extract_dir / Path(info.filename).name
            if not _is_relative_to(target, extract_dir):
                raise ValueError("Некорректное имя файла в архиве")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_uncompressed:
                        raise ValueError("Архив слишком большой после распаковки")
                    dst.write(chunk)
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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
