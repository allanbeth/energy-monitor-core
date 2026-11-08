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

## Web templates

Shared Jinja components live in `templates/_card_macros.html` (card headers),
`templates/_global_macros.html` (card shells and action buttons),
`templates/_dashboard_macros.html` (dashboard statistics and trends), and
`templates/_forms_macros.html` (settings rows, inputs and switches). Preserve
IDs, names and `data-*` attributes when changing these components: the page
scripts use them for live updates and form actions. Switches include a hidden
false value so unchecked settings are submitted correctly.

Card-content typography uses the dashboard's label, primary-value and detail
styles through the `--card-label-*`, `--card-value-*` and `--card-detail-*`
CSS variables. Connection and severity colours remain independent of these
typography tokens.

Dashboard reading cards and module sensor cards share a fixed height, minimum
width, surface, heading style and reading-row layout. On module pages, one
global Live Sensor Cards panel contains sensor cards in snapshot order and
grows to fit the visible cards. Each sensor's reading, editor, history, and
feedback views stay within its fixed-height card and scroll when necessary.
Module pages show unconnected sensors by default; the header switch can hide
them when desired.
Core and module settings use the same expanding global-panel layout and
reading-card heading styles; individual settings cards scroll internally.
Individual cards have a coloured top border: sensor cards share the dashboard
reading-card colours for their sensor type, while settings and log cards use
their header icon colour. Global panels do not have these accent borders.

Run the template and log regression tests with:

```sh
docker exec -w /app energy-monitor-core python -m unittest discover -s tests -p 'test_*.py' -v
```
