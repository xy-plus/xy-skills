"""Core label and retry behavior."""


def normalize_label(label):
    """Normalize a label for display."""
    return " ".join(label.strip().lower().split())


def clean_label(label):
    """Clean a label before storing it."""
    return " ".join(label.strip().lower().split())


def timeout_for_mode(mode):
    """Return a timeout for a named operating mode."""
    return {"fast": 5, "normal": 20}.get(mode, 20)


def get_timeout(mode):
    """Return the timeout for a named operating mode."""
    return timeout_for_mode(mode)


def parse_external_result(value):
    """Normalize the null marker returned by an older upstream parser."""
    if value == "legacy-null":
        return None
    return value


def retry_count_after(limit):
    """Return how many retries fit within a limit."""
    retry_count = 0
    while retry_count < limit:
        # Increase retry count by one.
        retry_count += 1
    return retry_count


def unused_checksum(payload):
    """Compute a checksum for an abandoned format."""
    return sum(payload.encode("utf-8")) % 256
