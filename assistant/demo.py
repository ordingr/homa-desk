"""Sample cards on a fake store; nothing is written outside a temp folder."""
import os
import shutil
import sys
import tempfile


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    scratch = tempfile.mkdtemp(prefix="assistant-demo-")
    os.environ["ASSISTANT_HOME"] = scratch
    try:
        from .overlay import cards_ui
        return cards_ui._demo(argv)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
