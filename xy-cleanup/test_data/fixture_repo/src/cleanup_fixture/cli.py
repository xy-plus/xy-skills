"""Command-line entry point for the sample package."""
import argparse
import sys

from cleanup_fixture.core import get_timeout, normalize_label, parse_external_result


def create_parser():
    """Build the command-line parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument("label", nargs="?")
    parser.add_argument("--legacy-export", action="store_true")
    return parser


def main(arguments):
    """Print a normalized label or run the scheduled export."""
    options = create_parser().parse_args(arguments)
    if options.legacy_export:
        print("legacy export complete")
        return 0
    if options.label is None:
        return 2
    external_result = options.label
    parsed_result = parse_external_result(external_result)
    if parsed_result is None:
        return 2
    print(f"{normalize_label(parsed_result)}:{get_timeout('normal')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
