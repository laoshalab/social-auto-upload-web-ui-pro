"""
Settings reader — all settings stored in SQLite `settings` table.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

from conf import BASE_DIR

DB_PATH = BASE_DIR / "db" / "database.db"


def _db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def read_settings() -> dict:
    try:
        conn = _db_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        result = {}
        for row in rows:
            val = row["value"]
            try:
                result[row["key"]] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                result[row["key"]] = val
        return result
    except Exception:
        return {}


def write_setting(key: str, value):
    conn = _db_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_proxy_url() -> str | None:
    val = read_settings().get("proxyUrl")
    return val if val else None


_IP_ENDPOINTS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
)


def _is_public_ip(value: str) -> bool:
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value):
        parts = [int(part) for part in value.split(".")]
        if any(part > 255 for part in parts):
            return False
        if parts[0] in (0, 10, 127) or (parts[0] == 192 and parts[1] == 168):
            return False
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return False
        return True
    return bool(re.fullmatch(r"[0-9a-fA-F:]+", value)) and value.count(":") >= 2 and value != "::1"


def _parse_ip(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for key in ("ip", "query"):
            ip = data.get(key)
            if isinstance(ip, str) and _is_public_ip(ip.strip()):
                return ip.strip()
        return None
    token = text.split()[0]
    return token if _is_public_ip(token) else None


def _redact_proxy(message: str) -> str:
    message = re.sub(r"://[^/@\s]+@", "://", message or "")
    return message.split("\n")[0][:300]


def _friendly_proxy_error(exc: Exception) -> str:
    text = _redact_proxy(str(exc)).lower()
    if "connection refused" in text or "errno 111" in text:
        return "无法连接代理，连接被拒绝"
    if "timed out" in text or "timeout" in text:
        return "连接代理超时"
    if "407" in text or "proxy authentication" in text:
        return "代理认证失败，请检查用户名和密码"
    if "name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text:
        return "无法解析代理地址"
    return _redact_proxy(str(exc)) or "连接失败"


def check_proxy_connection(proxy_url: str, timeout: float = 4) -> dict:
    """通过代理请求公网 IP。成功返回 ip，失败返回 error，不含代理账号密码。

    最多试两个地址，单个地址等待 timeout 秒。连不上时尽快失败。
    """
    url = (proxy_url or "").strip()
    if not url:
        return {"ok": False, "ip": "", "error": "未填写代理地址"}
    if "://" not in url:
        url = "http://" + url
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        return {"ok": False, "ip": "", "error": "仅支持 HTTP 代理"}

    import requests

    proxies = {"http": url, "https": url}
    last_error = "连接失败"
    for endpoint in _IP_ENDPOINTS:
        try:
            resp = requests.get(endpoint, proxies=proxies, timeout=timeout)
            resp.raise_for_status()
            ip = _parse_ip(resp.text)
            if ip:
                return {"ok": True, "ip": ip, "error": ""}
            last_error = "代理有响应，但没有返回公网 IP"
        except Exception as exc:
            last_error = _friendly_proxy_error(exc)
    return {"ok": False, "ip": "", "error": last_error}


def get_storage_config() -> dict:
    """读取存储配置。总是返回 dict（损坏值兜底为默认 local）。"""
    cfg = read_settings().get("storage")
    if not isinstance(cfg, dict):
        return {"type": "local", "s3": {}}
    return cfg
