from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .db import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = 'users'

    id: int = db.Column(db.Integer, primary_key=True)
    username: str = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email: str = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash: str = db.Column(db.String(64), nullable=False)
    salt: str = db.Column(db.String(32), nullable=False)
    display_name: str = db.Column(db.String(128), nullable=False)
    country: str = db.Column(db.String(64), default='')
    bio: str = db.Column(db.Text, default='')
    created_at: datetime = db.Column(db.DateTime, default=utcnow)
    email_verified: bool = db.Column(db.Boolean, default=False)
    verify_token: str | None = db.Column(db.String(64))
    reset_token: str | None = db.Column(db.String(64))
    steam_account_id: int | None = db.Column(db.BigInteger)
    soft_currency: int = db.Column(db.Integer, default=500000)
    hard_currency: int = db.Column(db.Integer, default=0)
    garage_slots: int = db.Column(db.Integer, default=8)
    garage_json: str = db.Column(db.Text, default='{}')

    tokens = db.relationship('GameToken', back_populates='user', cascade='all, delete-orphan')
    notifications = db.relationship('Notification', back_populates='user', cascade='all, delete-orphan')


class GameToken(db.Model):
    __tablename__ = 'game_tokens'

    id: int = db.Column(db.Integer, primary_key=True)
    user_id: int = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token: str = db.Column(db.String(80), unique=True, nullable=False, index=True)
    created_at: datetime = db.Column(db.DateTime, default=utcnow)
    revoked_at: datetime | None = db.Column(db.DateTime)

    user = db.relationship('User', back_populates='tokens')


def generate_game_token() -> str:
    return 'df_' + secrets.token_urlsafe(32)


class Club(db.Model):
    __tablename__ = 'clubs'

    id: str = db.Column(db.String(64), primary_key=True)
    name: str = db.Column(db.String(128), nullable=False)
    description: str = db.Column(db.Text, default='')
    visibility: str = db.Column(db.String(16), default='public')
    join_policy: str = db.Column(db.String(16), default='open')
    created_by: int = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at: datetime = db.Column(db.DateTime, default=utcnow)
    # JSON blobs mirroring the reference club document shape.
    admins_json: str = db.Column(db.Text, default='[]')          # [username]
    pending_requests_json: str = db.Column(db.Text, default='[]')  # [{username, requested_at}]
    invites_json: str = db.Column(db.Text, default='[]')         # [{username, invited_by, created_at}]
    cooldowns_json: str = db.Column(db.Text, default='{}')       # {username: iso-until}

    owner = db.relationship('User', foreign_keys=[created_by])
    members = db.relationship('ClubMember', back_populates='club', cascade='all, delete-orphan')


class ClubMember(db.Model):
    __tablename__ = 'club_members'

    id: int = db.Column(db.Integer, primary_key=True)
    club_id: str = db.Column(db.String(64), db.ForeignKey('clubs.id'), nullable=False, index=True)
    user_id: int = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    role: str = db.Column(db.String(16), default='member')

    club = db.relationship('Club', back_populates='members')
    user = db.relationship('User')


class ClubInvite(db.Model):
    """Shareable invite link for a club (revoked in place to keep audit info)."""

    __tablename__ = 'club_invites'

    id: int = db.Column(db.Integer, primary_key=True)
    token: str = db.Column(db.String(64), unique=True, nullable=False, index=True)
    club_id: str = db.Column(db.String(64), db.ForeignKey('clubs.id'), nullable=False)
    created_by: int = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at: datetime = db.Column(db.DateTime, default=utcnow)
    accepted_at: datetime | None = db.Column(db.DateTime)
    revoked_at: datetime | None = db.Column(db.DateTime)


class ChampionshipDraft(db.Model):
    """Unpublished championship builder state for one club."""

    __tablename__ = 'championship_drafts'

    id: str = db.Column(db.String(64), primary_key=True)
    club_id: str = db.Column(db.String(64), db.ForeignKey('clubs.id'), nullable=False, index=True)
    owner: str = db.Column(db.String(64), nullable=False)
    created_at: datetime = db.Column(db.DateTime, default=utcnow)
    data_json: str = db.Column(db.Text, nullable=False)
    # Permanent drafts survive submit (reusable templates); regular ones are
    # deleted when the championship is created.
    permanent: bool = db.Column(db.Boolean, default=False)


class Event(db.Model):
    __tablename__ = 'events'

    id: str = db.Column(db.String(64), primary_key=True)
    schema_version: int = db.Column(db.Integer, default=2)
    name: str = db.Column(db.String(255), nullable=False)
    type: str | None = db.Column(db.String(32))
    location: str | None = db.Column(db.String(64))
    car_class: str | None = db.Column(db.String(64))
    surface: str | None = db.Column(db.String(32))
    conditions: str | None = db.Column(db.String(64))
    stages_json: str = db.Column(db.Text, default='[]')
    settings_json: str = db.Column(db.Text, default='{}')
    legs_json: str = db.Column(db.Text, default='[]')
    start_time: datetime | None = db.Column(db.DateTime)
    end_time: datetime | None = db.Column(db.DateTime)
    active: bool = db.Column(db.Boolean, default=True)
    featured: bool = db.Column(db.Boolean, default=False)
    club_id: str | None = db.Column(db.String(64), db.ForeignKey('clubs.id'), index=True)
    system: bool = db.Column(db.Boolean, default=False)
    slot_id: str | None = db.Column(db.String(128), unique=True)

    def to_dict(self) -> dict:
        import json
        return {
            'id': self.id,
            'schema_version': self.schema_version,
            'name': self.name,
            'type': self.type,
            'location': self.location,
            'car_class': self.car_class,
            'surface': self.surface,
            'conditions': self.conditions,
            'stages': json.loads(self.stages_json or '[]'),
            'settings': json.loads(self.settings_json or '{}'),
            'events': json.loads(self.legs_json or '[]'),
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'active': self.active,
            'featured': self.featured,
            'club_id': self.club_id,
            'system': self.system,
            'slot_id': self.slot_id,
        }


class Result(db.Model):
    __tablename__ = 'results'

    id: int = db.Column(db.Integer, primary_key=True)
    event_id: str = db.Column(db.String(64), db.ForeignKey('events.id'), nullable=False, index=True)
    username: str = db.Column(db.String(64), nullable=False, index=True)
    car: str | None = db.Column(db.String(128))
    vehicle_id: int | None = db.Column(db.Integer)
    total_time_ms: int = db.Column(db.BigInteger, default=0)
    attempts_used: int = db.Column(db.Integer, default=0)
    stages_json: str = db.Column(db.Text, default='[]')
    in_progress_json: str = db.Column(db.Text, default='{}')

    __table_args__ = (db.UniqueConstraint('event_id', 'username'),)


class TimeTrialEntry(db.Model):
    __tablename__ = 'time_trials'

    id: int = db.Column(db.Integer, primary_key=True)
    vclass: str = db.Column(db.String(8), nullable=False)
    track: str = db.Column(db.String(8), nullable=False)
    conditions: str = db.Column(db.String(8), nullable=False)
    category: int = db.Column(db.Integer, nullable=False)
    username: str = db.Column(db.String(64), nullable=False, index=True)
    stage_time_ms: int = db.Column(db.BigInteger, nullable=False)
    vehicle_id: int | None = db.Column(db.Integer)
    livery_id: int = db.Column(db.Integer, default=0)
    nationality_id: int = db.Column(db.Integer, default=0)
    using_wheel: bool = db.Column(db.Boolean, default=False)
    using_assists: bool = db.Column(db.Boolean, default=False)
    ghost_data_b64: str | None = db.Column(db.Text)
    submitted_at: datetime = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint('vclass', 'track', 'conditions', 'category', 'username'),
    )


class Notification(db.Model):
    __tablename__ = 'notifications'

    id: int = db.Column(db.Integer, primary_key=True)
    user_id: int = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    kind: str = db.Column(db.String(64), nullable=False)
    payload_json: str = db.Column(db.Text, default='{}')
    read: bool = db.Column(db.Boolean, default=False)
    created_at: datetime = db.Column(db.DateTime, default=utcnow)

    user = db.relationship('User', back_populates='notifications')
