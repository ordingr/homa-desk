"""Result actions shared by the popup (in-process) and POST /v1/actions/<verb> (M3b-4)."""
from . import names, shell
from .shell import VERBS, action_log, dispatch

__all__ = ["names", "shell", "VERBS", "action_log", "dispatch"]
