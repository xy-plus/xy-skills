"""Find repeated six-line windows in tracked source, test, and documentation files."""
from collections import defaultdict
from pathlib import Path
import re

from repo_map_lib.map_data import file_category_with_skip
from repo_map_lib.skip_paths.skip_paths import is_skipped_path


# Six lines avoid common short idioms; forty alphanumerics filter low-information runs.
# Import-only lines are skipped separately before either window threshold is applied.
WINDOW_LINES = 6
MIN_ALPHANUMERIC_CHARACTERS = 40
SCANNED_CATEGORIES = {"code", "tests", "docs"}
TODO_FILENAME = "TODO.md"
PYTHON_IMPORT_LINE = re.compile(
    r"(?:import\s+[^#;]+|from\s+[^#;]+\s+import\s+[^#;]+)(?:\s+#.*)?"
)
RUST_USE_LINE = re.compile(r"(?:(?:pub(?:\([^)]*\))?\s+)?use\s+[^;]+;)(?:\s*//.*)?")


def _is_import_line(line):
    return bool(PYTHON_IMPORT_LINE.fullmatch(line) or RUST_USE_LINE.fullmatch(line))


def find_duplicate_windows(repository, tracked_files, skip_paths):
    occurrences = defaultdict(list)
    for path in sorted(tracked_files):
        if Path(path).name == TODO_FILENAME:
            continue
        if is_skipped_path(path, skip_paths):
            continue
        if file_category_with_skip(repository, path, skip_paths) not in SCANNED_CATEGORIES:
            continue
        try:
            physical_lines = (Path(repository) / path).read_bytes().splitlines()
        except OSError:
            continue
        meaningful_lines = []
        for line_number, raw_line in enumerate(physical_lines, start=1):
            line = " ".join(raw_line.decode("utf-8", errors="replace").split())
            if line and not _is_import_line(line) and any(
                character.isalnum() for character in line
            ):
                meaningful_lines.append((line_number, line))
        for index in range(len(meaningful_lines) - WINDOW_LINES + 1):
            window = meaningful_lines[index:index + WINDOW_LINES]
            text = tuple(line for _line_number, line in window)
            character_count = sum(
                character.isalnum() for line in text for character in line
            )
            if character_count < MIN_ALPHANUMERIC_CHARACTERS:
                continue
            occurrences[text].append((path, window[0][0], window[-1][0]))
    return {
        text: locations
        for text, locations in occurrences.items()
        if len(locations) > 1
    }


def merge_ranges(locations):
    merged = []
    for path, start, end in sorted(locations):
        if merged and merged[-1][0] == path and start <= merged[-1][2] + 1:
            previous_path, previous_start, previous_end = merged[-1]
            merged[-1] = (previous_path, previous_start, max(previous_end, end))
        else:
            merged.append((path, start, end))
    return merged
