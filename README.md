# Energy Monitor Core

Rewrite of the legacy `energymonitor` app with a smaller JSON-driven core, per-module activation, file-backed backups, mandatory authentication, module-scoped dependency installation, and a responsive web UI.

## Container Only

This rewrite is intended to run in Docker only. Start it through the workspace `docker-compose.yml` so the service comes up on port `8030` with the expected mounts and device access.

## Logs

The Logs page groups each timestamped record and its continuation lines into
one entry. When a record ends with JSON data, use its arrow button to view that
data in a dialog. The recent logs API also retains its `lines` field for clients
that need the original text; the `entries` field contains the grouped records.

To run the log regression tests in the running container:

```sh
docker exec -w /app energy-monitor-core python -m unittest discover -s tests -p test_logs.py -v
```
