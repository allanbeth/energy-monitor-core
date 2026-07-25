# Energy Monitor Core

Rewrite of the legacy `energymonitor` app with a smaller JSON-driven core, per-module activation, file-backed backups, mandatory authentication, module-scoped dependency installation, and a responsive web UI.

## Container Only

This rewrite is intended to run in Docker only. Start it through the workspace `docker-compose.yml` so the service comes up on port `8030` with the expected mounts and device access.
