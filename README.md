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

### Local WEB UI
Statement files are parsed locally and are not sent to a hosted service.

1. From the project root, install the optional UI dependency and start Streamlit:

```sh
uv sync --extra ui
uv run streamlit run test-ui/app.py --server.address 127.0.0.1
```

2. Open the local URL printed by Streamlit. Upload either statement or both. The app parses the files with `parse.py`, normalizes and matches them with the existing pipeline, then shows a paginated sheet with one transaction per row, sorted newest-first by date by default. Click a column heading to sort by it, and click again to reverse the order. The sheet shows 50 rows per page with previous/next arrow controls below the list; page changes return the view to the top of the transactions. Amounts and the optional Toshl `Description` field can be edited inline and are used in the downloads. Category and tag selections are editable, and the tag list follows the selected category. Repeated normalized descriptions share a learned mapping, so an edit applies to all rows with that description. Marking a row to omit excludes that description from the current output and future matching.

3. Saving writes manual mappings and omissions to the local JSON files selected in the sidebar. The app calls `export_to_toshl.py` to prepare the Toshl import CSV. The reviewed and Toshl CSVs are offered as browser downloads and are not saved into the repository automatically.

4. The defaults are `data/toshl_categories_tags.json`, `data/mappings.json`, and `data/omitted_descriptions.json`. Change their paths in the sidebar to use another local data set.

##### Install dependencies with pip

```sh
pip install -e '.[ui]'
```


### Manual 

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
