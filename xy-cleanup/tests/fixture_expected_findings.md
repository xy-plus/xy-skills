# Fixture review notes

The expected findings are outside the repository copied for reviewers.

- `unused_checksum` is not called and can be removed with its abandoned-format code.
- `normalize_label` and `clean_label` implement the same normalization.
- `get_timeout` only forwards its argument to `timeout_for_mode`.
- `parse_external_result` carries a special branch that compensates for an upstream result bug.
- The retry-count comment repeats the increment on the next line.
- The two label tests assert the same behavior.
- The `--legacy-export` option is used by the deployment host's cron job documented in the fixture README; ask before removing it.
