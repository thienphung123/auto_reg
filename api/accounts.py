import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from core.db import AccountModel, get_session, repair_fotor_ref_counts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    platform: str
    email: str
    password: str
    status: str = "registered"
    token: str = ""
    cashier_url: str = ""
    ref_link: str = ""
    parent_email: str = ""
    referred_count: int = 0


class AccountUpdate(BaseModel):
    status: Optional[str] = None
    token: Optional[str] = None
    cashier_url: Optional[str] = None
    ref_link: Optional[str] = None
    parent_email: Optional[str] = None
    referred_count: Optional[int] = None


class ImportRequest(BaseModel):
    platform: str
    lines: list[str]


class BatchDeleteRequest(BaseModel):
    ids: list[int]


def _normalize_platform(platform: Optional[str]) -> Optional[str]:
    if platform is None:
        return None
    return str(platform).strip().lower()


def _repair_if_needed(platform: Optional[str]) -> None:
    normalized = _normalize_platform(platform)
    if normalized in (None, "fotor", ""):
        repair_fotor_ref_counts()


@router.get("")
def list_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
):
    _repair_if_needed(platform)
    normalized_platform = _normalize_platform(platform)
    q = select(AccountModel)
    if normalized_platform:
        q = q.where(AccountModel.platform == normalized_platform)
    if status:
        q = q.where(AccountModel.status == status)
    if email:
        q = q.where(AccountModel.email.contains(email))
    total = len(session.exec(q).all())
    items = session.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "items": items}


@router.post("")
def create_account(body: AccountCreate, session: Session = Depends(get_session)):
    acc = AccountModel(
        platform=_normalize_platform(body.platform) or body.platform,
        email=body.email,
        password=body.password,
        status=body.status,
        token=body.token,
        cashier_url=body.cashier_url,
        ref_link=body.ref_link,
        parent_email=body.parent_email,
        referred_count=body.referred_count,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    _repair_if_needed(None)
    accounts = session.exec(select(AccountModel)).all()
    platforms: dict[str, int] = {}
    statuses: dict[str, int] = {}
    max_ref_count = 0
    for acc in accounts:
        platform = _normalize_platform(acc.platform) or acc.platform
        platforms[platform] = platforms.get(platform, 0) + 1
        statuses[acc.status] = statuses.get(acc.status, 0) + 1
        if int(acc.referred_count or 0) >= 20:
            max_ref_count += 1
    return {"total": len(accounts), "by_platform": platforms, "by_status": statuses, "max_ref_count": max_ref_count}


@router.get("/export")
def export_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    _repair_if_needed(platform)
    normalized_platform = _normalize_platform(platform)
    q = select(AccountModel)
    if normalized_platform:
        q = q.where(AccountModel.platform == normalized_platform)
    if status:
        q = q.where(AccountModel.status == status)
    accounts = session.exec(q).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "platform",
            "email",
            "password",
            "user_id",
            "region",
            "status",
            "cashier_url",
            "ref_link",
            "parent_email",
            "referred_count",
            "created_at",
        ]
    )
    for acc in accounts:
        writer.writerow(
            [
                acc.platform,
                acc.email,
                acc.password,
                acc.user_id,
                acc.region,
                acc.status,
                acc.cashier_url,
                acc.ref_link,
                acc.parent_email,
                acc.referred_count,
                acc.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts.csv"},
    )


@router.post("/import")
def import_accounts(body: ImportRequest, session: Session = Depends(get_session)):
    created = 0
    for line in body.lines:
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        email, password = parts[0], parts[1]
        extra = parts[2] if len(parts) > 2 else ""
        if extra:
            try:
                json.loads(extra)
            except (json.JSONDecodeError, ValueError):
                extra = "{}"
        else:
            extra = "{}"
        acc = AccountModel(
            platform=_normalize_platform(body.platform) or body.platform,
            email=email,
            password=password,
            extra_json=extra,
        )
        session.add(acc)
        created += 1
    session.commit()
    return {"created": created}


@router.post("/batch-delete")
def batch_delete_accounts(body: BatchDeleteRequest, session: Session = Depends(get_session)):
    if not body.ids:
        raise HTTPException(400, "Account ID list cannot be empty")
    if len(body.ids) > 1000:
        raise HTTPException(400, "At most 1000 accounts per delete")

    deleted_count = 0
    not_found_ids = []
    try:
        for account_id in body.ids:
            acc = session.get(AccountModel, account_id)
            if acc:
                session.delete(acc)
                deleted_count += 1
            else:
                not_found_ids.append(account_id)
        session.commit()
        return {
            "deleted": deleted_count,
            "not_found": not_found_ids,
            "total_requested": len(body.ids),
        }
    except Exception as e:
        session.rollback()
        logger.exception("Batch delete failed")
        raise HTTPException(500, f"Batch delete failed: {str(e)}")


@router.post("/check-all")
def check_all_accounts(platform: Optional[str] = None, background_tasks: BackgroundTasks = None):
    from core.scheduler import scheduler

    background_tasks.add_task(scheduler.check_accounts_valid, platform)
    return {"message": "Batch check started"}


@router.get("/{account_id}")
def get_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    return acc


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    if body.status is not None:
        acc.status = body.status
    if body.token is not None:
        acc.token = body.token
    if body.cashier_url is not None:
        acc.cashier_url = body.cashier_url
    if body.ref_link is not None:
        acc.ref_link = body.ref_link
    if body.parent_email is not None:
        acc.parent_email = body.parent_email
    if body.referred_count is not None:
        acc.referred_count = body.referred_count
    acc.updated_at = datetime.now(timezone.utc)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.delete("/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    session.delete(acc)
    session.commit()
    return {"ok": True}


@router.post("/{account_id}/check")
def check_account(account_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    background_tasks.add_task(_do_check, account_id)
    return {"message": "Check started"}


def _do_check(account_id: int):
    from core.db import engine
    from core.base_platform import Account, RegisterConfig
    from core.registry import get

    with Session(engine) as s:
        acc = s.get(AccountModel, account_id)
    if not acc:
        return
    try:
        PlatformCls = get(acc.platform)
        plugin = PlatformCls(config=RegisterConfig())
        obj = Account(
            platform=acc.platform,
            email=acc.email,
            password=acc.password,
            user_id=acc.user_id,
            region=acc.region,
            token=acc.token,
            extra=json.loads(acc.extra_json or "{}"),
        )
        valid = plugin.check_valid(obj)
        with Session(engine) as s:
            current = s.get(AccountModel, account_id)
            if current:
                if current.platform != "chatgpt":
                    current.status = current.status if valid else "invalid"
                current.updated_at = datetime.now(timezone.utc)
                s.add(current)
                s.commit()
    except Exception:
        logger.exception("Account check failed for %s", account_id)
