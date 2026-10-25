"""将采集结果写入 JSON / CSV 文件。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CSV_FIELDS = [
    "url",
    "title",
    "description",
    "status",
    "depth",
    "word_count",
    "link_count",
    "image_count",
    "table_count",
    "content_type",
    "fetched_at",
    "text",
]


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def save_json(result: dict[str, Any], path: Path) -> Path:
    _write_json_atomic(path, result)
    return path


def _csv_safe(value: Any) -> Any:
    """避免网页内容在电子表格中被当作公式执行（CSV 注入）。"""

    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def save_csv(pages: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for page in pages:
            row = {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "description": page.get("description", ""),
                "status": page.get("status", ""),
                "depth": page.get("depth", ""),
                "word_count": page.get("word_count", ""),
                "link_count": len(page.get("links") or []),
                "image_count": len(page.get("images") or []),
                "table_count": len(page.get("tables") or []),
                "content_type": page.get("content_type", ""),
                "fetched_at": page.get("fetched_at", ""),
                "text": page.get("text", ""),
            }
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    temp_path.replace(path)
    return path
