from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, time
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Schedule, ScheduleType
from app.settings import settings

logger = logging.getLogger("scheduler")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def compute_next_run_at(s: Schedule, *, now: datetime) -> datetime | None:
    if not s.active:
        return None

    if s.type == ScheduleType.once:
        return s.run_at

    if s.type == ScheduleType.interval:
        if not s.every_minutes:
            return None
        base = s.next_run_at or now
        nxt = base + timedelta(minutes=int(s.every_minutes))
        # catch up if server was down
        while nxt <= now:
            nxt += timedelta(minutes=int(s.every_minutes))
        return nxt

    if s.type == ScheduleType.daily:
        times: list[str] = []
        if s.times_hhmm:
            try:
                parsed = json.loads(s.times_hhmm)
                if isinstance(parsed, list):
                    times = [str(x) for x in parsed]
            except Exception:
                times = []
        if not times and s.time_hhmm:
            times = [s.time_hhmm]
        if not times:
            return None
        tz = ZoneInfo(s.timezone or "UTC")
        local_now = now.astimezone(tz)
        parsed_times: list[time] = []
        for t in times:
            try:
                hh, mm = str(t).split(":")
                parsed_times.append(time(hour=int(hh), minute=int(mm)))
            except Exception:
                continue
        if not parsed_times:
            return None
        parsed_times = sorted(parsed_times)

        # next time today
        for tt in parsed_times:
            candidate = datetime.combine(local_now.date(), tt, tzinfo=tz)
            if candidate > local_now:
                return candidate.astimezone(UTC)

        # otherwise tomorrow earliest
        candidate = datetime.combine(local_now.date() + timedelta(days=1), parsed_times[0], tzinfo=tz)
        return candidate.astimezone(UTC)

    return None


@dataclass(frozen=True)
class DueSchedule:
    id: uuid.UUID
    token: str
    user_id: int
    scenario_id: int
    type: ScheduleType


class SchedulerWorker:
    def __init__(self):
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._sem = asyncio.Semaphore(settings.max_concurrent_runs)

        self._client = httpx.AsyncClient(
            base_url=settings.puzzlebot_base_url,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            headers={"User-Agent": "telegram-bot-scheduler/1.0"},
        )

    async def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._run_loop(), name="scheduler-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        await self._client.aclose()

    async def _run_loop(self) -> None:
        logger.info("worker started")
        while not self._stop.is_set():
            started = _now()
            try:
                await self._tick()
            except Exception:
                logger.exception("worker tick failed")
            elapsed = (_now() - started).total_seconds()
            sleep_for = max(0.0, float(settings.worker_poll_seconds) - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except TimeoutError:
                pass
        logger.info("worker stopped")

    async def _tick(self) -> None:
        from app.db import db_session

        async with db_session() as session:
            due = await self._lock_and_fetch_due(session)

        if not due:
            logger.debug("no due schedules")
            return

        logger.info("due schedules claimed: %s", len(due))

        async def run_one(d: DueSchedule) -> None:
            async with self._sem:
                await self._execute(d)

        await asyncio.gather(*(run_one(d) for d in due))

    async def _lock_and_fetch_due(self, session: AsyncSession) -> list[DueSchedule]:
        now = _now()
        lease_until = now + timedelta(seconds=int(settings.worker_lock_lease_seconds))

        due_filter = and_(
            Schedule.active.is_(True),
            Schedule.next_run_at.is_not(None),
            Schedule.next_run_at <= now,
            or_(Schedule.locked_until.is_(None), Schedule.locked_until <= now),
        )

        # 1) Pick up to batch_size candidates (portable approach for SQLite/Postgres)
        pick_stmt = (
            select(Schedule.id)
            .where(due_filter)
            .order_by(Schedule.next_run_at.asc())
            .limit(int(settings.worker_batch_size))
        )
        ids = [r[0] for r in (await session.execute(pick_stmt)).all()]
        if not ids:
            return []

        # 2) Claim only picked ids (race-safe: may return fewer rows)
        claim_stmt = (
            update(Schedule)
            .where(and_(Schedule.id.in_(ids), due_filter))
            .values(locked_until=lease_until)
            .returning(Schedule.id, Schedule.token, Schedule.user_id, Schedule.scenario_id, Schedule.type)
            .execution_options(synchronize_session=False)
        )
        rows = (await session.execute(claim_stmt)).all()
        await session.commit()

        return [DueSchedule(*row) for row in rows]

    async def _execute(self, d: DueSchedule) -> None:
        now = _now()
        url_params = {
            "token": d.token,
            "method": "scenarioRun",
            "scenario_id": str(d.scenario_id),
            "user_id": str(d.user_id),
        }

        status_code: int | None = None
        err: str | None = None

        try:
            resp = await self._request_with_retries(params=url_params)
            status_code = resp.status_code
            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {resp.text[:1000]}"
        except Exception as e:
            err = repr(e)
        finally:
            logger.info(
                "executed schedule id=%s user_id=%s scenario_id=%s status=%s error=%s",
                d.id,
                d.user_id,
                d.scenario_id,
                status_code,
                "none" if not err else err[:200],
            )

        from app.db import db_session

        async with db_session() as session:
            s = await session.get(Schedule, d.id)
            if not s:
                return

            s.last_run_at = now
            s.last_status_code = status_code
            s.last_error = err
            s.locked_until = None

            if s.type == ScheduleType.once:
                s.active = False
                s.next_run_at = None
            else:
                s.next_run_at = compute_next_run_at(s, now=now)

            await session.commit()

    async def _request_with_retries(self, *, params: dict[str, str]) -> httpx.Response:
        attempts = int(settings.http_retries) + 1
        last_exc: Exception | None = None

        for i in range(attempts):
            try:
                return await self._client.get("/", params=params)
            except Exception as e:
                last_exc = e
                if i == attempts - 1:
                    raise
                await asyncio.sleep(0.5 * (2**i))

        raise last_exc or RuntimeError("unexpected retry flow")
