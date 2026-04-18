from datetime import datetime, timezone
import json
import threading
from typing import Optional

from sqlalchemy import text
from sqlmodel import Field, SQLModel, Session, create_engine, select

from .runtime_paths import get_runtime_file


def _utcnow():
    return datetime.now(timezone.utc)


def _normalize_platform_name(value: str) -> str:
    return str(value or "").strip().lower()


DATABASE_FILE = get_runtime_file("account_manager.db")
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"
engine = create_engine(DATABASE_URL)
_fotor_ref_lock = threading.Lock()


class AccountModel(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    email: str = Field(index=True)
    password: str
    user_id: str = ""
    region: str = ""
    token: str = ""
    status: str = "registered"
    trial_end_time: int = 0
    cashier_url: str = ""
    ref_link: str = Field(default="", index=True)
    parent_email: str = Field(default="", index=True)
    referred_count: int = Field(default=0, index=True)
    ref_pending_count: int = Field(default=0, index=True)
    extra_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)


class TaskLog(SQLModel, table=True):
    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str
    email: str
    status: str
    error: str = ""
    detail_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)


class ProxyModel(SQLModel, table=True):
    __tablename__ = "proxies"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    region: str = ""
    success_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    last_checked: Optional[datetime] = None


class ScheduledTaskModel(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"

    task_id: str = Field(primary_key=True)
    platform: str
    count: int = 1
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra_json: str = "{}"
    interval_type: str = "minutes"
    interval_value: int = 30
    paused: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)


def save_account(account) -> AccountModel:
    with Session(engine) as session:
        normalized_platform = _normalize_platform_name(account.platform)
        existing = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == normalized_platform)
            .where(AccountModel.email == account.email)
        ).first()

        if existing:
            existing.password = account.password
            existing.user_id = account.user_id or ""
            existing.region = account.region or ""
            existing.token = account.token or ""
            existing.status = account.status.value
            extra = account.extra or {}
            existing.extra_json = json.dumps(extra, ensure_ascii=False)
            existing.cashier_url = extra.get("cashier_url", "")
            existing.ref_link = extra.get("ref_link", existing.ref_link or "")
            existing.parent_email = extra.get("parent_email", existing.parent_email or "")
            if normalized_platform != "fotor":
                try:
                    existing.referred_count = int(extra.get("referred_count", existing.referred_count or 0) or 0)
                except (TypeError, ValueError):
                    existing.referred_count = existing.referred_count or 0
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        extra = account.extra or {}
        referred_count = 0 if normalized_platform == "fotor" else int(extra.get("referred_count", 0) or 0)
        row = AccountModel(
            platform=normalized_platform,
            email=account.email,
            password=account.password,
            user_id=account.user_id or "",
            region=account.region or "",
            token=account.token or "",
            status=account.status.value,
            extra_json=json.dumps(extra, ensure_ascii=False),
            cashier_url=extra.get("cashier_url", ""),
            ref_link=extra.get("ref_link", ""),
            parent_email=extra.get("parent_email", ""),
            referred_count=referred_count,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _ensure_account_columns() -> None:
    expected_columns = {
        "ref_link": "TEXT NOT NULL DEFAULT ''",
        "parent_email": "TEXT NOT NULL DEFAULT ''",
        "referred_count": "INTEGER NOT NULL DEFAULT 0",
        "ref_pending_count": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)")).fetchall()}
        for column_name, ddl in expected_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {ddl}"))


def repair_account_platform_names() -> None:
    with Session(engine) as session:
        accounts = session.exec(select(AccountModel)).all()
        dirty = False
        for account in accounts:
            normalized = _normalize_platform_name(account.platform)
            if account.platform != normalized:
                account.platform = normalized
                account.updated_at = _utcnow()
                session.add(account)
                dirty = True
        if dirty:
            session.commit()


def repair_fotor_ref_counts() -> None:
    with Session(engine) as session:
        fotor_accounts = session.exec(select(AccountModel).where(AccountModel.platform == "fotor")).all()
        child_count_by_parent: dict[str, int] = {}
        for account in fotor_accounts:
            parent_email = (account.parent_email or "").strip()
            if not parent_email or parent_email.upper() == "MASTER":
                continue
            child_count_by_parent[parent_email] = child_count_by_parent.get(parent_email, 0) + 1

        dirty = False
        for account in fotor_accounts:
            expected = int(child_count_by_parent.get(account.email, 0))
            if int(account.referred_count or 0) != expected or int(account.ref_pending_count or 0) != 0:
                account.referred_count = expected
                account.ref_pending_count = 0
                account.updated_at = _utcnow()
                session.add(account)
                dirty = True
        if dirty:
            session.commit()


def get_fotor_ref_parent(master_ref_link: str) -> tuple[str, str]:
    with _fotor_ref_lock:
        with Session(engine) as session:
            parent = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "fotor")
                .where(AccountModel.ref_link != "")
                .where((AccountModel.referred_count + AccountModel.ref_pending_count) < 20)
                .order_by(AccountModel.created_at.asc(), AccountModel.id.asc())
            ).first()
            if parent:
                parent.ref_pending_count = int(parent.ref_pending_count or 0) + 1
                parent.updated_at = _utcnow()
                session.add(parent)
                session.commit()
                return parent.email, parent.ref_link
    return "MASTER", master_ref_link


def increment_referral_count(parent_email: str) -> None:
    normalized = (parent_email or "").strip()
    if not normalized or normalized.upper() == "MASTER":
        return
    with _fotor_ref_lock:
        with Session(engine) as session:
            parent = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "fotor")
                .where(AccountModel.email == normalized)
            ).first()
            if not parent:
                return
            parent.referred_count = int(parent.referred_count or 0) + 1
            parent.ref_pending_count = max(int(parent.ref_pending_count or 0) - 1, 0)
            parent.updated_at = _utcnow()
            session.add(parent)
            session.commit()


def release_fotor_ref_parent(parent_email: str) -> None:
    normalized = (parent_email or "").strip()
    if not normalized or normalized.upper() == "MASTER":
        return
    with _fotor_ref_lock:
        with Session(engine) as session:
            parent = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "fotor")
                .where(AccountModel.email == normalized)
            ).first()
            if not parent:
                return
            parent.ref_pending_count = max(int(parent.ref_pending_count or 0) - 1, 0)
            parent.updated_at = _utcnow()
            session.add(parent)
            session.commit()


def init_db():
    SQLModel.metadata.create_all(engine)
    _ensure_account_columns()
    repair_account_platform_names()
    repair_fotor_ref_counts()


def get_session():
    with Session(engine) as session:
        yield session
