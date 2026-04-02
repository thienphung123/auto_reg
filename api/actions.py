"""平台操作 API - 通用接口，各平台通过 get_platform_actions/execute_action 实现"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Any, List, Optional
from core.db import AccountModel, get_session
from core.registry import get
from core.base_platform import RegisterConfig
from core.config_store import config_store

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionRequest(BaseModel):
    params: dict = {}


class BatchActionRequest(BaseModel):
    account_ids: Optional[List[int]] = None
    params: dict = {}


@router.get("/{platform}")
def list_actions(platform: str):
    """获取平台支持的操作列表"""
    PlatformCls = get(platform)
    instance = PlatformCls(config=RegisterConfig(extra=config_store.get_all()))
    return {"actions": instance.get_platform_actions()}


@router.post("/{platform}/batch/{action_id}")
def execute_batch_action(
    platform: str,
    action_id: str,
    body: BatchActionRequest,
    session: Session = Depends(get_session),
):
    """批量执行平台操作"""
    if not body.account_ids:
        raise HTTPException(400, "账号 ID 列表不能为空")

    PlatformCls = get(platform)
    instance = PlatformCls(config=RegisterConfig(extra=config_store.get_all()))

    # 检查是否支持批量操作
    if action_id not in ["upload_sub2api", "upload_cpa", "upload_tm"]:
        raise HTTPException(400, f"操作 {action_id} 不支持批量执行")

    results = {
        "success": 0,
        "failed": 0,
        "total": len(body.account_ids),
        "items": []
    }

    for account_id in body.account_ids:
        acc_model = session.get(AccountModel, account_id)
        if not acc_model or acc_model.platform != platform:
            results["items"].append({
                "id": account_id,
                "ok": False,
                "msg": "账号不存在"
            })
            results["failed"] += 1
            continue

        from core.base_platform import Account, AccountStatus
        account = Account(
            platform=acc_model.platform,
            email=acc_model.email,
            password=acc_model.password,
            user_id=acc_model.user_id,
            token=acc_model.token,
            status=AccountStatus(acc_model.status),
            extra=acc_model.get_extra(),
        )

        try:
            result = instance.execute_action(action_id, account, body.params)
            ok = bool(result.get("ok"))
            msg = result.get("data") or result.get("error") or "操作完成"
            results["items"].append({
                "id": account_id,
                "email": acc_model.email,
                "ok": ok,
                "msg": msg
            })
            if ok:
                results["success"] += 1
                # 更新同步状态
                if action_id == "upload_cpa":
                    from services.chatgpt_sync import update_account_model_cpa_sync
                    update_account_model_cpa_sync(acc_model, True, msg, session=session, commit=False)
                elif action_id == "upload_sub2api":
                    _update_sub2api_sync_result(acc_model, True, msg, session, commit=False)
            else:
                results["failed"] += 1
        except Exception as e:
            results["items"].append({
                "id": account_id,
                "email": acc_model.email,
                "ok": False,
                "msg": str(e)
            })
            results["failed"] += 1

    session.commit()
    return results


@router.post("/{platform}/{account_id}/{action_id}")
def execute_action(
    platform: str,
    account_id: int,
    action_id: str,
    body: ActionRequest,
    session: Session = Depends(get_session),
):
    """执行平台特定操作"""
    acc_model = session.get(AccountModel, account_id)
    if not acc_model or acc_model.platform != platform:
        raise HTTPException(404, "账号不存在")

    PlatformCls = get(platform)
    instance = PlatformCls(config=RegisterConfig(extra=config_store.get_all()))

    from core.base_platform import Account, AccountStatus
    account = Account(
        platform=acc_model.platform,
        email=acc_model.email,
        password=acc_model.password,
        user_id=acc_model.user_id,
        token=acc_model.token,
        status=AccountStatus(acc_model.status),
        extra=acc_model.get_extra(),
    )

    try:
        result = instance.execute_action(action_id, account, body.params)
        if platform == "chatgpt" and action_id == "upload_cpa":
            from services.chatgpt_sync import update_account_model_cpa_sync

            sync_msg = result.get("data") or result.get("error") or ""
            update_account_model_cpa_sync(
                acc_model,
                bool(result.get("ok")),
                str(sync_msg),
                session=session,
                commit=False,
            )
        elif platform == "chatgpt" and action_id == "upload_sub2api":
            # 记录 Sub2API 上传结果
            sync_msg = result.get("data") or result.get("error") or ""
            _update_sub2api_sync_result(acc_model, bool(result.get("ok")), str(sync_msg), session, commit=False)
        # 若操作返回了新 token，更新数据库
        if result.get("ok") and result.get("data", {}) and isinstance(result["data"], dict):
            data = result["data"]
            tracked_keys = {"access_token", "accessToken", "refreshToken", "clientId", "clientSecret", "webAccessToken"}
            if tracked_keys.intersection(data.keys()):
                extra = acc_model.get_extra()
                extra.update(data)
                acc_model.set_extra(extra)
                if data.get("access_token"):
                    acc_model.token = data["access_token"]
                elif data.get("accessToken"):
                    acc_model.token = data["accessToken"]
                from datetime import datetime, timezone
                acc_model.updated_at = datetime.now(timezone.utc)
                session.add(acc_model)
        session.commit()
        return result
    except NotImplementedError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/{platform}/batch/{action_id}")
def execute_batch_action(
    platform: str,
    action_id: str,
    body: BatchActionRequest,
    session: Session = Depends(get_session),
):
    """批量执行平台操作"""
    if not body.account_ids:
        raise HTTPException(400, "账号 ID 列表不能为空")

    PlatformCls = get(platform)
    instance = PlatformCls(config=RegisterConfig(extra=config_store.get_all()))

    # 检查是否支持批量操作
    if action_id not in ["upload_sub2api", "upload_cpa", "upload_tm"]:
        raise HTTPException(400, f"操作 {action_id} 不支持批量执行")

    results = {
        "success": 0,
        "failed": 0,
        "total": len(body.account_ids),
        "items": []
    }

    for account_id in body.account_ids:
        acc_model = session.get(AccountModel, account_id)
        if not acc_model or acc_model.platform != platform:
            results["items"].append({
                "id": account_id,
                "ok": False,
                "msg": "账号不存在"
            })
            results["failed"] += 1
            continue

        from core.base_platform import Account, AccountStatus
        account = Account(
            platform=acc_model.platform,
            email=acc_model.email,
            password=acc_model.password,
            user_id=acc_model.user_id,
            token=acc_model.token,
            status=AccountStatus(acc_model.status),
            extra=acc_model.get_extra(),
        )

        try:
            result = instance.execute_action(action_id, account, body.params)
            ok = bool(result.get("ok"))
            msg = result.get("data") or result.get("error") or "操作完成"
            results["items"].append({
                "id": account_id,
                "email": acc_model.email,
                "ok": ok,
                "msg": msg
            })
            if ok:
                results["success"] += 1
                # 更新同步状态
                if action_id == "upload_cpa":
                    from services.chatgpt_sync import update_account_model_cpa_sync
                    update_account_model_cpa_sync(acc_model, True, msg, session=session, commit=False)
                elif action_id == "upload_sub2api":
                    _update_sub2api_sync_result(acc_model, True, msg, session, commit=False)
            else:
                results["failed"] += 1
        except Exception as e:
            results["items"].append({
                "id": account_id,
                "email": acc_model.email,
                "ok": False,
                "msg": str(e)
            })
            results["failed"] += 1

    session.commit()
    return results


def _update_sub2api_sync_result(acc_model, ok: bool, msg: str, session: Session, commit: bool = True):
    """更新 Sub2API 同步状态"""
    extra = acc_model.get_extra()
    sync_statuses = extra.get("sync_statuses", {})
    if not isinstance(sync_statuses, dict):
        sync_statuses = {}

    state = sync_statuses.get("sub2api", {})
    if not isinstance(state, dict):
        state = {}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    state["last_attempt_ok"] = bool(ok)
    state["last_message"] = msg
    state["last_attempt_at"] = now
    state["uploaded"] = bool(state.get("uploaded")) or bool(ok)
    if ok:
        state["uploaded_at"] = now

    sync_statuses["sub2api"] = state
    extra["sync_statuses"] = sync_statuses
    acc_model.set_extra(extra)
    acc_model.updated_at = datetime.now(timezone.utc)
    session.add(acc_model)
    if commit:
        session.commit()
        session.refresh(acc_model)
    return state
