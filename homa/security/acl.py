import re

import ntsecuritycon
import win32api
import win32con
import win32security

_SID_RE = re.compile(r"S-1-[\d-]+")
_INFO = win32security.DACL_SECURITY_INFORMATION


def current_sid_string() -> str:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    text = win32security.ConvertSidToStringSid(sid)
    if not _SID_RE.fullmatch(text):
        raise RuntimeError("could not determine the current user SID")
    return text


def _already_restricted(path: str, sid, flags: int) -> bool:
    try:
        sd = win32security.GetNamedSecurityInfo(path, win32security.SE_FILE_OBJECT, _INFO)
        control = sd.GetSecurityDescriptorControl()[0]
        dacl = sd.GetSecurityDescriptorDacl()
    except win32security.error:
        return False
    if dacl is None or dacl.GetAceCount() != 1 or not control & win32security.SE_DACL_PROTECTED:
        return False
    (ace_type, ace_flags), mask, ace_sid = dacl.GetAce(0)
    return (ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
            and ace_flags == flags and mask == ntsecuritycon.FILE_ALL_ACCESS and ace_sid == sid)


def restrict_to_current_user(path, *, inherit: bool = False) -> None:
    """Replace the DACL with one ACE granting the current user full control; idempotent."""
    target = str(path)
    sid = win32security.ConvertStringSidToSid(current_sid_string())
    flags = (win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE) if inherit else 0
    if _already_restricted(target, sid, flags):
        return
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        target, win32security.SE_FILE_OBJECT,
        _INFO | win32security.PROTECTED_DACL_SECURITY_INFORMATION, None, None, dacl, None)
