"""Shared utilities for route modules — no dependency on BACKEND_API_ROUTES."""
import asyncio
import os
import io
import hashlib as _hashlib
import clamd as _clamd

# Cap fire-and-forget asyncio.create_task() fan-out.
_TASK_SEMAPHORE = asyncio.Semaphore(50)


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro


def _scan_bytes_for_virus(content: bytes) -> tuple:
    """
    Scan raw bytes with ClamAV daemon.
    Returns: ('clean', None) | ('infected', '<VirusName>') | ('skipped', None)
    Tries Unix socket first, falls back to TCP, then skips gracefully.
    """
    for _make_scanner in (
        lambda: _clamd.ClamdUnixSocket(),
        lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
    ):
        try:
            scanner = _make_scanner()
            result = scanner.instream(io.BytesIO(content))
            status, virus_name = result.get("stream", ("skipped", None))
            return (status.lower(), virus_name)
        except Exception:
            continue
    # ClamAV daemon unavailable — skip scan (dev/CI without ClamAV)
    return ("skipped", None)


def _mask_supplier(name: str) -> str:
    """Return a deterministic numbered alias for supplier names."""
    if not name:
        return "ספק"
    digest = int(_hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    num = (digest % 9999) + 1
    return f"ספק #{num}"
