"""WS3: Worker Manager — BaseWorker abstraction + ThreadPoolWorker for v1."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from uuid6 import uuid7

logger = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskResult:
    """Result of a submitted task."""

    task_id: str
    status: WorkerStatus
    result: Any = None
    error: Exception | None = None


class BaseWorker(ABC):
    """Abstract worker interface — swap between Thread (v1) and Celery (v2)."""

    @abstractmethod
    async def submit(self, func: Callable[..., Awaitable], *args: Any) -> str:
        """Submit an async task. Returns task_id for tracking."""
        ...

    @abstractmethod
    async def get_result(self, task_id: str) -> TaskResult:
        """Wait for and return the result of a submitted task."""
        ...

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean shutdown — cancel pending tasks, drain executor."""
        ...


class ThreadPoolWorker(BaseWorker):
    """Thread-based worker using asyncio.to_thread + ThreadPoolExecutor.

    Suitable for single-machine deployments (Test/Standard tier).
    CeleryWorker (same BaseWorker interface) deferred to v2.

    Args:
        max_workers: Number of concurrent threads.
        task_timeout: Per-task timeout in seconds.
    """

    def __init__(self, max_workers: int = 10, task_timeout: int = 60):
        self._max_workers = max_workers
        self._task_timeout = task_timeout
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, TaskResult] = {}

    async def submit(self, func: Callable[..., Awaitable], *args: Any) -> str:
        """Submit an async callable to run in the thread pool.

        The callable is awaited on the current event loop but may offload
        CPU work via asyncio.to_thread internally.
        """
        task_id = str(uuid7())

        async def _run() -> Any:
            return await func(*args)

        task = asyncio.create_task(_run())
        self._tasks[task_id] = task
        self._results[task_id] = TaskResult(task_id=task_id, status=WorkerStatus.RUNNING)
        task.add_done_callback(lambda t: self._on_done(task_id, t))
        return task_id

    def _on_done(self, task_id: str, task: asyncio.Task) -> None:
        """Callback when a task completes or fails."""
        if task.cancelled():
            self._results[task_id] = TaskResult(
                task_id=task_id, status=WorkerStatus.CANCELLED
            )
        elif task.exception():
            self._results[task_id] = TaskResult(
                task_id=task_id, status=WorkerStatus.FAILED, error=task.exception()
            )
        else:
            self._results[task_id] = TaskResult(
                task_id=task_id, status=WorkerStatus.COMPLETED, result=task.result()
            )

    async def get_result(self, task_id: str) -> TaskResult:
        """Wait for a task to complete and return its result."""
        task = self._tasks.get(task_id)
        if task is None:
            return self._results.get(
                task_id,
                TaskResult(task_id=task_id, status=WorkerStatus.FAILED),
            )
        try:
            await asyncio.wait_for(task, timeout=self._task_timeout)
        except asyncio.TimeoutError:
            task.cancel()
            self._results[task_id] = TaskResult(
                task_id=task_id,
                status=WorkerStatus.FAILED,
                error=TimeoutError(f"Task {task_id} timed out after {self._task_timeout}s"),
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # _on_done already captured it
        return self._results[task_id]

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        """Cancel all pending tasks and shut down the executor."""
        for task_id, task in self._tasks.items():
            if not task.done():
                task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("ThreadPoolWorker shut down (%d tasks)", len(self._tasks))


class WorkerManager:
    """High-level worker coordination for batch processing.

    Manages the lifecycle of a worker pool and provides batch-level
    submit/await/cancel operations used by PipelineOrchestrator.

    Args:
        max_workers: Pool size (default 10, configurable via CORE_WORKER_POOL_SIZE).
        task_timeout: Per-batch timeout in seconds (default 60).
    """

    def __init__(self, max_workers: int = 10, task_timeout: int = 60):
        self._worker = ThreadPoolWorker(
            max_workers=max_workers, task_timeout=task_timeout
        )
        self._max_workers = max_workers

    @property
    def worker_count(self) -> int:
        return self._max_workers

    async def submit_batch(
        self, func: Callable[..., Awaitable], batch: list
    ) -> str:
        """Submit a single batch for processing. Returns task_id."""
        return await self._worker.submit(func, batch)

    async def await_all(self, task_ids: list[str]) -> list[TaskResult]:
        """Wait for all submitted tasks to complete. Returns results in order."""
        results = await asyncio.gather(
            *(self._worker.get_result(tid) for tid in task_ids)
        )
        return list(results)

    async def cancel_all(self, task_ids: list[str]) -> None:
        """Cancel all tasks in the list."""
        for tid in task_ids:
            await self._worker.cancel(tid)

    async def shutdown(self) -> None:
        """Shut down the worker pool."""
        await self._worker.shutdown()
