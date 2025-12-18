from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


class ScheduleBase(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    scenario_id: int = Field(ge=1)
    user_id: int = Field(ge=1)


class CreateDailySchedule(ScheduleBase):
    type: Literal["daily"] = "daily"
    time_hhmm: str = Field(description="HH:MM, 24h format")
    timezone: str = Field(default="UTC", description="IANA timezone, e.g. Europe/Moscow")

    @field_validator("time_hhmm")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError("time_hhmm must be HH:MM")
        hh, mm = v.split(":")
        hhi, mmi = int(hh), int(mm)
        if not (0 <= hhi <= 23 and 0 <= mmi <= 59):
            raise ValueError("time_hhmm must be a valid time")
        return v


class CreateIntervalSchedule(ScheduleBase):
    type: Literal["interval"] = "interval"
    every_minutes: int = Field(ge=1, le=60 * 24 * 365)


class CreateOnceSchedule(ScheduleBase):
    type: Literal["once"] = "once"
    run_at: datetime = Field(description="ISO datetime with timezone, e.g. 2025-12-17T10:30:00+03:00")

    @field_validator("run_at")
    @classmethod
    def _validate_run_at_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "run_at must include timezone offset, e.g. 2025-12-17T10:30:00+03:00 or ...Z"
            )
        return v


CreateSchedule = CreateDailySchedule | CreateIntervalSchedule | CreateOnceSchedule


class UpdateSchedule(BaseModel):
    # allow partial updates
    scenario_id: int | None = Field(default=None, ge=1)
    time_hhmm: str | None = None
    timezone: str | None = None
    every_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)
    run_at: datetime | None = None
    active: bool | None = None

    @field_validator("time_hhmm")
    @classmethod
    def _validate_hhmm_optional(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _HHMM_RE.match(v):
            raise ValueError("time_hhmm must be HH:MM")
        hh, mm = v.split(":")
        hhi, mmi = int(hh), int(mm)
        if not (0 <= hhi <= 23 and 0 <= mmi <= 59):
            raise ValueError("time_hhmm must be a valid time")
        return v

    @field_validator("run_at")
    @classmethod
    def _validate_run_at_optional_tzaware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "run_at must include timezone offset, e.g. 2025-12-17T10:30:00+03:00 or ...Z"
            )
        return v


class ScheduleOut(BaseModel):
    id: uuid.UUID
    token: str
    user_id: int
    scenario_id: int
    type: str
    time_hhmm: str | None
    timezone: str | None
    every_minutes: int | None
    run_at: datetime | None
    active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleKey(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    user_id: int = Field(ge=1)
    type: Literal["daily", "interval", "once"] = "daily"


class UpdateScheduleByKey(ScheduleKey, UpdateSchedule):
    """
    Update schedule identified by (token, user_id, type).
    Fields from UpdateSchedule are optional (patch semantics).
    """


class DeleteSchedulesByKey(ScheduleKey):
    """
    Delete schedules by (token, user_id, type).
    If type is omitted by client, send type="daily" or use /schedules/by_key/delete_all.
    """


class DeleteAllSchedulesByTokenUser(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    user_id: int = Field(ge=1)
