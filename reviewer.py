"""Interactively review unmatched transaction classifications."""

import argparse
import json
import re
from pathlib import Path

import pandas as pd

_DATA_DIR = Path(__file__).parent / 'data'
_DEFAULT_TAXONOMY = _DATA_DIR / 'toshl_categories_tags.json'
_DEFAULT_MAPPINGS = _DATA_DIR / 'mappings.json'
_DEFAULT_OMISSIONS = _DATA_DIR / 'omitted_descriptions.json'


def _key(value):
    if pd.isna(value):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()


def _parse_tag_ids(value):
    if value is None or pd.isna(value) or value == '':
        return []
    if isinstance(value, list):
        return [str(tag_id) for tag_id in value]
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(tag_id) for tag_id in parsed] if isinstance(parsed, list) else []


def _select_category(categories, suggested_id=None):
    suggested_id = str(suggested_id) if suggested_id else None
    suggested_number = None
    print('Categories:')
    for number, category in enumerate(categories, start=1):
        category_id = str(category['id'])
        suffix = ' [suggested]' if category_id == suggested_id else ''
        print(f"  {number}. {category['name']} [{category_id}]{suffix}")
        if category_id == suggested_id:
            suggested_number = number

    while True:
        prompt = 'Choose category number'
        if suggested_number:
            prompt += f' (Enter accepts {suggested_number})'
        answer = input(f'{prompt}; Enter to skip, x to omit this description, q to finish: ').strip()
        if answer.lower() == 'q':
            return 'quit'
        if answer.lower() == 'x':
            return 'omit'
        if not answer:
            return categories[suggested_number - 1] if suggested_number else None

        for number, category in enumerate(categories, start=1):
            if answer == str(number) or answer == str(category['id']):
                return category
        print('Enter a listed number or category ID, Enter to skip, x to omit, or q to finish.')


def _select_tags(tags, suggested_ids=None):
    suggested_ids = [str(tag_id) for tag_id in (suggested_ids or [])]
    if not tags:
        print('This category has no available tags.')
        return []

    print('Tags:')
    for number, tag in enumerate(tags, start=1):
        tag_id = str(tag['id'])
        suffix = ' [suggested]' if tag_id in suggested_ids else ''
        print(f"  {number}. {tag['name']} [{tag_id}]{suffix}")

    suggested_numbers = [
        str(number)
        for number, tag in enumerate(tags, start=1)
        if str(tag['id']) in suggested_ids
    ]
    while True:
        prompt = 'Choose tag number(s), comma-separated'
        if suggested_numbers:
            prompt += f" (Enter accepts {', '.join(suggested_numbers)})"
        answer = input(f'{prompt}; Enter/0 for no tag, x to omit this description, q to finish: ').strip()
        if answer.lower() == 'q':
            return 'quit'
        if answer.lower() == 'x':
            return 'omit'
        if not answer:
            return suggested_ids if suggested_numbers else []
        if answer == '0':
            return []
        try:
            numbers = [int(part.strip()) for part in answer.split(',')]
        except ValueError:
            numbers = []
        if numbers and all(1 <= number <= len(tags) for number in numbers):
            return list(dict.fromkeys(str(tags[number - 1]['id']) for number in numbers))
        print('Enter tag numbers separated by commas, 0 for no tag, x to omit, or q to finish.')


def _save_mappings(reviewed, mappings_path, taxonomy):
    path = Path(mappings_path)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'mappings': []}
    mappings = data.setdefault('mappings', [])
    existing = {_key(item.get('description')): item for item in mappings}

    category_ids = {str(item['id']) for item in taxonomy['categories']}
    valid_links = {
        (str(link['category_id']), str(link['tag_id']))
        for link in taxonomy['category_tags']
    }
    known_tag_ids = {str(item['id']) for item in taxonomy['tags']}
    unassigned_tag_ids = known_tag_ids - {tag_id for _, tag_id in valid_links}

    added = changed = unchanged = 0
    for description_key, mapping in reviewed.items():
        category_id = mapping['category_id']
        tag_ids = mapping['tag_ids']
        if category_id not in category_ids:
            raise ValueError(f"Unknown category ID {category_id!r}")
        for tag_id in tag_ids:
            if tag_id not in known_tag_ids:
                raise ValueError(f"Unknown tag ID {tag_id!r}")
            if tag_id not in unassigned_tag_ids and (category_id, tag_id) not in valid_links:
                raise ValueError(f"Tag {tag_id!r} is not linked to category {category_id!r}")

        new_record = {
            'description': mapping['description'],
            'category_id': category_id,
            'tag_ids': tag_ids,
            'source': 'manual',
        }
        old_record = existing.get(description_key)
        if old_record is None:
            mappings.append(new_record)
            existing[description_key] = new_record
            added += 1
        elif (
            str(old_record.get('category_id')) == category_id
            and [str(tag_id) for tag_id in old_record.get('tag_ids', [])] == tag_ids
        ):
            unchanged += 1
        else:
            old_record.update(new_record)
            changed += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return {'added': added, 'changed': changed, 'unchanged': unchanged}


def _save_omissions(descriptions, omissions_path=_DEFAULT_OMISSIONS):
    path = Path(omissions_path)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'descriptions': []}
    existing = {_key(value) for value in data.get('descriptions', [])}
    added = 0
    for description in descriptions:
        key = _key(description)
        if key and key not in existing:
            data.setdefault('descriptions', []).append(description)
            existing.add(key)
            added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return added


def review_matches(input_path, output_path=None, mappings_path=_DEFAULT_MAPPINGS,
                   taxonomy_path=_DEFAULT_TAXONOMY, omissions_path=_DEFAULT_OMISSIONS,
                   update_mappings=True):
    """Prompt for uncategorized/unreviewed descriptions and save reviewed CSV."""
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_name(
        f'{input_path.stem}_reviewed.csv'
    )
    transactions = pd.read_csv(input_path, dtype={'category_id': 'string', 'tag_ids': 'string'})
    existing_omissions = Path(omissions_path)
    preexisting_omitted_count = 0
    if existing_omissions.exists():
        omission_data = json.loads(existing_omissions.read_text(encoding='utf-8'))
        omitted_keys = {_key(value) for value in omission_data.get('descriptions', [])}
        description_keys = transactions['normalised_description'].map(_key)
        keep_rows = ~description_keys.isin(omitted_keys)
        preexisting_omitted_count = int((~keep_rows).sum())
        transactions = transactions.loc[keep_rows].copy()
    required = {'normalised_description', 'category_id', 'tag_ids', 'match_method'}
    missing = required - set(transactions.columns)
    if missing:
        raise ValueError(f'Matched CSV is missing columns: {sorted(missing)}')

    taxonomy = json.loads(Path(taxonomy_path).read_text(encoding='utf-8'))
    categories = taxonomy['categories']
    tags_by_id = {str(tag['id']): tag for tag in taxonomy['tags']}
    tags_by_category = {}
    linked_tag_ids = set()
    for link in taxonomy['category_tags']:
        category_id = str(link['category_id'])
        tag_id = str(link['tag_id'])
        tags_by_category.setdefault(category_id, []).append(tags_by_id[tag_id])
        linked_tag_ids.add(tag_id)
    unassigned_tags = [
        tag for tag_id, tag in tags_by_id.items() if tag_id not in linked_tag_ids
    ]

    descriptions = transactions['normalised_description'].map(_key)
    statuses = transactions['match_method'].fillna('').astype(str).str.casefold()
    missing_category = transactions['category_id'].isna() | transactions['category_id'].eq('')
    needs_review = missing_category | statuses.isin({'unknown', 'fuzzy', 'review'})
    review_keys = list(dict.fromkeys(descriptions.loc[needs_review].tolist()))
    # Drop blank descriptions while preserving first-seen order.
    review_keys = [key for key in review_keys if key]

    reviewed = {}
    omitted_indices = set()
    omitted_descriptions = []
    for position, description_key in enumerate(review_keys, start=1):
        group_indices = transactions.index[descriptions.eq(description_key)]
        needs_review_indices = [index for index in group_indices if needs_review.loc[index]]
        if not needs_review_indices:
            continue
        first = transactions.loc[needs_review_indices[0]]
        current_category = first.get('category_id')
        suggested_category = None if pd.isna(current_category) else str(current_category)
        suggested_tags = _parse_tag_ids(first.get('tag_ids'))
        amount_series = pd.to_numeric(
            transactions.loc[needs_review_indices, 'amount'], errors='coerce'
        ) if 'amount' in transactions else pd.Series(dtype=float)
        total_amount = amount_series.sum()

        print(f'\nReview {position}/{len(review_keys)}: {first["normalised_description"]}')
        print(f"  Transactions: {len(needs_review_indices)}; total amount: {total_amount:g}")
        if 'date' in transactions:
            print('  Entries:')
            for index in needs_review_indices:
                date_value = pd.to_datetime(transactions.at[index, 'date'], errors='coerce')
                date_text = (
                    date_value.strftime('%d-%m-%Y')
                    if not pd.isna(date_value) else 'unknown date'
                )
                amount = transactions.at[index, 'amount'] if 'amount' in transactions else ''
                print(f'    {date_text} | amount {amount}')
        if 'type' in transactions:
            print('  Statement type(s): ' + ', '.join(
                transactions.loc[needs_review_indices, 'type'].dropna().astype(str).unique()
            ))

        category = _select_category(categories, suggested_category)
        if category == 'quit':
            break
        if category == 'omit':
            omitted_indices.update(group_indices)
            omitted_descriptions.append(str(first['normalised_description']).strip())
            print(f'  Omitted {len(group_indices)} transaction(s) with this description.')
            continue
        if category is None:
            print('  Skipped.')
            continue

        category_id = str(category['id'])
        available_tags = tags_by_category.get(category_id, []) + unassigned_tags
        same_suggestion = category_id == suggested_category
        tag_ids = _select_tags(available_tags, suggested_tags if same_suggestion else [])
        if tag_ids == 'quit':
            break
        if tag_ids == 'omit':
            omitted_indices.update(group_indices)
            omitted_descriptions.append(str(first['normalised_description']).strip())
            print(f'  Omitted {len(group_indices)} transaction(s) with this description.')
            continue

        transactions.loc[group_indices, 'category_id'] = category_id
        transactions.loc[group_indices, 'tag_ids'] = json.dumps(tag_ids)
        transactions.loc[group_indices, 'match_method'] = 'manual'
        reviewed[description_key] = {
            'description': str(first['normalised_description']).strip(),
            'category_id': category_id,
            'tag_ids': tag_ids,
        }
        print('  Saved classification for this description.')

    if omitted_indices:
        transactions = transactions.drop(index=list(omitted_indices))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    transactions.to_csv(output_path, index=False)
    print(f'Wrote reviewed transactions to {output_path}')
    if preexisting_omitted_count:
        print(f'Filtered {preexisting_omitted_count} previously omitted transaction(s).')

    if omitted_descriptions:
        added_omissions = _save_omissions(omitted_descriptions, omissions_path)
        print(
            f'Omitted {len(omitted_indices)} transaction(s); '
            f'added {added_omissions} persistent description omission(s).'
        )

    if update_mappings and reviewed:
        counts = _save_mappings(reviewed, mappings_path, taxonomy)
        print(
            f"Updated mappings: {counts['added']} added, "
            f"{counts['changed']} changed, {counts['unchanged']} unchanged"
        )
    elif not update_mappings:
        print('Mappings were not changed.')
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description='Review unmatched classifications in a matcher CSV.'
    )
    parser.add_argument('matched_csv', help='CSV generated by matcher.py')
    parser.add_argument('-o', '--output', help='reviewed CSV path; defaults to <input>_reviewed.csv')
    parser.add_argument(
        '--no-update-mappings',
        action='store_true',
        help='write reviewed CSV without updating data/mappings.json',
    )
    args = parser.parse_args()
    try:
        review_matches(
            args.matched_csv,
            args.output,
            update_mappings=not args.no_update_mappings,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
