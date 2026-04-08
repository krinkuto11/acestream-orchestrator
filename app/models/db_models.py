from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, DateTime, Boolean, Text, JSON, ForeignKey
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
    forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    vpn_container: Mapped[str | None] = mapped_column(String(128))

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

class ConfigRow(Base):
    __tablename__ = "config"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # engine, stream, vpn, health, system
    category: Mapped[str] = mapped_column(String(32))  # created, deleted, started, ended, etc.
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON, default={})
    container_id: Mapped[str | None] = mapped_column(String(128))
    stream_id: Mapped[str | None] = mapped_column(String(256))


class DashboardMetricSampleRow(Base):
    __tablename__ = "dashboard_metric_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    proxy_ingress_rate_bps: Mapped[float] = mapped_column(default=0.0)
    proxy_egress_rate_bps: Mapped[float] = mapped_column(default=0.0)
    active_streams: Mapped[int] = mapped_column(Integer, default=0)
    active_clients: Mapped[int] = mapped_column(Integer, default=0)
    success_rate_percent: Mapped[float] = mapped_column(default=100.0)
    ttfb_p95_ms: Mapped[float] = mapped_column(default=0.0)
    docker_cpu_percent: Mapped[float] = mapped_column(default=0.0)
    docker_memory_bytes: Mapped[float] = mapped_column(default=0.0)


class RuntimeSettingsRow(Base):
    """Single-row settings aggregate for runtime configuration categories."""

    __tablename__ = "runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engine_config: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    orchestrator_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # proxy_settings stores dynamic proxy config including proxy_prebuffer_seconds.
    proxy_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    vpn_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    loop_detection_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    vpn_credentials: Mapped[list["VPNCredentialRow"]] = relationship(
        back_populates="settings",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class VPNCredentialRow(Base):
    """VPN credential records stored separately to model nested credential arrays safely."""

    __tablename__ = "vpn_credentials"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    settings_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("runtime_settings.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str | None] = mapped_column(String(128))
    protocol: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    settings: Mapped[RuntimeSettingsRow] = relationship(back_populates="vpn_credentials")
