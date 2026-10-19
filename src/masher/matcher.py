"""Exact transaction classification against learned mappings and Toshl taxonomy."""

import json
import re
from pathlib import Path

import pandas as pd

_DATA_DIR = Path(__file__).resolve().parents[2] / 'data'
_DEFAULT_TAXONOMY = _DATA_DIR / 'toshl_categories_tags.json'
_DEFAULT_MAPPINGS = _DATA_DIR / 'mappings.json'
_DEFAULT_OMISSIONS = _DATA_DIR / 'omitted_descriptions.json'


def _description_key(value):
    """Normalize whitespace and case for exact description lookups."""
    if pd.isna(value):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()

def _load_mappings(mappings_path, taxonomy_path):
    """Load learned mappings and reject category/tag IDs outside the taxonomy."""
    taxonomy = json.loads(Path(taxonomy_path).read_text(encoding='utf-8'))
    mappings_data = json.loads(Path(mappings_path).read_text(encoding='utf-8'))

    category_ids = {str(category['id']) for category in taxonomy['categories']}
    valid_links = {
        (str(link['category_id']), str(link['tag_id']))
        for link in taxonomy['category_tags']
    }
    known_tag_ids = {str(tag['id']) for tag in taxonomy['tags']}
    linked_tag_ids = {tag_id for _, tag_id in valid_links}
    unassigned_tag_ids = known_tag_ids - linked_tag_ids

    mapping_by_description = {}
    for mapping in mappings_data.get('mappings', []):
        key = _description_key(mapping.get('description'))
        category_id = str(mapping.get('category_id', ''))
        tag_ids = [str(tag_id) for tag_id in mapping.get('tag_ids', [])]

        if not key:
            raise ValueError('Every mapping needs a non-empty description')
        if category_id not in category_ids:
            raise ValueError(
                f"Mapping {mapping.get('description')!r} references unknown category {category_id!r}"
            )
        for tag_id in tag_ids:
            if tag_id not in known_tag_ids:
                raise ValueError(f'Mapping references unknown tag {tag_id!r}')
            if tag_id not in unassigned_tag_ids and (category_id, tag_id) not in valid_links:
                raise ValueError(
                    f"Tag {tag_id!r} is not associated with category {category_id!r}"
                )
        if key in mapping_by_description:
            raise ValueError(f'Duplicate mapping for description {mapping.get("description")!r}')
        mapping_by_description[key] = {
            'category_id': category_id,
            'tag_ids': tag_ids,
        }

    return mapping_by_description

def match_transactions(
    transactions,
    mappings_path=_DEFAULT_MAPPINGS,
    taxonomy_path=_DEFAULT_TAXONOMY,
    omissions_path=_DEFAULT_OMISSIONS,
):
    """Add exact category/tag matches and an ``exact`` or ``unknown`` status.

    Mappings are keyed by ``normalised_description``. Descriptions listed in
    ``data/omitted_descriptions.json`` are excluded from the returned copy.
    Other rows gain ``category_id``, ``tag_ids``, and ``match_method`` columns.
    """
    mapping_by_description = _load_mappings(mappings_path, taxonomy_path)
    result = transactions.copy()
    keys = result['normalised_description'].map(_description_key)
    omissions_path = Path(omissions_path)
    if omissions_path.exists():
        omission_data = json.loads(omissions_path.read_text(encoding='utf-8'))
        omitted_keys = {_description_key(value) for value in omission_data.get('descriptions', [])}
        keep_rows = ~keys.isin(omitted_keys)
        result = result.loc[keep_rows].copy()
        keys = keys.loc[keep_rows]

    result['category_id'] = pd.NA
    result['tag_ids'] = pd.Series([[] for _ in range(len(result))], index=result.index, dtype=object)
    result['match_method'] = 'unknown'
    result['toshl_description'] = ''

    for index, key in keys.items():
        mapping = mapping_by_description.get(key)
        if mapping is not None:
            result.at[index, 'category_id'] = mapping['category_id']
            result.at[index, 'tag_ids'] = mapping['tag_ids']
            result.at[index, 'match_method'] = 'exact'

    return result


def main():
    """Parse statement files, normalize transactions, and classify them."""
    import argparse
    from datetime import datetime

    from .normalise import normalise_description
    from .parse import parse_hdfc_statement, parse_icici_statement, parse_statements

    parser = argparse.ArgumentParser(
        description='Normalize transactions and match them to Toshl categories and tags.'
    )
    parser.add_argument('hdfc_input', nargs='?', help='optional HDFC statement in .txt format')
    parser.add_argument('icici_input', nargs='?', help='optional ICICI statement in .xls format')
    parser.add_argument('--start-date', help='start date in DD/MM/YYYY format')
    parser.add_argument('--end-date', help='end date in DD/MM/YYYY format')
    parser.add_argument(
        '--export-to-csv',
        nargs='?',
        const='',
        default=None,
        metavar='FILEPATH',
        help='export matches to CSV, optionally specifying the output filepath',
    )
    args = parser.parse_args()

    try:
        start_date = (
            datetime.strptime(args.start_date, '%d/%m/%Y').date()
            if args.start_date else None
        )
        end_date = (
            datetime.strptime(args.end_date, '%d/%m/%Y').date()
            if args.end_date else None
        )
    except ValueError as error:
        parser.error(str(error))

    if start_date and end_date and start_date > end_date:
        parser.error('--start-date must be on or before --end-date')

    if not args.hdfc_input and not args.icici_input:
        parser.error('provide at least one statement input')

    if args.hdfc_input and args.icici_input:
        parsed_df = parse_statements(
            args.hdfc_input, args.icici_input, start_date, end_date
        )
    else:
        single_input = args.hdfc_input or args.icici_input
        if Path(single_input).suffix.lower() in {'.xls', '.xlsx'}:
            parsed_df = parse_icici_statement(single_input, start_date, end_date)
        else:
            parsed_df = parse_hdfc_statement(single_input, start_date, end_date)

    normalised_df = normalise_description(parsed_df)
    matched_df = match_transactions(normalised_df)
    print(matched_df.to_string(index=False))

    if args.export_to_csv is not None:
        filepath = args.export_to_csv
        if not filepath:
            from datetime import datetime as _datetime
            filepath = f"{_datetime.now():%Y%m%d_%H%M%S}_matched_statement.csv"
        export_df = matched_df.copy()
        export_df['tag_ids'] = export_df['tag_ids'].map(json.dumps)
        export_df.to_csv(filepath, index=False)
        print(f'Exported CSV: {filepath}')


if __name__ == '__main__':
    main()
