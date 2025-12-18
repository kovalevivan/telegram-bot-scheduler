from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScheduleType(str, enum.Enum):
    daily = "daily"
    interval = "interval"
    once = "once"


def _uuid_column():
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = _uuid_column()

    token: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    scenario_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType, name="schedule_type"), nullable=False)

    # daily
    time_hhmm: Mapped[str | None] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # daily (multiple times per day) stored as JSON string: ["HH:MM", ...]
    times_hhmm: Mapped[str | None] = mapped_column(Text, nullable=True)

    # interval
    every_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # once
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
