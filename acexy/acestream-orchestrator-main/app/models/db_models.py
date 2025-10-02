from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Boolean, Text, JSON
from datetime import datetime, timezone

class Base(DeclarativeBase):
    pass

class EngineRow(Base):
    __tablename__ = "engines"
    engine_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    container_id: Mapped[str | None] = mapped_column(String(128))
    container_name: Mapped[str | None] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(128))
    port: Mapped[int] = mapped_column(Integer)
    labels: Mapped[dict | None] = mapped_column(JSON, default={})
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class StreamRow(Base):
    __tablename__ = "streams"
    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    engine_key: Mapped[str] = mapped_column(String(128))
    key_type: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(256))
    playback_session_id: Mapped[str] = mapped_column(String(256))
    stat_url: Mapped[str] = mapped_column(Text)
    command_url: Mapped[str] = mapped_column(Text)
    is_live: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="started")

class StatRow(Base):
    __tablename__ = "stream_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[str] = mapped_column(String(256), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    peers: Mapped[int | None] = mapped_column(Integer)
    speed_down: Mapped[int | None] = mapped_column(Integer)
    speed_up: Mapped[int | None] = mapped_column(Integer)
    downloaded: Mapped[int | None] = mapped_column(Integer)
    uploaded: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
