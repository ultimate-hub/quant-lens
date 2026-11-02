"""
密钥安全存储：用 Windows DPAPI 加密 API Key。

为什么不用明文存数据库？
    数据库文件被拷走、或被人打开看一眼，密钥就泄露了。
    中转站的 key 往往有余额，泄露等于送钱。

DPAPI 是什么？
    Windows 内置的加密接口（CryptProtectData）。
    加密后的数据只能用「同一台机器 + 同一个用户」解开。
    即使有人拷走数据库文件，换台电脑也解不开。

为什么不用 keyring 库？
    少一个依赖就少一个安装失败的可能。DPAPI 用 ctypes 直接调系统 API，
    Python 标准库自带，零安装。
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

# ------------------------------------------------------------------
# DPAPI 结构体定义
# ------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


_crypt32 = ctypes.windll.crypt32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

if _crypt32:
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]

CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretError(Exception):
    """密钥加解密失败。"""


def encrypt(plain: str, entropy: str = "quant-terminal") -> str:
    """
    加密字符串，返回 base64 文本（可直接存数据库）。
    非 Windows 平台退化为 base64（仅供开发调试，会警告）。
    """
    if not plain:
        return ""
    data = plain.encode("utf-8")

    if not _crypt32:
        # 非 Windows：只做编码，明确不是加密
        return "b64:" + base64.b64encode(data).decode()

    in_blob = _blob(data)
    ent_blob = _blob(entropy.encode("utf-8"))
    out_blob = DATA_BLOB()

    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob), "quant-terminal-key",
        ctypes.byref(ent_blob), None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretError(f"DPAPI 加密失败（错误码 {ctypes.get_last_error()}）")

    try:
        return base64.b64encode(_blob_to_bytes(out_blob)).decode()
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def decrypt(cipher: str, entropy: str = "quant-terminal") -> str:
    """解密。失败抛 SecretError（例如换了电脑/用户）。"""
    if not cipher:
        return ""

    if cipher.startswith("b64:"):
        return base64.b64decode(cipher[4:]).decode("utf-8")

    if not _crypt32:
        raise SecretError("当前平台不支持 DPAPI 解密")

    try:
        raw = base64.b64decode(cipher)
    except Exception as exc:  # noqa: BLE001
        raise SecretError(f"密文格式错误: {exc}") from exc

    in_blob = _blob(raw)
    ent_blob = _blob(entropy.encode("utf-8"))
    out_blob = DATA_BLOB()

    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise SecretError(
            "解密失败：密钥可能是在另一台电脑/另一个Windows账户下加密的")

    try:
        return _blob_to_bytes(out_blob).decode("utf-8")
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def mask(plain: str, keep: int = 4) -> str:
    """把密钥显示成 sk-****abcd 的形式，用于界面展示。"""
    if not plain:
        return ""
    if len(plain) <= keep * 2:
        return "*" * len(plain)
    return f"{plain[:keep]}{'*' * 8}{plain[-keep:]}"
