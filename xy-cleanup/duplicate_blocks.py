"""Find repeated six-line windows in tracked source, test, and documentation files."""
from collections import defaultdict
from pathlib import Path

from repo_map_lib.map_data import file_category


# Six lines avoid flagging common two- or three-line idioms with little to merge;
# forty alphanumeric characters filter low-information runs such as imports and pass.
WINDOW_LINES = 6
MIN_ALPHANUMERIC_CHARACTERS = 40
SCANNED_CATEGORIES = {"code", "tests", "docs"}
TODO_FILENAME = "TODO.md"


def find_duplicate_windows(repository, tracked_files):
    occurrences = defaultdict(list)
    for path in sorted(tracked_files):
        if Path(path).name == TODO_FILENAME:
            continue
        if file_category(repository, path) not in SCANNED_CATEGORIES:
            continue
        try:
            physical_lines = (Path(repository) / path).read_bytes().splitlines()
        except OSError:
            continue
        meaningful_lines = []
        for line_number, raw_line in enumerate(physical_lines, start=1):
            line = " ".join(raw_line.decode("utf-8", errors="replace").split())
            if line and any(character.isalnum() for character in line):
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


def other_ranges_for_keep(keep_range, duplicate_windows):
    path, start_line, end_line = keep_range
    alternatives = set()
    for locations in duplicate_windows.values():
        overlaps_keep = any(
            other_path == path
            and other_start <= end_line
            and start_line <= other_end
            for other_path, other_start, other_end in locations
        )
        if not overlaps_keep:
            continue
        alternatives.update(
            location
            for location in locations
            if not (
                location[0] == path
                and location[1] <= end_line
                and start_line <= location[2]
            )
        )
    return _merge_ranges(alternatives)


def _merge_ranges(locations):
    merged = []
    for path in sorted({location[0] for location in locations}):
        ranges = sorted(
            (start, end)
            for location_path, start, end in locations
            if location_path == path
        )
        for start, end in ranges:
            if merged and merged[-1][0] == path and start <= merged[-1][2] + 1:
                previous_path, previous_start, previous_end = merged[-1]
                merged[-1] = (previous_path, previous_start, max(previous_end, end))
            else:
                merged.append((path, start, end))
    return merged
