import time
from datetime import datetime
from typing import Optional

from .base_mailbox import BaseMailbox, MailboxAccount
from .proxy_utils import build_requests_proxy_config


class MailTmMailbox(BaseMailbox):
    """mail.tm mailbox using REST + Mercure SSE fallback."""

    def __init__(self, api_url: str = "https://api.mail.tm", domain: str = "", proxy: str = None):
        self.api = (api_url or "https://api.mail.tm").rstrip("/")
        self.domain = str(domain or "").strip()
        self.proxy = build_requests_proxy_config(proxy)
        self._token = ""
        self._address = ""
        self._account_id = ""

    def _headers(self, token: str = "") -> dict:
        headers = {"accept": "application/json", "content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        return headers

    def _request(self, method: str, endpoint: str, token: str = "", **kwargs):
        import requests

        url = f"{self.api}{endpoint}"
        last_response = None
        for attempt in range(1, 4):
            response = requests.request(
                method,
                url,
                headers=self._headers(token),
                proxies=self.proxy,
                timeout=15,
                **kwargs,
            )
            last_response = response
            if response.status_code != 429:
                return response
            self._log(f"[Mail.tm] HTTP 429 for {endpoint}, retry {attempt}/3 after 5s")
            if attempt < 3:
                time.sleep(5)
        return last_response

    def _resolve_domain(self) -> str:
        if self.domain:
            return self.domain
        response = self._request("GET", "/domains?page=1")
        if response.status_code >= 400:
            raise RuntimeError(
                f"[Mail.tm] failed to fetch domains: HTTP {response.status_code} body={response.text[:300]}"
            )
        payload = response.json() if response.text.strip().startswith(("{", "[")) else {}
        if isinstance(payload, dict):
            domains = payload.get("hydra:member") or payload.get("members") or payload.get("items") or []
        elif isinstance(payload, list):
            domains = payload
        else:
            domains = []

        first_domain = ""
        for item in domains:
            domain = str((item or {}).get("domain", "") or "").strip()
            if not domain:
                continue
            if not first_domain:
                first_domain = domain
            if bool((item or {}).get("isActive", True)):
                return domain
        if first_domain:
            return first_domain
        self._log(f"[Mail.tm] /domains payload: {str(payload)[:500]}")
        raise RuntimeError("[Mail.tm] no usable domain returned by API")

    def _resolve_token(self, account: MailboxAccount) -> str:
        token = ""
        if account and isinstance(account.extra, dict):
            token = str(account.extra.get("token") or "").strip()
        if not token and getattr(account, "account_id", "") and str(account.account_id).count(".") >= 2:
            token = str(account.account_id).strip()
        if not token:
            token = str(self._token or "").strip()
        return token

    def _resolve_account_id(self, account: MailboxAccount) -> str:
        account_id = ""
        if account and isinstance(account.extra, dict):
            account_id = str(account.extra.get("mailtm_account_id") or "").strip()
        if not account_id and getattr(account, "account_id", "") and str(account.account_id).count(".") < 2:
            account_id = str(account.account_id).strip()
        if not account_id:
            account_id = str(self._account_id or "").strip()
        return account_id

    def _fetch_messages(self, token: str) -> list:
        response = self._request("GET", "/messages?page=1", token=token)
        payload = response.json() if response.text.strip().startswith(("{", "[")) else {}
        if isinstance(payload, dict):
            messages = payload.get("hydra:member", []) or []
            self._log(f"[Mail.tm] fetched {len(messages)} messages (total={payload.get('hydra:totalItems')})")
            return messages
        if isinstance(payload, list):
            self._log(f"[Mail.tm] fetched {len(payload)} messages")
            return payload
        self._log(f"[Mail.tm] unexpected /messages payload: {str(payload)[:300]}")
        return []

    def _wait_for_message_event(self, token: str, account_id: str, timeout_seconds: int = 8) -> None:
        if not token or not account_id:
            return
        import requests

        try:
            with requests.get(
                "https://mercure.mail.tm/.well-known/mercure",
                params={"topic": f"/accounts/{account_id}"},
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                proxies=self.proxy,
                timeout=(10, timeout_seconds),
                stream=True,
            ) as response:
                if response.status_code >= 400:
                    self._log(f"[Mail.tm] SSE status={response.status_code}")
                    return
                for raw_line in response.iter_lines(decode_unicode=True):
                    self._checkpoint()
                    if not raw_line:
                        continue
                    line = str(raw_line).strip()
                    if line.startswith("data:"):
                        self._log(f"[Mail.tm] SSE event received for account {account_id}")
                        return
        except Exception as exc:
            self._log(f"[Mail.tm] SSE wait failed: {exc}")

    def get_email(self) -> MailboxAccount:
        import random
        import string

        username = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        password = "Test" + "".join(random.choices(string.digits, k=8)) + "!"
        domain = self._resolve_domain()
        address = f"{username}@{domain}"
        self._log(f"[Mail.tm] creating account: {address}")
        print(f"[Mail.tm] creating account: {address}")

        response = self._request("POST", "/accounts", json={"address": address, "password": password})
        if response.status_code >= 400 or not response.text.strip().startswith("{"):
            raise RuntimeError(
                f"[Mail.tm] account creation failed: HTTP {response.status_code} body={response.text[:300]}"
            )
        payload = response.json()
        self._account_id = str(payload.get("id") or "").strip()
        self._address = payload.get("address", address)

        token_response = self._request("POST", "/token", json={"address": self._address, "password": password})
        if token_response.status_code >= 400 or not token_response.text.strip().startswith(("{", "[")):
            raise RuntimeError(
                f"[Mail.tm] token fetch failed: HTTP {token_response.status_code} body={token_response.text[:300]}"
            )
        self._token = str(token_response.json().get("token", "") or "").strip()
        if not self._token:
            raise RuntimeError("[Mail.tm] token is empty")

        return MailboxAccount(
            email=self._address,
            account_id=self._account_id or self._token,
            extra={
                "token": self._token,
                "mailtm_account_id": self._account_id,
                "password": password,
            },
        )

    def get_current_ids(self, account: MailboxAccount) -> set:
        try:
            token = self._resolve_token(account)
            if not token:
                return set()
            messages = self._fetch_messages(token)
            return {
                str(m.get("id") or m.get("msgid") or "")
                for m in messages
                if str(m.get("id") or m.get("msgid") or "")
            }
        except Exception:
            return set()

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
        **kwargs,
    ) -> str:
        import re

        seen = set(before_ids or [])
        token = self._resolve_token(account)
        account_id = self._resolve_account_id(account)
        if not token:
            raise RuntimeError("[Mail.tm] missing bearer token")

        exclude_codes = {
            str(code).strip()
            for code in (kwargs.get("exclude_codes") or set())
            if str(code or "").strip()
        }
        otp_sent_at = kwargs.get("otp_sent_at")

        def _parse_message_timestamp(*values) -> Optional[float]:
            for value in values:
                if value in (None, ""):
                    continue
                text = str(value).strip()
                if not text:
                    continue
                try:
                    normalized = text.replace("Z", "+00:00")
                    return datetime.fromisoformat(normalized).timestamp()
                except ValueError:
                    continue
                try:
                    numeric = float(text)
                    return numeric / 1000 if numeric > 10_000_000_000 else numeric
                except (TypeError, ValueError):
                    continue
            return None

        def poll_once() -> Optional[str]:
            try:
                messages = self._fetch_messages(token)
                if not messages and account_id:
                    self._wait_for_message_event(token, account_id, timeout_seconds=8)
                    messages = self._fetch_messages(token)

                for msg in messages:
                    mid = str(msg.get("id") or msg.get("msgid") or "")
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)

                    try:
                        detail_response = self._request("GET", f"/messages/{mid}", token=token)
                        detail = detail_response.json()
                        sender = detail.get("from") or msg.get("from") or {}
                        html_parts = detail.get("html") or []
                        body = " ".join(
                            [
                                str(sender.get("address") or ""),
                                str(sender.get("name") or ""),
                                str(detail.get("text") or ""),
                                str(detail.get("subject") or ""),
                                " ".join(str(part or "") for part in html_parts),
                            ]
                        )
                    except Exception:
                        detail = {}
                        sender = msg.get("from") or {}
                        body = " ".join(
                            [
                                str(sender.get("address") or ""),
                                str(sender.get("name") or ""),
                                str(msg.get("subject") or ""),
                                str(msg.get("intro") or ""),
                            ]
                        )

                    subject = str(detail.get("subject") or msg.get("subject") or "")[:120]
                    self._log(f"[Mail.tm] message id={mid} subject={subject}")

                    message_ts = _parse_message_timestamp(
                        detail.get("createdAt"),
                        detail.get("updatedAt"),
                        msg.get("createdAt"),
                        msg.get("updatedAt"),
                    )
                    if otp_sent_at and message_ts and message_ts < float(otp_sent_at):
                        continue

                    body_lower = body.lower()
                    if keyword and keyword.lower() not in body_lower:
                        if keyword.lower() != "fotor" or not (
                            "support@fotor.com" in body_lower
                            or "fotor support" in body_lower
                            or "verify your email address for fotor registration" in body_lower
                            or "verification code" in body_lower
                        ):
                            continue

                    body = re.sub(
                        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
                        "",
                        body,
                    )
                    code = self._safe_extract(body, code_pattern)
                    if code and code in exclude_codes:
                        self._log(f"[Mail.tm] skip excluded OTP: {code}")
                        continue
                    if code:
                        self._log(f"[Mail.tm] matched OTP: {code}")
                        return code
            except Exception as exc:
                self._log(f"[Mail.tm] poll failed: {exc}")
            return None

        return self._run_polling_wait(timeout=timeout, poll_interval=2, poll_once=poll_once)
