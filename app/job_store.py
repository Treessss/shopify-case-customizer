from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ExportJob:
    id: str
    shop_domain: str
    status: str = "queued"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    export_mode: str = "bulk"
    product_query: str = "status:active"
    test_mode: bool = False
    file_name: str | None = None
    file_path: str | None = None
    row_count: int = 0
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.file_path:
            data["download_url"] = f"/api/jobs/{self.id}/download"
        return data


class JobStore:
    def __init__(self) -> None:
        self._items: dict[str, ExportJob] = {}
        self._lock = Lock()

    def create(self, shop_domain: str, export_mode: str, product_query: str, test_mode: bool = False) -> ExportJob:
        with self._lock:
            job = ExportJob(
                id=str(uuid4()),
                shop_domain=shop_domain,
                export_mode=export_mode,
                product_query=product_query,
                test_mode=test_mode,
            )
            self._items[job.id] = job
            return job

    def get(self, job_id: str) -> ExportJob | None:
        with self._lock:
            return self._items.get(job_id)

    def update(self, job_id: str, **changes: Any) -> ExportJob:
        with self._lock:
            job = self._items[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = utc_now_iso()
            return job


job_store = JobStore()
