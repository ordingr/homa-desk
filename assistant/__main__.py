import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant")
    parser.add_argument("--doctor", action="store_true", help="print an environment report and exit")
    args = parser.parse_args(argv)
    if args.doctor:
        from . import doctor
        return doctor.run()
    from . import app
    return app.main()


if __name__ == "__main__":
    sys.exit(main())
