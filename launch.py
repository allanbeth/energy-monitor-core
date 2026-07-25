from pathlib import Path
import os
import sys

from energy_monitor_core.app import create_app
from energy_monitor_core.logging import configure_logging


APP_ROOT = Path(__file__).resolve().parent


def main() -> None:
    if os.environ.get("ENERGY_MONITOR_DOCKER", "") != "1":
        sys.stderr.write("Energy Monitor Core is Docker-only. Start it with docker compose.\n")
        raise SystemExit(1)

    configure_logging(APP_ROOT)
    app = create_app(APP_ROOT)
    host = app.config.get("APP_HOST", "0.0.0.0")
    port = int(app.config.get("APP_PORT", 8030))
    app.logger.info("Starting Energy Monitor Core on %s:%s", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
