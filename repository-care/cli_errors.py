import sys


def report_cli_argument_error(error):
    print(str(error), file=sys.stderr)
    return 2


def report_cli_input_error(error):
    print(str(error))
    return 1
