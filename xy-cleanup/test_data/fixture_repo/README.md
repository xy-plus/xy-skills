# Cleanup fixture

This sample package processes labels and exposes a small command line.

The nightly export is still called from an external cron job:

```sh
PYTHONPATH=src python3 -m cleanup_fixture.cli --legacy-export nightly
```

The cron job runs in the deployment host, outside this repository.
