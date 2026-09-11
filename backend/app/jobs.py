from __future__ import annotations

import traceback
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import httpx

from .repository import Repository, now


class JobManager:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gk-job")

    def submit(self, job: dict, operation: Callable[[Callable[..., None]], None]) -> dict:
        def runner() -> None:
            self.repository.update_job(job["job_id"], status="running", started_at=now(), message="工作開始")

            def progress(**fields) -> None:
                self.repository.update_job(job["job_id"], **fields)

            try:
                operation(progress)
                self.repository.update_job(job["job_id"], status="completed", progress=1.0, message="完成", finished_at=now())
            except Exception as exc:
                traceback.print_exc()
                generation_job = job.get("job_type") in {"legacy_generate_all", "generate_page", "regenerate_panel"}
                if generation_job and isinstance(exc, (OSError, httpx.TransportError)):
                    self.repository.mark_generation_transport_interrupted(job["job_id"], job["project_id"], str(exc))
                else:
                    self.repository.update_job(job["job_id"], status="failed", error=str(exc), message="執行失敗", finished_at=now())
                    self.repository.update_project(job["project_id"], status="failed", error=str(exc))

        context = copy_context()
        self.executor.submit(context.run, runner)
        return job
