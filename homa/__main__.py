import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m homa")
    parser.add_argument("--doctor", action="store_true", help="print an environment report and exit")
    parser.add_argument("--init", action="store_true",
                        help="create the home folder and the access token if missing, then exit")
    sub = parser.add_subparsers(dest="command")

    send = sub.add_parser("send", help="post a card to the running app")
    send.add_argument("--title", required=True)
    send.add_argument("--id", help="update the card with this id in place instead of creating a new one")
    send.add_argument("--detail")
    send.add_argument("--kind", default="custom", choices=("custom", "waiting", "done", "meeting", "reminder"))
    send.add_argument("--priority", type=int)
    send.add_argument("--sticky", action="store_true")
    send.add_argument("--url")

    sub.add_parser("shutdown", help="ask the running app to quit")

    sub.add_parser("demo", help="show sample cards without installing anything")

    update = sub.add_parser("update", help="update this install (git pull, or a release zip from update.source)")
    update.add_argument("--dry-run", action="store_true", help="print what would be done without doing it")
    update.add_argument("--yes", action="store_true",
                        help="release-zip route: also run pip on the new copy's requirements (otherwise it is printed)")

    hooks = sub.add_parser("hooks", help="manage Claude Code hook entries")
    hooks.add_argument("action", choices=("install", "remove"))
    hooks.add_argument("--dry-run", action="store_true", help="print the entries without writing")
    return parser


def _migrate_first() -> int:
    from . import migrate
    code = migrate.run_once()
    return 0 if code is None else code


def _init() -> int:
    from . import paths
    from .security import token
    root = paths.ensure_layout()
    token.ensure()
    print(f"home: {root}")
    print("token: present")
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "demo":
        parser.error("unrecognized arguments: " + " ".join(extra))
    if args.init:
        return _migrate_first() or _init()
    if args.doctor:
        from . import doctor
        return doctor.run()
    if args.command == "send":
        from . import send
        return send.run_send(args)
    if args.command == "shutdown":
        from . import send
        return send.run_shutdown()
    if args.command == "demo":
        from . import demo
        return demo.main(extra)
    if args.command == "update":
        from . import update
        return update.run(args)
    if args.command == "hooks":
        from . import hooks_installer
        return hooks_installer.run(args)
    code = _migrate_first()
    if code:
        return code
    from . import app
    return app.main()


if __name__ == "__main__":
    sys.exit(main())
