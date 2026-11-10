"""Credential storage for the provider API key.

On Windows the key goes to Credential Manager (DPAPI-protected per user)
instead of a plaintext `.env`. Other platforms return False so callers can fall
back to the existing per-user config file.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from ctypes import wintypes

TARGET = "scout/gemini-api-key"
_USERNAME = "scout"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _dll() -> object | None:
    if os.name != "nt":
        return None
    try:
        dll = ctypes.WinDLL("advapi32", use_last_error=True)
        dll.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_Credential)),
        ]
        dll.CredReadW.restype = wintypes.BOOL
        dll.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        dll.CredWriteW.restype = wintypes.BOOL
        dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        dll.CredDeleteW.restype = wintypes.BOOL
        dll.CredFree.argtypes = [ctypes.c_void_p]
        dll.CredFree.restype = None
        return dll
    except (AttributeError, OSError):  # pragma: no cover - unusual Windows installs
        return None


def load_credential() -> str | None:
    """Read the API key from Credential Manager, or return None."""
    dll = _dll()
    if dll is None:
        return None
    pointer = ctypes.POINTER(_Credential)()
    try:
        ok = dll.CredReadW(TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer))
        if not ok or not pointer:
            return None
        credential = pointer.contents
        size = int(credential.CredentialBlobSize)
        if size <= 0 or not credential.CredentialBlob:
            return None
        raw = ctypes.string_at(credential.CredentialBlob, size)
        value = raw.decode("utf-16-le", errors="ignore").strip()
        return value or None
    except (AttributeError, OSError, ValueError):  # pragma: no cover - host dependent
        return None
    finally:
        if pointer:
            with contextlib.suppress(AttributeError, OSError):  # pragma: no cover
                dll.CredFree(pointer)


def save_credential(api_key: str) -> bool:
    """Write the key to Credential Manager. Returns False when unavailable."""
    dll = _dll()
    key = api_key.strip()
    if dll is None or not key:
        return False
    blob = key.encode("utf-16-le")
    buffer = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
    credential = _Credential(
        Flags=0,
        Type=_CRED_TYPE_GENERIC,
        TargetName=TARGET,
        Comment="Scout Gemini API key",
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE,
        UserName=_USERNAME,
    )
    try:
        return bool(dll.CredWriteW(ctypes.byref(credential), 0))
    except (AttributeError, OSError):  # pragma: no cover - host dependent
        return False


def delete_credential() -> bool:
    """Delete the stored key. Returns False if none existed or unavailable."""
    dll = _dll()
    if dll is None:
        return False
    try:
        return bool(dll.CredDeleteW(TARGET, _CRED_TYPE_GENERIC, 0))
    except (AttributeError, OSError):  # pragma: no cover - host dependent
        return False


def available() -> bool:
    """True on Windows where Credential Manager APIs can be loaded."""
    return sys.platform == "win32" and _dll() is not None
