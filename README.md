# MASHER

A matcher tool to convert my bank statments to toshl data that learns based on past categorisations.
To ease my manual bookeeping using toshl.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if it is not already installed.
2. Install the project dependencies and create the virtual environment:

   ```sh
   uv sync
   ```

The project requires Python 3.12 or newer. `uv` will use an installed compatible Python version or can install one with `uv python install 3.12`.

## PreRequistes

### Fetach & Refresh Toshl categories and tags

Currently toshl script to fetch categories and tags use a personal token.

The account API token belongs in a local `.env` file in the project root. Start with the ignored template:

```sh
cp .env.example .env
```

Edit `.env` and replace the placeholder with your token. `.env` is ignored by Git; do not commit it.

Preview the API data without changing the taxonomy file:

```sh
uv run --env-file .env python sync_toshl_taxonomy.py --dry-run
```

Refresh `data/toshl_categories_tags.json`:

```sh
uv run --env-file .env python sync_toshl_taxonomy.py
```

The updater keeps only category/tag IDs and names, rebuilds each tag's category link, excludes Toshl's `unsorted` pseudo-category, and keeps tags assigned there as unassigned. It replaces the taxonomy file atomically after both API requests succeed. It does not change `data/mappings.json` or `data/omitted_descriptions.json`.

You can choose another output file with `--output PATH`.

## Usage

Run these scripts directly to process statements and create a Toshl import CSV:

1. Parse the statement files, normalise descriptions, and match transactions. Provide either statement path or both; `matcher.py` runs parsing and normalisation as part of this step.

   ```sh
   uv run python matcher.py path/to/hdfc_statement.txt path/to/icici_statement.xls --export-to-csv
   ```

   The command prints the matched transactions and writes a timestamped `*_matched_statement.csv`. You can optionally filter dates with `--start-date DD/MM/YYYY --end-date DD/MM/YYYY`.

2. Review the matched CSV. By default, this saves your review and updates `data/mappings.json`:

   ```sh
   uv run python reviewer.py YYYYMMDD_HHMMSS_matched_statement.csv
   ```

3. Export the reviewed CSV in Toshl's import format:

   ```sh
   uv run python export_to_toshl.py YYYYMMDD_HHMMSS_matched_statement_reviewed.csv
   ```

The Toshl CSV is written beside the reviewed file. Run any script with `--help` to see its options.
