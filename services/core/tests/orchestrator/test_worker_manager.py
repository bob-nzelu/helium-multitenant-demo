"""
WS3 Orchestrator — Worker Manager tests.

Covers ThreadPoolWorker (submit/get_result/cancel/shutdown) and
WorkerManager (submit_batch/await_all/cancel_all).
"""
from __future__ import annotations

import asyncio
import pytest

from src.orchestrator.worker_manager import (
    TaskResult,
    ThreadPoolWorker,
    WorkerManager,
    WorkerStatus,
)


# ---------------------------------------------------------------------------
# ThreadPoolWorker — basic submit + result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_pool_worker_submit_returns_task_id():
    worker = ThreadPoolWorker()
    async def _task():
        return 42

    task_id = await worker.submit(_task)
    assert isinstance(task_id, str)
    assert len(task_id) > 0
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_task_completes():
    worker = ThreadPoolWorker()
    async def _task():
        return "done"

    task_id = await worker.submit(_task)
    result = await worker.get_result(task_id)
    assert result.status == WorkerStatus.COMPLETED
    assert result.result == "done"
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_task_with_args():
    worker = ThreadPoolWorker()
    async def _add(a, b):
        return a + b

    task_id = await worker.submit(_add, 3, 7)
    result = await worker.get_result(task_id)
    assert result.status == WorkerStatus.COMPLETED
    assert result.result == 10
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_task_failure():
    worker = ThreadPoolWorker()
    async def _bad():
        raise ValueError("intentional failure")

    task_id = await worker.submit(_bad)
    result = await worker.get_result(task_id)
    assert result.status == WorkerStatus.FAILED
    assert isinstance(result.error, ValueError)
    assert "intentional failure" in str(result.error)
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_task_timeout():
    worker = ThreadPoolWorker(task_timeout=1)
    async def _slow():
        await asyncio.sleep(10)
        return "never"

    task_id = await worker.submit(_slow)
    result = await worker.get_result(task_id)
    assert result.status == WorkerStatus.FAILED
    assert result.error is not None
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_cancel_pending():
    worker = ThreadPoolWorker()
    async def _slow():
        await asyncio.sleep(10)
        return "never"

    task_id = await worker.submit(_slow)
    # Give the task a tick to start
    await asyncio.sleep(0)
    cancelled = await worker.cancel(task_id)
    assert cancelled is True
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_cancel_completed_returns_false():
    worker = ThreadPoolWorker()
    async def _instant():
        return "fast"

    task_id = await worker.submit(_instant)
    await worker.get_result(task_id)
    # Task is done — cancel should return False
    result = await worker.cancel(task_id)
    assert result is False
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_get_result_unknown_task_id():
    worker = ThreadPoolWorker()
    result = await worker.get_result("non-existent-task-id")
    assert result.status == WorkerStatus.FAILED
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_shutdown_cancels_pending():
    worker = ThreadPoolWorker()
    async def _slow():
        await asyncio.sleep(30)
        return "never"

    task_id = await worker.submit(_slow)
    await asyncio.sleep(0)
    # Shutdown should not raise
    await worker.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_worker_task_result_has_task_id():
    worker = ThreadPoolWorker()
    async def _task():
        return 1

    task_id = await worker.submit(_task)
    result = await worker.get_result(task_id)
    assert result.task_id == task_id
    await worker.shutdown()


# ---------------------------------------------------------------------------
# WorkerManager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_manager_submit_batch_returns_task_id():
    mgr = WorkerManager(max_workers=4)
    async def _work(batch):
        return len(batch)

    task_id = await mgr.submit_batch(_work, [1, 2, 3])
    assert isinstance(task_id, str)
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_await_all_returns_results():
    mgr = WorkerManager(max_workers=4)
    async def _work(batch):
        await asyncio.sleep(0)  # yield so tasks can run
        return sum(batch)

    t1 = await mgr.submit_batch(_work, [1, 2])
    t2 = await mgr.submit_batch(_work, [10, 20])
    results = await mgr.await_all([t1, t2])
    assert len(results) == 2
    assert all(r.status == WorkerStatus.COMPLETED for r in results)
    values = {r.result for r in results}
    assert 3 in values
    assert 30 in values
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_await_all_preserves_order():
    mgr = WorkerManager(max_workers=4)
    results_order = []

    async def _work(batch):
        await asyncio.sleep(0)
        return batch[0]

    t1 = await mgr.submit_batch(_work, [1])
    t2 = await mgr.submit_batch(_work, [2])
    t3 = await mgr.submit_batch(_work, [3])
    results = await mgr.await_all([t1, t2, t3])
    assert results[0].result == 1
    assert results[1].result == 2
    assert results[2].result == 3
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_cancel_all():
    mgr = WorkerManager(max_workers=2)
    async def _slow(batch):
        await asyncio.sleep(30)
        return batch

    t1 = await mgr.submit_batch(_slow, [])
    t2 = await mgr.submit_batch(_slow, [])
    await asyncio.sleep(0)
    # Should not raise
    await mgr.cancel_all([t1, t2])
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_worker_count_property():
    mgr = WorkerManager(max_workers=6)
    assert mgr.worker_count == 6
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_concurrent_batches():
    mgr = WorkerManager(max_workers=8)
    completed = []

    async def _work(batch):
        await asyncio.sleep(0)
        completed.append(batch[0])
        return batch[0]

    task_ids = []
    for i in range(5):
        tid = await mgr.submit_batch(_work, [i])
        task_ids.append(tid)

    results = await mgr.await_all(task_ids)
    assert all(r.status == WorkerStatus.COMPLETED for r in results)
    assert len(results) == 5
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_worker_manager_shutdown_graceful():
    mgr = WorkerManager(max_workers=2)
    # No tasks submitted — shutdown should complete cleanly
    await mgr.shutdown()
