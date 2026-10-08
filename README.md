# MASHER

A matcher tool to convert my bank statments to toshl data that learns based on past categorisations.
To ease my manual bookeeping using toshl.

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
