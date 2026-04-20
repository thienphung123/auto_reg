from __future__ import annotations

import logging
import os

import httpx


logger = logging.getLogger(__name__)


def get_dbc_balance() -> float:
    username = str(os.getenv("DBC_USERNAME", "")).strip()
    password = str(os.getenv("DBC_PASSWORD", "")).strip()
    if not username or not password:
        return 0.0

    url = "http://api.dbcapi.me/api/user"
    payload = {"username": username, "password": password}
    headers = {"Accept": "application/json"}

    try:
        response = httpx.post(url, data=payload, headers=headers, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            balance_in_cents = data.get("balance", 0)
            return round(float(balance_in_cents) / 100, 3)
    except Exception as e:
        logger.warning("[DBC_ERROR] Lỗi check số dư: %s", e)
    return 0.0
