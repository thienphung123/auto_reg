"""数据库模型 - SQLite via SQLModel"""
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, select
import json
from sqlalchemy import text
from .runtime_paths import get_runtime_file


def _utcnow():
    return datetime.now(timezone.utc)

DATABASE_FILE = get_runtime_file("account_manager.db")
DATABASE_URL = f"sqlite:///{DATABASE_FILE.as_posix()}"
engine = create_engine(DATABASE_URL)


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
    extra_json: str = "{}"   # JSON 存储平台自定义字段
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
    status: str        # success | failed
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


def save_account(account) -> 'AccountModel':
    """从 base_platform.Account 存入数据库（同平台同邮箱则更新）"""
    with Session(engine) as session:
        existing = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == account.platform)
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
            if account.platform != "fotor":
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
        referred_count = 0 if account.platform == "fotor" else int(extra.get("referred_count", 0) or 0)
        m = AccountModel(
            platform=account.platform,
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
        session.add(m)
        session.commit()
        session.refresh(m)
        return m


def _ensure_account_columns() -> None:
    expected_columns: dict[str, str] = {
        "ref_link": "TEXT NOT NULL DEFAULT ''",
        "parent_email": "TEXT NOT NULL DEFAULT ''",
        "referred_count": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        existing = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(accounts)")).fetchall()
        }
        for column_name, ddl in expected_columns.items():
            if column_name in existing:
                continue
            conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {column_name} {ddl}"))


def get_fotor_ref_parent(master_ref_link: str) -> tuple[str, str]:
    with Session(engine) as session:
        parent = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == "fotor")
            .where(AccountModel.referred_count < 20)
            .where(AccountModel.ref_link != "")
            .order_by(AccountModel.created_at.asc(), AccountModel.id.asc())
        ).first()
        if parent:
            return parent.email, parent.ref_link
    return "MASTER", master_ref_link


def increment_referral_count(parent_email: str) -> None:
    normalized = (parent_email or "").strip()
    if not normalized or normalized.upper() == "MASTER":
        return
    with Session(engine) as session:
        parent = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == "fotor")
            .where(AccountModel.email == normalized)
        ).first()
        if not parent:
            return
        parent.referred_count = int(parent.referred_count or 0) + 1
        parent.updated_at = _utcnow()
        session.add(parent)
        session.commit()


def repair_fotor_ref_counts() -> None:
    with Session(engine) as session:
        fotor_accounts = session.exec(
            select(AccountModel).where(AccountModel.platform == "fotor")
        ).all()
        child_count_by_parent: dict[str, int] = {}
        for account in fotor_accounts:
            parent_email = (account.parent_email or "").strip()
            if not parent_email or parent_email.upper() == "MASTER":
                continue
            child_count_by_parent[parent_email] = child_count_by_parent.get(parent_email, 0) + 1

        dirty = False
        for account in fotor_accounts:
            expected = int(child_count_by_parent.get(account.email, 0))
            if int(account.referred_count or 0) != expected:
                account.referred_count = expected
                account.updated_at = _utcnow()
                session.add(account)
                dirty = True
        if dirty:
            session.commit()


def init_db():
    SQLModel.metadata.create_all(engine)
    _ensure_account_columns()
    repair_fotor_ref_counts()


def get_session():
    with Session(engine) as session:
        yield session


# 定时任务模型
class ScheduledTaskModel(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"

    task_id: str = Field(primary_key=True)
    platform: str
    count: int = 1
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra_json: str = "{}"
    interval_type: str = "minutes"  # minutes | hours
    interval_value: int = 30
    paused: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, d: dict):
        self.extra_json = json.dumps(d, ensure_ascii=False)
