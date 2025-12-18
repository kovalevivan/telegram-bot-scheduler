from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import delete, select

from app.db import engine, db_session, ensure_schema_migrations
from app.models import Base, Schedule, ScheduleType
from app.schemas import (
    CreateDailySchedule,
    CreateIntervalSchedule,
    CreateOnceSchedule,
    DeleteAllSchedulesByTokenUser,
    DeleteSchedulesByKey,
    ScheduleOut,
    UpdateSchedule,
    UpdateScheduleByKey,
)
from app.scheduler import SchedulerWorker, compute_next_run_at

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

worker = SchedulerWorker()


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Telegram Bot Scheduler", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now(tz=UTC).isoformat()}


def _to_out(s: Schedule) -> ScheduleOut:
    times_hhmm = None
    if s.times_hhmm:
        try:
            parsed = json.loads(s.times_hhmm)
            if isinstance(parsed, list):
                times_hhmm = [str(x) for x in parsed]
        except Exception:
            times_hhmm = None
    return ScheduleOut.model_validate(
        {
            "id": s.id,
            "token": s.token,
            "user_id": s.user_id,
            "scenario_id": s.scenario_id,
            "type": s.type.value,
            "time_hhmm": s.time_hhmm,
            "times_hhmm": times_hhmm,
            "timezone": s.timezone,
            "every_minutes": s.every_minutes,
            "run_at": s.run_at,
            "active": s.active,
            "next_run_at": s.next_run_at,
            "last_run_at": s.last_run_at,
            "last_status_code": s.last_status_code,
            "last_error": s.last_error,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
    )


@app.post("/schedules/daily", response_model=ScheduleOut)
async def create_daily(payload: CreateDailySchedule):
    now = datetime.now(tz=UTC)
    # Normalize: allow single time_hhmm or multiple times_hhmm
    times = payload.times_hhmm
    if not times and payload.time_hhmm:
        times = [payload.time_hhmm]
    if not times:
        raise HTTPException(status_code=400, detail="daily schedule requires time_hhmm or times_hhmm")
    times_json = json.dumps(times)

    async with db_session() as session:
        # Enforce "one daily schedule per token+user_id" (bot + client).
        # If exists -> update it; if multiple exist -> keep the newest, delete the rest.
        existing = (
            await session.execute(
                select(Schedule)
                .where(
                    Schedule.type == ScheduleType.daily,
                    Schedule.token == payload.token,
                    Schedule.user_id == payload.user_id,
                )
                .order_by(Schedule.created_at.desc())
            )
        ).scalars().all()

        if existing:
            s = existing[0]
            s.scenario_id = payload.scenario_id
            s.time_hhmm = times[0] if times else None
            s.times_hhmm = times_json
            s.timezone = payload.timezone
            s.active = True
            s.locked_until = None
            s.next_run_at = compute_next_run_at(s, now=now)

            # cleanup duplicates if any
            if len(existing) > 1:
                dup_ids = [x.id for x in existing[1:]]
                await session.execute(delete(Schedule).where(Schedule.id.in_(dup_ids)))

            await session.commit()
            await session.refresh(s)
            return _to_out(s)

        s = Schedule(
            token=payload.token,
            user_id=payload.user_id,
            scenario_id=payload.scenario_id,
            type=ScheduleType.daily,
            time_hhmm=times[0] if times else None,
            times_hhmm=times_json,
            timezone=payload.timezone,
            active=True,
        )
        s.next_run_at = compute_next_run_at(s, now=now)
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return _to_out(s)


@app.post("/schedules/interval", response_model=ScheduleOut)
async def create_interval(payload: CreateIntervalSchedule):
    now = datetime.now(tz=UTC)
    s = Schedule(
        token=payload.token,
        user_id=payload.user_id,
        scenario_id=payload.scenario_id,
        type=ScheduleType.interval,
        every_minutes=payload.every_minutes,
        active=True,
    )
    s.next_run_at = compute_next_run_at(s, now=now)
    async with db_session() as session:
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return _to_out(s)


@app.post("/schedules/once", response_model=ScheduleOut)
async def create_once(payload: CreateOnceSchedule):
    now = datetime.now(tz=UTC)
    run_at_utc = payload.run_at.astimezone(UTC)
    s = Schedule(
        token=payload.token,
        user_id=payload.user_id,
        scenario_id=payload.scenario_id,
        type=ScheduleType.once,
        run_at=run_at_utc,
        active=True,
    )
    s.next_run_at = compute_next_run_at(s, now=now)
    async with db_session() as session:
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return _to_out(s)


@app.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    token: str | None = Query(default=None),
    user_id: int | None = Query(default=None, ge=1),
    active: bool | None = Query(default=None),
):
    stmt = select(Schedule).order_by(Schedule.created_at.desc())
    if token is not None:
        stmt = stmt.where(Schedule.token == token)
    if user_id is not None:
        stmt = stmt.where(Schedule.user_id == user_id)
    if active is not None:
        stmt = stmt.where(Schedule.active.is_(active))

    async with db_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_out(s) for s in rows]


@app.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: uuid.UUID, payload: UpdateSchedule):
    async with db_session() as session:
        s = await session.get(Schedule, schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="schedule not found")

        if payload.scenario_id is not None:
            s.scenario_id = payload.scenario_id
        if payload.active is not None:
            s.active = payload.active

        if payload.time_hhmm is not None:
            s.time_hhmm = payload.time_hhmm
            if s.type == ScheduleType.daily:
                s.times_hhmm = json.dumps([payload.time_hhmm])
        if payload.times_hhmm is not None:
            if s.type != ScheduleType.daily:
                raise HTTPException(status_code=400, detail="times_hhmm is only valid for daily schedules")
            s.time_hhmm = payload.times_hhmm[0] if payload.times_hhmm else None
            s.times_hhmm = json.dumps(payload.times_hhmm)
        if payload.timezone is not None:
            s.timezone = payload.timezone
        if payload.every_minutes is not None:
            s.every_minutes = payload.every_minutes
        if payload.run_at is not None:
            s.run_at = payload.run_at.astimezone(UTC)

        # basic type safety
        if s.type == ScheduleType.daily and not (s.times_hhmm or s.time_hhmm):
            raise HTTPException(status_code=400, detail="daily schedule requires time_hhmm or times_hhmm")
        if s.type == ScheduleType.interval and not s.every_minutes:
            raise HTTPException(status_code=400, detail="interval schedule requires every_minutes")
        if s.type == ScheduleType.once and not s.run_at:
            raise HTTPException(status_code=400, detail="once schedule requires run_at")

        s.next_run_at = compute_next_run_at(s, now=datetime.now(tz=UTC))
        s.locked_until = None

        await session.commit()
        await session.refresh(s)
        return _to_out(s)


@app.patch("/schedules/by_key", response_model=ScheduleOut)
async def update_schedule_by_key(payload: UpdateScheduleByKey):
    """
    Update a schedule using (token, user_id, type). Useful when client doesn't store UUID.
    For daily schedules this matches the "one per token+user_id" constraint.
    """
    async with db_session() as session:
        s = (
            await session.execute(
                select(Schedule)
                .where(
                    Schedule.token == payload.token,
                    Schedule.user_id == payload.user_id,
                    Schedule.type == ScheduleType(payload.type),
                )
                .order_by(Schedule.created_at.desc())
            )
        ).scalars().first()

        if not s:
            raise HTTPException(status_code=404, detail="schedule not found")

        if payload.scenario_id is not None:
            s.scenario_id = payload.scenario_id
        if payload.active is not None:
            s.active = payload.active

        if payload.time_hhmm is not None:
            s.time_hhmm = payload.time_hhmm
            if s.type == ScheduleType.daily:
                s.times_hhmm = json.dumps([payload.time_hhmm])
        if payload.times_hhmm is not None:
            if s.type != ScheduleType.daily:
                raise HTTPException(status_code=400, detail="times_hhmm is only valid for daily schedules")
            s.time_hhmm = payload.times_hhmm[0] if payload.times_hhmm else None
            s.times_hhmm = json.dumps(payload.times_hhmm)
        if payload.timezone is not None:
            s.timezone = payload.timezone
        if payload.every_minutes is not None:
            s.every_minutes = payload.every_minutes
        if payload.run_at is not None:
            s.run_at = payload.run_at.astimezone(UTC)

        # basic type safety
        if s.type == ScheduleType.daily and not (s.times_hhmm or s.time_hhmm):
            raise HTTPException(status_code=400, detail="daily schedule requires time_hhmm or times_hhmm")
        if s.type == ScheduleType.interval and not s.every_minutes:
            raise HTTPException(status_code=400, detail="interval schedule requires every_minutes")
        if s.type == ScheduleType.once and not s.run_at:
            raise HTTPException(status_code=400, detail="once schedule requires run_at")

        s.next_run_at = compute_next_run_at(s, now=datetime.now(tz=UTC))
        s.locked_until = None

        await session.commit()
        await session.refresh(s)
        return _to_out(s)


@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: uuid.UUID):
    async with db_session() as session:
        s = await session.get(Schedule, schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="schedule not found")
        await session.delete(s)
        await session.commit()
    return {"deleted": True, "id": str(schedule_id)}


@app.post("/schedules/by_key/delete")
async def delete_schedule_by_key(payload: DeleteSchedulesByKey):
    """Delete ONE schedule type by (token, user_id, type) - parameters in body."""
    async with db_session() as session:
        stmt = delete(Schedule).where(
            Schedule.token == payload.token,
            Schedule.user_id == payload.user_id,
            Schedule.type == ScheduleType(payload.type),
        )
        res = await session.execute(stmt)
        await session.commit()
        return {"deleted": True, "deleted_count": int(res.rowcount or 0)}


@app.post("/schedules/by_key/delete_all")
async def delete_all_schedules_by_token_user(payload: DeleteAllSchedulesByTokenUser):
    """Delete ALL schedules by (token, user_id) - parameters in body."""
    async with db_session() as session:
        stmt = delete(Schedule).where(Schedule.token == payload.token, Schedule.user_id == payload.user_id)
        res = await session.execute(stmt)
        await session.commit()
        return {"deleted": True, "deleted_count": int(res.rowcount or 0)}
