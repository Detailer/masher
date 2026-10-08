"""Refresh the local minimal Toshl category/tag taxonomy from the Toshl API."""

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
_DEFAULT_OUTPUT = ROOT / 'data' / 'toshl_categories_tags.json'
_API_BASE = 'https://api.toshl.com'


def _fetch_collection(resource, token):
    """Fetch one Toshl API collection using the account API token."""
    credentials = base64.b64encode(f'{token}:'.encode('utf-8')).decode('ascii')
    request = Request(
        f'{_API_BASE}/{resource}',
        headers={
            'Authorization': f'Basic {credentials}',
            'Accept': 'application/json',
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'Toshl {resource} request failed with HTTP {error.code}.') from None
    except URLError as error:
        raise RuntimeError(f'Could not reach Toshl while fetching {resource}: {error.reason}') from None
    except json.JSONDecodeError as error:
        raise RuntimeError(f'Toshl returned invalid JSON for {resource}.') from error

    if not isinstance(payload, list):
        raise RuntimeError(f'Toshl returned an unexpected {resource} response; expected a JSON list.')
    return payload


def _minimal_records(records, label, *, exclude_unsorted=False):
    """Keep only non-deleted records with an ID and name."""
    output = []
    seen_ids = set()
    for record in records:
        if not isinstance(record, dict) or record.get('deleted'):
            continue
        record_id = record.get('id')
        name = record.get('name')
        if record_id is None or not str(name or '').strip():
            continue
        record_id = str(record_id)
        if exclude_unsorted and str(name).strip().casefold() == 'unsorted':
            continue
        if record_id in seen_ids:
            raise RuntimeError(f'Toshl returned duplicate {label} ID {record_id!r}.')
        seen_ids.add(record_id)
        output.append({'id': record_id, 'name': str(name).strip()})
    return output


def build_taxonomy(categories_response, tags_response):
    """Create categories, tags, and category/tag links from Toshl responses."""
    categories = _minimal_records(
        categories_response, 'category', exclude_unsorted=True
    )
    tags = _minimal_records(tags_response, 'tag')
    category_ids = {category['id'] for category in categories}

    category_tags = []
    seen_links = set()
    for tag in tags_response:
        if not isinstance(tag, dict) or tag.get('deleted'):
            continue
        tag_id = tag.get('id')
        category_id = tag.get('category')
        if tag_id is None or category_id is None:
            continue
        tag_id, category_id = str(tag_id), str(category_id)
        # Tags assigned to Toshl's "unsorted" pseudo-category remain in the
        # tag list but are represented as unassigned in this taxonomy.
        if category_id not in category_ids:
            continue
        link = (category_id, tag_id)
        if link not in seen_links:
            category_tags.append({'category_id': category_id, 'tag_id': tag_id})
            seen_links.add(link)

    return {
        'categories': categories,
        'tags': tags,
        'category_tags': category_tags,
    }


def sync_taxonomy(output_path=_DEFAULT_OUTPUT, *, dry_run=False):
    """Fetch both API collections and write the compact local taxonomy."""
    token = os.environ.get('TOSHL_API_TOKEN', '').strip()
    if not token:
        raise RuntimeError(
            'TOSHL_API_TOKEN is missing. Put it in the local .env file and run '
            'with: uv run --env-file .env python sync_toshl_taxonomy.py'
        )

    categories_response = _fetch_collection('categories', token)
    tags_response = _fetch_collection('tags', token)
    taxonomy = build_taxonomy(categories_response, tags_response)

    output_path = Path(output_path).expanduser()
    if dry_run:
        return output_path, taxonomy

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=output_path.parent,
        prefix=f'.{output_path.name}.', suffix='.tmp', delete=False,
    ) as temporary:
        json.dump(taxonomy, temporary, ensure_ascii=False, indent=2)
        temporary.write('\n')
        temp_path = Path(temporary.name)
    temp_path.replace(output_path)
    return output_path, taxonomy


def main():
    parser = argparse.ArgumentParser(
        description='Refresh the local Toshl category/tag mapping file.'
    )
    parser.add_argument(
        '-o', '--output', type=Path, default=_DEFAULT_OUTPUT,
        help=f'output taxonomy JSON (default: {_DEFAULT_OUTPUT})',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='fetch and summarize changes without writing the output file',
    )
    args = parser.parse_args()

    try:
        output_path, taxonomy = sync_taxonomy(args.output, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))

    summary = (
        f'{len(taxonomy["categories"])} categories, '
        f'{len(taxonomy["tags"])} tags, '
        f'{len(taxonomy["category_tags"])} category/tag links'
    )
    if args.dry_run:
        print(f'Dry run: would write {summary} to {output_path}')
    else:
        print(f'Updated {output_path}: {summary}')


if __name__ == '__main__':
    main()
