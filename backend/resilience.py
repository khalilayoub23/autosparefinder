"""
Resilience patterns: retry with exponential backoff, circuit breaker stubs, rate limiting.
This module provides decorators and helpers for production-grade resilience in async workloads.
"""

import asyncio
import functools
import logging
import random
from typing import Callable, Set, Tuple, Type, TypeVar, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retry_on: Tuple[int, ...] = (429, 503, 504),
    skip_on: Tuple[int, ...] = (404, 401, 403),
    jitter: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for async functions that implements exponential backoff retry logic.
    
    Args:
        max_retries: Maximum number of retry attempts (default 3).
        base_delay: Initial delay in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        retry_on: Tuple of HTTP status codes to retry on (default 429, 503, 504).
        skip_on: Tuple of HTTP status codes to skip retry (fail immediately) (default 404, 401, 403).
        jitter: Whether to add random jitter to delay (default True).
    
    Returns:
        Decorated async function with retry logic.
    
    Example:
        @retry_with_backoff(max_retries=3, base_delay=1.0, retry_on=(429, 503))
        async def fetch_supplier_catalog(url: str) -> dict:
            # Function body with httpx.AsyncClient.get() call
            pass
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            last_status_code = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(
                            f"{func.__name__} succeeded on retry attempt {attempt}",
                            extra={"attempts": attempt}
                        )
                    return result
                except Exception as e:
                    # Extract HTTP status code if available (from httpx.HTTPStatusError or similar)
                    status_code = None
                    if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                        status_code = e.response.status_code
                    elif hasattr(e, 'status_code'):
                        status_code = e.status_code
                    
                    last_exception = e
                    last_status_code = status_code
                    
                    # Skip retry for certain status codes (e.g., auth failures, not found)
                    if status_code and status_code in skip_on:
                        logger.warning(
                            f"{func.__name__} failed with non-retryable status {status_code}",
                            extra={"status_code": status_code, "attempt": attempt}
                        )
                        raise
                    
                    # Check if we should retry
                    should_retry = (
                        attempt < max_retries and
                        (status_code is None or status_code in retry_on)
                    )
                    
                    if should_retry:
                        # Calculate exponential backoff with optional jitter
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        if jitter:
                            delay = delay * (0.5 + random.random())
                        
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{max_retries + 1} failed "
                            f"(status: {status_code}). Retrying in {delay:.2f}s. Error: {str(e)[:100]}",
                            extra={"attempt": attempt, "status_code": status_code, "delay": delay}
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts (status: {status_code}). "
                            f"Giving up. Error: {str(e)[:100]}",
                            extra={"attempt": attempt, "status_code": status_code}
                        )
                        raise
            
            # Fallback (should not reach here, but just in case)
            if last_exception:
                raise last_exception
            
        return wrapper
    return decorator


def check_supplier_rate_limit(
    redis_client,
    supplier_domain: str,
    limit_per_minute: int = 30,
) -> bool:
    """
    Check if a supplier domain has exceeded its rate limit (sliding window per minute).
    
    Args:
        redis_client: Redis client instance (e.g., from app state or dependency injection).
        supplier_domain: Domain name extracted from supplier API endpoint or website URL.
        limit_per_minute: Rate limit threshold (default 30 requests/minute).
    
    Returns:
        True if limit is OK (proceed); False if limit exceeded (skip call).
    
    Example:
        if check_supplier_rate_limit(redis_client, "api.supplier.com", limit_per_minute=30):
            # Make HTTP call
            response = await httpx_client.get(url)
        else:
            logger.warning(f"Rate limit exceeded for {supplier_domain}")
            # Add to job_failures DLQ
    """
    from datetime import datetime, timezone
    
    # Use minute granularity (YYYY-MM-DD HH:MM format)
    current_minute = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    key = f"rate:supplier:{supplier_domain}:{current_minute}"
    
    try:
        # Increment counter for this minute
        count = redis_client.incr(key)
        
        # Set expiration if this is the first increment in the minute
        if count == 1:
            redis_client.expire(key, 60)
        
        # Check if limit exceeded
        if count > limit_per_minute:
            logger.warning(
                f"Supplier rate limit exceeded for {supplier_domain}: "
                f"{count} requests in minute {current_minute}",
                extra={"domain": supplier_domain, "count": count, "limit": limit_per_minute}
            )
            return False
        
        return True
    
    except Exception as e:
        # If Redis fails, log and allow the call (fail-open for resilience)
        logger.error(
            f"Error checking rate limit for {supplier_domain}: {str(e)}",
            extra={"domain": supplier_domain, "error": str(e)}
        )
        return True  # Fail-open: allow the call if rate-limit check fails


def get_supplier_domain_from_url(url: Optional[str]) -> Optional[str]:
    """
    Extract domain (netloc) from a supplier URL.
    
    Args:
        url: Full URL (e.g., "https://api.supplier.com/catalog/v1").
    
    Returns:
        Domain name (e.g., "api.supplier.com"), or None if URL is invalid/empty.
    """
    if not url:
        return None
    
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        netloc = parsed.netloc
        if netloc:
            return netloc
    except Exception as e:
        logger.warning(f"Failed to parse URL {url}: {str(e)}")
    
    return None


async def log_job_failure(
    db_session,
    job_name: str,
    error: str,
    payload: Optional[dict] = None,
    attempts: int = 1,
    next_retry_at: Optional[object] = None,
) -> None:
    """
    Write a job failure to the job_failures table (Dead Letter Queue).
    
    Args:
        db_session: AsyncSession connected to PII database.
        job_name: Name of the failed job (e.g., 'sync_prices', 'run_scraper_cycle').
        error: Exception message / traceback.
        payload: Original job parameters (dict).
        attempts: Number of attempts so far (default 1).
        next_retry_at: When to retry next (optional; if None, job won't auto-retry).
    
    Example:
        try:
            await sync_prices()
        except Exception as e:
            await log_job_failure(
                db_session=db,
                job_name='sync_prices',
                error=str(e),
                payload={},
                attempts=1,
                next_retry_at=datetime.now() + timedelta(hours=1)
            )
            raise
    """
    from datetime import datetime
    
    try:
        # Import JobFailure model (deferred to avoid circular imports)
        from BACKEND_DATABASE_MODELS import JobFailure
        
        failure = JobFailure(
            job_name=job_name,
            payload=payload or {},
            error=error[:2000],  # truncate traceback if very long
            attempts=attempts,
            next_retry_at=next_retry_at,
            status='pending',
        )
        db_session.add(failure)
        await db_session.commit()
        logger.info(f"Logged job failure: {job_name} (ID: {failure.id})")
    except Exception as e:
        logger.error(f"Failed to log job failure for {job_name}: {str(e)}")
