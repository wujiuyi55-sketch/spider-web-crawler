"""后台采集任务管理与结果持久化。"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CrawlConfig
from .engine import Crawler
from .exporters import save_csv, save_json
from .urls import ensure_safe_url, normalize_url

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobNotFoundError(KeyError):
    pass


class JobManager:
    """使用线程池执行爬虫，任务状态保存在内存中，结果落盘。"""

    def __init__(self, output_dir: Path | str, max_workers: int = 3) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="crawler"
        )

    def create(self, url: str, config: CrawlConfig) -> dict[str, Any]:
        normalized = normalize_url(url)
        ensure_safe_url(normalized, allow_private=config.allow_private)

        job_id = (
            datetime.now().strftime("%Y%m%d")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        cancel_event = threading.Event()
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "start_url": normalized,
            "config": config.to_dict(),
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "pages_done": 0,
            "successes": 0,
            "error_count": 0,
            "skipped_count": 0,
            "current_url": "",
            "message": "任务已排队，等待开始…",
            "duration_ms": None,
            "limit_reached": False,
            "result_available": False,
            "error_message": "",
            "_cancel": cancel_event,
            "_result": None,
            "_started_monotonic": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id, config)
        return self._snapshot(job)

    def _snapshot(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if not key.startswith("_")
        }

    def get_status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobNotFoundError(job_id)
            return self._snapshot(job)

    def get_result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobNotFoundError(job_id)
            if job["status"] not in TERMINAL_STATUSES:
                raise RuntimeError("任务尚未结束")
            result = job.get("_result")
            if result is None:
                raise FileNotFoundError(job_id)
            return {
                "job": self._snapshot(job),
                "pages": copy.deepcopy(result.get("pages", [])),
                "errors": copy.deepcopy(result.get("errors", [])),
                "warnings": copy.deepcopy(result.get("warnings", [])),
                "skipped": copy.deepcopy(result.get("skipped", [])),
                "summary": {
                    key: copy.deepcopy(result.get(key))
                    for key in (
                        "pages_done",
                        "successes",
                        "error_count",
                        "skipped_count",
                        "status_counts",
                        "cancelled",
                        "limit_reached",
                        "started_at",
                        "finished_at",
                        "duration_ms",
                    )
                },
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobNotFoundError(job_id)
            if job["status"] in TERMINAL_STATUSES:
                return self._snapshot(job)
            job["_cancel"].set()
            if job["status"] in {"queued", "running"}:
                job["status"] = "cancelling"
                job["message"] = "正在停止采集，请稍候…"
            return self._snapshot(job)

    def download_path(self, job_id: str, file_format: str) -> Path | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobNotFoundError(job_id)
            if job["status"] not in TERMINAL_STATUSES:
                return None
            if not job["result_available"]:
                return None
        extension = "json" if file_format == "json" else "csv"
        path = self.output_dir / job_id / f"pages.{extension}"
        return path if path.is_file() else None

    def _run(self, job_id: str, config: CrawlConfig) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            started = time.monotonic()
            job["status"] = "running"
            job["started_at"] = _now_iso()
            job["_started_monotonic"] = started
            job["message"] = "正在连接目标网站…"
            cancel_event: threading.Event = job["_cancel"]
            start_url = job["start_url"]

        if cancel_event.is_set():
            self._finish_cancelled(job_id)
            return

        def on_progress(update: dict[str, Any]) -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if not current or current["status"] in TERMINAL_STATUSES:
                    return
                for key in (
                    "pages_done",
                    "successes",
                    "error_count",
                    "current_url",
                    "message",
                ):
                    if key in update:
                        current[key] = update[key]
                if "errors" in update:
                    current["error_count"] = update["errors"]
                if "skipped" in update:
                    current["skipped_count"] = update["skipped"]
                if current["status"] not in {"cancelling"}:
                    current["status"] = "running"

        result: dict[str, Any]
        try:
            crawler = Crawler(
                config=config,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
            result = crawler.crawl(start_url)
        except Exception as exc:
            logger.exception("采集任务异常: %s", job_id)
            result = {
                "start_url": start_url,
                "pages": [],
                "errors": [
                    {
                        "url": start_url,
                        "message": f"任务执行失败：{exc}",
                        "depth": 1,
                        "kind": "internal",
                    }
                ],
                "warnings": [],
                "skipped": [],
                "skipped_count": 0,
                "status_counts": {},
                "pages_done": 0,
                "successes": 0,
                "error_count": 1,
                "cancelled": cancel_event.is_set(),
                "limit_reached": False,
                "started_at": _now_iso(),
                "finished_at": _now_iso(),
                "duration_ms": int(
                    (time.monotonic() - (job.get("_started_monotonic") or started))
                    * 1000
                ),
                "config": config.to_dict(),
            }

        if cancel_event.is_set():
            result["cancelled"] = True

        job_dir = self.output_dir / job_id
        persist_error = ""
        predicted_status = (
            "cancelled"
            if cancel_event.is_set()
            else ("failed" if not result.get("successes") else "completed")
        )
        result_for_disk = dict(result)
        result_for_disk["job"] = {
            "id": job_id,
            "status": predicted_status,
            "start_url": result.get("start_url", start_url),
            "created_at": job.get("created_at"),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "duration_ms": result.get("duration_ms"),
            "result_available": True,
        }
        try:
            save_json(result_for_disk, job_dir / "pages.json")
            save_csv(result.get("pages", []), job_dir / "pages.csv")
        except Exception as exc:
            logger.exception("保存采集结果失败: %s", job_id)
            persist_error = f"结果文件保存失败：{exc}"

        self._finalize(job_id, result, persist_error)

    def _finish_cancelled(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "cancelled"
            job["message"] = "采集已取消"
            job["finished_at"] = _now_iso()
            job["duration_ms"] = int(
                (
                    time.monotonic()
                    - (job.get("_started_monotonic") or time.monotonic())
                )
                * 1000
            )

    def _finalize(
        self, job_id: str, result: dict[str, Any], persist_error: str = ""
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            successes = int(result.get("successes", 0))
            errors = result.get("errors", [])
            cancelled = bool(result.get("cancelled"))
            job["_result"] = result
            job["pages_done"] = int(result.get("pages_done", 0))
            job["successes"] = successes
            job["error_count"] = len(errors)
            job["skipped_count"] = int(result.get("skipped_count", 0))
            job["current_url"] = ""
            job["finished_at"] = result.get("finished_at") or _now_iso()
            job["duration_ms"] = int(result.get("duration_ms", 0))
            job["limit_reached"] = bool(result.get("limit_reached"))
            job["result_available"] = not persist_error

            if persist_error:
                job["error_message"] = persist_error
                job["message"] = persist_error
            if cancelled:
                job["status"] = "cancelled"
                job["message"] = (
                    f"采集已停止，已保存 {successes} 个页面"
                    if successes
                    else "采集已取消"
                )
            elif successes == 0:
                job["status"] = "failed"
                warnings = result.get("warnings", [])
                first_error = (
                    errors[0]["message"]
                    if errors
                    else (warnings[0] if warnings else "没有采集到任何页面")
                )
                job["error_message"] = first_error
                job["message"] = f"采集失败：{first_error}"
            else:
                job["status"] = "completed"
                job["message"] = (
                    f"采集完成，共 {successes} 个页面"
                    if not errors
                    else f"采集完成，成功 {successes} 个，失败 {len(errors)} 个"
                )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
