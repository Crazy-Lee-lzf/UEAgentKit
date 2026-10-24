"""Local file-permission hardening for the M1 layout broker descriptor.

The broker writes one file that contains a high-entropy session token, so the
file must be readable by the current user only.  On Windows this module changes
the file DACL through ``advapi32`` with ``ctypes`` (no ``icacls``/subprocess) and
then re-reads the security descriptor to prove the result.  On POSIX it uses
``chmod 0600`` and verifies the mode.

"PROVEN" means the verification itself succeeded and showed a protected DACL
whose only access-allowed ACE is the current user.  Anything else is
"UNVERIFIED"; callers must fail closed unless the operator explicitly opted into
a disposable host.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Literal

State = Literal["PROVEN", "UNVERIFIED"]

PROVEN: State = "PROVEN"
UNVERIFIED: State = "UNVERIFIED"

_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class FileSecurityStatus:
    state: State
    mechanism: str
    detail: str

    @property
    def proven(self) -> bool:
        return self.state == PROVEN

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state, "mechanism": self.mechanism, "detail": self.detail}


if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SE_DACL_PRESENT = 0x0004
    _SE_DACL_PROTECTED = 0x1000
    _ACCESS_ALLOWED_ACE_TYPE = 0
    _FILE_ALL_ACCESS = 0x001F01FF
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_USER = 1
    _SET_ACCESS = 2
    _NO_INHERITANCE = 0
    _INHERITANCE_FLAGS = 0x0F
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _TOKEN_USER_CLASS = 1
    _TOKEN_QUERY = 0x0008
    _ACL_INFO_SIZE = 2

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_wchar_p),
        ]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _TRUSTEE_W),
        ]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.SetEntriesInAclW.argtypes = [
        wintypes.ULONG,
        ctypes.POINTER(_EXPLICIT_ACCESS_W),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _current_user_sid() -> tuple[str, bytes]:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        try:
            size = wintypes.DWORD(0)
            _advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
            if size.value == 0:
                raise OSError(ctypes.get_last_error(), "GetTokenInformation(size)")
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER_CLASS, buffer, size.value, ctypes.byref(size)
            ):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation")
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
            sid = token_user.User.Sid
            length = int(_advapi32.GetLengthSid(sid))
            if length <= 0:
                raise OSError("GetLengthSid returned no length")
            raw = ctypes.string_at(sid, length)
            text = wintypes.LPWSTR()
            if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW")
            try:
                value = str(text.value)
            finally:
                _kernel32.LocalFree(text)
            return value, raw
        finally:
            _kernel32.CloseHandle(token)

    def _harden_windows(path: str) -> None:
        user_sid, sid_raw = _current_user_sid()
        del user_sid  # only the SID bytes are needed to build the trustee
        sid_buffer = ctypes.create_string_buffer(sid_raw, len(sid_raw))
        access = _EXPLICIT_ACCESS_W()
        access.grfAccessPermissions = _FILE_ALL_ACCESS
        access.grfAccessMode = _SET_ACCESS
        access.grfInheritance = _NO_INHERITANCE
        access.Trustee.TrusteeForm = _TRUSTEE_IS_SID
        access.Trustee.TrusteeType = _TRUSTEE_IS_USER
        access.Trustee.ptstrName = ctypes.cast(sid_buffer, ctypes.c_wchar_p)
        acl = ctypes.c_void_p()
        result = _advapi32.SetEntriesInAclW(1, ctypes.byref(access), None, ctypes.byref(acl))
        if result != 0:
            raise OSError(result, "SetEntriesInAclW")
        try:
            result = _advapi32.SetNamedSecurityInfoW(
                path,
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
            if result != 0:
                raise OSError(result, "SetNamedSecurityInfoW")
        finally:
            _kernel32.LocalFree(acl)

    def _verify_windows(path: str) -> FileSecurityStatus:
        attributes = int(_kernel32.GetFileAttributesW(path))
        if attributes == 0xFFFFFFFF:
            return FileSecurityStatus(UNVERIFIED, "windows-dacl", "file-attributes-unavailable")
        if attributes & _FILE_ATTRIBUTE_DIRECTORY:
            return FileSecurityStatus(UNVERIFIED, "windows-dacl", "not-a-regular-file")
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return FileSecurityStatus(UNVERIFIED, "windows-dacl", "reparse-point")

        user_sid, _ = _current_user_sid()
        owner = ctypes.c_void_p()
        group = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        sacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        result = _advapi32.GetNamedSecurityInfoW(
            path,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(descriptor),
        )
        if result != 0:
            return FileSecurityStatus(UNVERIFIED, "windows-dacl", f"security-info-error-{result}")
        try:
            control = wintypes.WORD(0)
            revision = wintypes.DWORD(0)
            if not _advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "control-unavailable")
            flags = int(control.value)
            if not flags & _SE_DACL_PRESENT:
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "dacl-absent")
            if not flags & _SE_DACL_PROTECTED:
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "dacl-inherited")
            if not dacl:
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "dacl-null")
            info = _ACL_SIZE_INFORMATION()
            if not _advapi32.GetAclInformation(
                dacl, ctypes.byref(info), ctypes.sizeof(info), _ACL_INFO_SIZE
            ):
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "acl-info-unavailable")
            if info.AceCount == 0:
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "dacl-empty")
            seen: list[str] = []
            for index in range(int(info.AceCount)):
                ace = ctypes.c_void_p()
                if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)) or not ace:
                    return FileSecurityStatus(UNVERIFIED, "windows-dacl", "ace-unreadable")
                header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
                if header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                    return FileSecurityStatus(UNVERIFIED, "windows-dacl", "ace-not-allow")
                if header.AceFlags & _INHERITANCE_FLAGS:
                    return FileSecurityStatus(UNVERIFIED, "windows-dacl", "ace-inherited")
                sid_pointer = ctypes.c_void_p(ace.value + 8)
                text = wintypes.LPWSTR()
                if not _advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
                    return FileSecurityStatus(UNVERIFIED, "windows-dacl", "ace-sid-unreadable")
                try:
                    seen.append(str(text.value))
                finally:
                    _kernel32.LocalFree(text)
            if len(seen) != 1 or seen[0] != user_sid:
                return FileSecurityStatus(UNVERIFIED, "windows-dacl", "dacl-not-user-only")
            return FileSecurityStatus(PROVEN, "windows-dacl", "protected-dacl-current-user-only")
        finally:
            _kernel32.LocalFree(descriptor)

    def _restrict_and_verify(path: str) -> FileSecurityStatus:
        try:
            _harden_windows(path)
        except Exception as exc:  # noqa: BLE001 - report the failure, never leak a path
            return FileSecurityStatus(UNVERIFIED, "windows-dacl", f"harden-failed:{type(exc).__name__}")
        return _verify_windows(path)

    def _verify(path: str) -> FileSecurityStatus:
        return _verify_windows(path)

else:  # pragma: no cover - POSIX fallback, kept intentionally small
    def _restrict_and_verify(path: str) -> FileSecurityStatus:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            return FileSecurityStatus(UNVERIFIED, "posix-mode", f"chmod-failed:{exc.errno}")
        return _verify(path)

    def _verify(path: str) -> FileSecurityStatus:
        try:
            info = os.lstat(path)
        except OSError as exc:
            return FileSecurityStatus(UNVERIFIED, "posix-mode", f"stat-failed:{exc.errno}")
        if not stat.S_ISREG(info.st_mode):
            return FileSecurityStatus(UNVERIFIED, "posix-mode", "not-a-regular-file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            return FileSecurityStatus(UNVERIFIED, "posix-mode", "group-or-world-accessible")
        if info.st_uid != os.geteuid():
            return FileSecurityStatus(UNVERIFIED, "posix-mode", "not-owned-by-current-user")
        return FileSecurityStatus(PROVEN, "posix-mode", "mode-0600-current-user")


def restrict_file_permissions(path: str | os.PathLike[str]) -> FileSecurityStatus:
    """Harden ``path`` and prove the resulting permissions.

    Never raises for a failed hardening step: the returned state is UNVERIFIED
    and the caller decides whether that is acceptable.
    """

    return _restrict_and_verify(os.fspath(path))


def verify_file_permissions(path: str | os.PathLike[str]) -> FileSecurityStatus:
    """Verify an existing file without modifying it."""

    return _verify(os.fspath(path))


__all__ = [
    "FileSecurityStatus",
    "PROVEN",
    "UNVERIFIED",
    "restrict_file_permissions",
    "verify_file_permissions",
]
