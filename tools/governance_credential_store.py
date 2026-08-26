# -*- coding: utf-8 -*-
"""Governance Credential Store —— 治理管理 Token 的本机安全保存（Windows DPAPI）。

Phase 2A（2026-08-27）：
    面板管理 Token 通过 Windows DPAPI（CryptProtectData/CryptUnprotectData，
    ctypes 标准库调用，零新增依赖）加密后写入
    %LOCALAPPDATA%/QianWuYuyi/governance_cred.bin。

安全原则：
    - 磁盘上只有 base64(DPAPI(ciphertext))，绝无明文 token；
    - DPAPI 不可用/失败时绝不降级明文保存，仅返回 False/None；
    - 本模块只负责"本地保存与读取"，不参与任何服务器认证逻辑；
    - 错误信息不包含 token/密文/headers。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# 凭据文件路径：%LOCALAPPDATA%/QianWuYuyi/governance_cred.bin
def _default_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or ""
    if not local:
        raise RuntimeError("Credential storage unavailable: LOCALAPPDATA not set")
    return Path(local) / "QianWuYuyi" / "governance_cred.bin"


# ============================================================
# Windows DPAPI（ctypes，零新依赖）
# ============================================================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_from_bytes(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _bytes_from_blob(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi_crypt_protect(data: bytes) -> bytes:
    """DPAPI 加密（当前 Windows 用户级）。失败抛异常。"""
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    crypt32.CryptProtectData.restype = wt.BOOL

    in_blob = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        err = ctypes.get_last_error()
        raise RuntimeError(f"DPAPI encrypt failed (err={err})")
    try:
        return _bytes_from_blob(out_blob)
    finally:
        if out_blob.pbData:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_crypt_unprotect(data: bytes) -> bytes:
    """DPAPI 解密。失败抛异常。"""
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.restype = wt.BOOL

    in_blob = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        err = ctypes.get_last_error()
        raise RuntimeError(f"DPAPI decrypt failed (err={err})")
    try:
        return _bytes_from_blob(out_blob)
    finally:
        if out_blob.pbData:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def is_available() -> bool:
    """DPAPI（Windows + Crypt32）是否可用。非 Windows 返回 False。"""
    if os.name != "nt":
        return False
    try:
        ctypes.WinDLL("Crypt32")
        return True
    except Exception:  # noqa: BLE001
        return False


class CredentialStore:
    """本地凭据存取（DPAPI 加密；路径可注入供测试）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _default_path()

    def save(self, token: str) -> bool:
        """保存 token（DPAPI 加密写盘）。成功 → True；不可用/失败 → False。"""
        if not is_available():
            return False
        if not token or not token.strip():
            return False
        try:
            payload = json.dumps({
                "version": 1,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "token": token,
            }, ensure_ascii=False).encode("utf-8")
            cipher = _dpapi_crypt_protect(payload)
            # base64 仅为可写字节流；安全由 DPAPI 保证（磁盘无明文）
            encoded = base64.b64encode(cipher).decode("ascii")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(encoded, encoding="ascii")
            return True
        except Exception:  # noqa: BLE001
            return False

    def load(self) -> Optional[str]:
        """读取 token。不存在 → None；解密/损坏 → 明确失败（None，不返回伪造值）。"""
        if not is_available():
            return None
        try:
            if not self.path.exists():
                return None
            encoded = self.path.read_text(encoding="ascii").strip()
            if not encoded:
                return None
            cipher = base64.b64decode(encoded)
            payload = _dpapi_crypt_unprotect(cipher)
            data = json.loads(payload.decode("utf-8"))
            tok = str(data.get("token") or "")
            return tok if tok else None
        except Exception:  # noqa: BLE001
            # 损坏/解密失败：不返回随机值、不覆盖文件
            return None

    def clear(self) -> bool:
        try:
            if self.path.exists():
                self.path.unlink()
            return True
        except Exception:  # noqa: BLE001
            return False

    def exists(self) -> bool:
        return self.path.exists()


# 默认单例路径供面板使用
def get_credential_store() -> CredentialStore:
    return CredentialStore()
