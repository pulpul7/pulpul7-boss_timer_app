"""Windows user-scoped DPAPI for the private DDNS token; no plaintext fallback."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os


class SecretStorageError(Exception):
    pass


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data, *, encrypt):
    if os.name != "nt":
        raise SecretStorageError("Token 암호화 저장은 Windows에서 지원합니다.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    entropy_data = b"BossTimer.web.ddns.v1"
    entropy_buffer = ctypes.create_string_buffer(entropy_data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(entropy_data), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; never use CRYPTPROTECT_LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise SecretStorageError("Token을 저장하거나 읽지 못했습니다. 현재 Windows 계정에서 Token을 다시 입력해주세요.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def protect_secret(value):
    return base64.b64encode(_transform(value.encode("ascii"), encrypt=True)).decode("ascii")


def unprotect_secret(value):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 16384:
            raise ValueError("invalid secret")
        data = base64.b64decode(value, validate=True)
        return _transform(data, encrypt=False).decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise SecretStorageError("저장된 Token을 읽지 못했습니다. Token을 다시 입력해주세요.") from exc
