"""Standalone entrypoint: `python -m training [--port 5050] [--db path]`."""
import argparse
from pathlib import Path
from .app import create_app


def main():
    p = argparse.ArgumentParser(prog="training")
    p.add_argument("--port", type=int, default=5050)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--db", default=None, help="Path to training.db (default: ./training.db)")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    db_path = Path(args.db).resolve() if args.db else Path.cwd() / "training.db"
    app = create_app(db_path=db_path)
    print(f"  Training module starting at http://{args.host}:{args.port}")
    print(f"  DB: {db_path}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
