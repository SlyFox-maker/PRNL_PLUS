import os
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, String, Text, ForeignKey, DateTime, Integer, BigInteger, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = 'accounts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20))
    portal: Mapped[str] = mapped_column(String(255), default='')
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(ForeignKey('accounts.id'))
    state: Mapped[str] = mapped_column(String(30), default='stopped')
    resources: Mapped[str] = mapped_column(String(20), default='default')
    room: Mapped[str] = mapped_column(Text, default='')
    link_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    rx_total: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0', nullable=False)
    tx_total: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0', nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(20), default='', server_default='', nullable=False)
    recipient_label: Mapped[str] = mapped_column(String(100), default='', server_default='', nullable=False)
    recipient_value: Mapped[str] = mapped_column(String(500), default='', server_default='', nullable=False)
    vpn_enabled: Mapped[bool] = mapped_column(default=False, server_default='false', nullable=False)
    matrix_user_id: Mapped[str] = mapped_column(String(255), default='', server_default='', nullable=False)
    matrix_display_name: Mapped[str] = mapped_column(String(255), default='', server_default='', nullable=False)
    matrix_has_avatar: Mapped[bool] = mapped_column(default=False, server_default='false', nullable=False)
    matrix_room_id: Mapped[str] = mapped_column(String(255), default='', server_default='', nullable=False)
    matrix_greeting_sent: Mapped[bool] = mapped_column(default=False, server_default='false', nullable=False)
    matrix_retry_exhausted: Mapped[bool] = mapped_column(default=False, server_default='false', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Log(Base):
    __tablename__ = 'logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Destination(Base):
    __tablename__ = 'destinations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    port: Mapped[int] = mapped_column(Integer)
    connection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    __table_args__ = (Index('ix_destinations_session_connection_id', 'session_id', 'connection_id'),)


class AppSetting(Base):
    __tablename__ = 'app_settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


engine = create_engine(os.environ.get('DATABASE_URL', 'sqlite:////tmp/wlp-dev.db'), pool_pre_ping=True)
DB = sessionmaker(engine, expire_on_commit=False)
