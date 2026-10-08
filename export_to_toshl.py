"""Convert reviewed transactions to Toshl's CSV import layout."""

import argparse
import json
from pathlib import Path

import pandas as pd

_DATA_DIR = Path(__file__).parent / 'data'
_DEFAULT_TAXONOMY = _DATA_DIR / 'toshl_categories_tags.json'
_COLUMNS = [
    'Date', 'Category', 'Expense Amount', 'Tags',
    'Description', 'Account', 'Currency',
]


def _parse_tag_ids(value, row_number):
    if value is None or pd.isna(value) or value == '':
        return []
    try:
        tag_ids = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as error:
        raise ValueError(f'Invalid tag_ids on input row {row_number}: {value!r}') from error
    if not isinstance(tag_ids, list):
        raise ValueError(f'tag_ids must be a JSON list on input row {row_number}')
    return [str(tag_id) for tag_id in tag_ids]


def export_to_toshl(input_path, output_path=None, taxonomy_path=_DEFAULT_TAXONOMY,
                    skip_unclassified=False):
    """Convert the reviewed CSV and resolve Toshl category/tag IDs to names."""
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_name(
        f'{input_path.stem}_toshl.csv'
    )

    transactions = pd.read_csv(
        input_path,
        dtype={'category_id': 'string', 'tag_ids': 'string'},
    )
    required_columns = {'date', 'amount', 'category_id', 'tag_ids'}
    missing = required_columns - set(transactions.columns)
    if missing:
        raise ValueError(f'Input CSV is missing columns: {sorted(missing)}')

    taxonomy = json.loads(Path(taxonomy_path).read_text(encoding='utf-8'))
    categories = {str(item['id']): item['name'] for item in taxonomy['categories']}
    tags = {str(item['id']): item['name'] for item in taxonomy['tags']}
    valid_links = {
        (str(link['category_id']), str(link['tag_id']))
        for link in taxonomy['category_tags']
    }
    linked_tag_ids = {tag_id for _, tag_id in valid_links}
    unassigned_tag_ids = set(tags) - linked_tag_ids

    missing_categories = [
        row_number
        for row_number, category_id in enumerate(transactions['category_id'], start=2)
        if pd.isna(category_id) or not str(category_id).strip()
    ]
    if missing_categories and not skip_unclassified:
        row_list = ', '.join(map(str, missing_categories))
        raise ValueError(
            f'{len(missing_categories)} transaction(s) have no category '
            f'(CSV row(s): {row_list}). Review them first, or use --skip-unclassified '
            'to export only classified transactions.'
        )

    output_rows = []
    for row_number, (_, transaction) in enumerate(transactions.iterrows(), start=2):
        category_value = transaction['category_id']
        if pd.isna(category_value) or not str(category_value).strip():
            continue
        category_id = str(category_value).strip()
        if category_id not in categories:
            raise ValueError(
                f'Unknown category ID {category_id!r} on input row {row_number}'
            )

        tag_ids = _parse_tag_ids(transaction['tag_ids'], row_number)
        tag_names = []
        for tag_id in tag_ids:
            if tag_id not in tags:
                raise ValueError(f'Unknown tag ID {tag_id!r} on input row {row_number}')
            if tag_id not in unassigned_tag_ids and (category_id, tag_id) not in valid_links:
                raise ValueError(
                    f'Tag {tag_id!r} is not associated with category {category_id!r} '
                    f'on input row {row_number}'
                )
            tag_names.append(tags[tag_id])

        date_value = pd.to_datetime(transaction['date'], errors='coerce')
        if pd.isna(date_value):
            raise ValueError(f'Invalid date on input row {row_number}: {transaction["date"]!r}')
        amount_value = pd.to_numeric(transaction['amount'], errors='coerce')
        if pd.isna(amount_value):
            raise ValueError(f'Invalid amount on input row {row_number}: {transaction["amount"]!r}')
        amount_text = format(abs(float(amount_value)), '.15g')

        toshl_description = transaction.get('toshl_description', '')
        if pd.isna(toshl_description):
            toshl_description = ''
        else:
            toshl_description = str(toshl_description).strip()

        output_rows.append({
            'Date': date_value.strftime('%d-%m-%Y'),
            'Category': categories[category_id],
            'Expense Amount': amount_text,
            'Tags': ', '.join(tag_names),
            'Description': toshl_description,
            'Account': 'Cash',
            'Currency': 'INR',
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(output_rows, columns=_COLUMNS).to_csv(
        output_path, index=False, encoding='utf-8-sig'
    )
    skipped_count = len(missing_categories) if skip_unclassified else 0
    return output_path, len(output_rows), skipped_count


def main():
    parser = argparse.ArgumentParser(
        description='Convert matched_reviewed.csv to Toshl import CSV format.'
    )
    parser.add_argument('input_csv', help='reviewed transaction CSV from reviewer.py')
    parser.add_argument('-o', '--output', help='output path; defaults to <input>_toshl.csv')
    parser.add_argument(
        '--skip-unclassified',
        action='store_true',
        help='omit rows without a category instead of stopping the export',
    )
    args = parser.parse_args()

    try:
        output_path, exported, skipped = export_to_toshl(
            args.input_csv,
            args.output,
            skip_unclassified=args.skip_unclassified,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f'Wrote {exported} Toshl import rows to {output_path}')
    if skipped:
        print(f'Skipped {skipped} unclassified transaction(s).')


if __name__ == '__main__':
    main()
