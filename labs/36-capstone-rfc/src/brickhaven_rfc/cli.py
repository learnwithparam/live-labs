"""`brickhaven-rfc review|send-back|serve`: check an RFC against the signed benchmark and budget."""

import argparse
import sys
from pathlib import Path

from brickhaven_rfc.review import review, send_back, summary
from brickhaven_rfc.rfc import read_rfc


def main() -> None:
    parser = argparse.ArgumentParser(prog="brickhaven-rfc")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("review", help="check an RFC and print the sign-off")
    check.add_argument("path", type=Path)
    check.add_argument("--without-tag", default="", help="what if the benchmark had no cases with this tag")
    back = commands.add_parser("send-back", help="a reviewer sends one failure mode back")
    back.add_argument("path", type=Path)
    back.add_argument("mode")
    back.add_argument("--reviewer", required=True)
    back.add_argument("--reason", required=True)
    commands.add_parser("serve", help="open the review page")
    args = parser.parse_args()
    if args.command == "serve":
        from brickhaven_rfc.serve import run
        run()
    elif args.command == "send-back":
        send_back(read_rfc(args.path), args.mode, args.reviewer, args.reason)
        print(f"sent back: {args.mode} by {args.reviewer}")
    else:
        verdict = review(read_rfc(args.path), without_tag=args.without_tag)
        print(args.path)
        print("\n".join(summary(verdict)))
        sys.exit(0 if verdict.approved else 1)
