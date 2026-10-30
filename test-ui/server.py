"""Small local web server for reviewing and exporting statement transactions."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_file

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / 'src'
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from masher.export_to_toshl import export_to_toshl
from masher.matcher import match_transactions
from masher.normalise import normalise_description
from masher.parse import parse_hdfc_statement, parse_icici_statement
from masher.reviewer import _save_mappings, _save_omissions
from masher.utils.descriptions import description_key
from masher.utils.paths import MAPPINGS_PATH, OMISSIONS_PATH, TAXONOMY_PATH

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

review_state: dict = {}


def _path_arg(name: str, default: Path) -> Path:
    return Path(request.form.get(name, str(default))).expanduser().resolve()


def _json_value(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (list, dict)):
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'item'):
        value = value.item()
    if pd.isna(value):
        return None
    return value


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    records = []
    for index, row in frame.reset_index(drop=True).iterrows():
        record = {key: _json_value(value) for key, value in row.to_dict().items()}
        tags = record.get('tag_ids')
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = []
        record['tag_ids'] = tags if isinstance(tags, list) else []
        record['index'] = index
        record['omit_action'] = 'Include'
        records.append(record)
    return records


def _load_taxonomy(path: Path) -> dict:
    taxonomy = json.loads(path.read_text(encoding='utf-8'))
    categories = taxonomy['categories']
    tags = taxonomy['tags']
    links = taxonomy['category_tags']
    linked = {str(link['tag_id']) for link in links}
    return {
        'categories': categories,
        'tags': tags,
        'links': links,
        'unassigned_tag_ids': [str(tag['id']) for tag in tags if str(tag['id']) not in linked],
    }


@app.get('/')
def index():
    return app.send_static_file('index.html')


@app.get('/api/config')
def config():
    try:
        taxonomy_path = Path(request.args.get('taxonomy', TAXONOMY_PATH)).expanduser()
        taxonomy = _load_taxonomy(taxonomy_path)
        return jsonify({
            **taxonomy,
            'taxonomy_path': str(taxonomy_path),
            'mappings_path': str(Path(request.args.get('mappings', MAPPINGS_PATH)).expanduser()),
            'omissions_path': str(Path(request.args.get('omissions', OMISSIONS_PATH)).expanduser()),
        })
    except (OSError, KeyError, json.JSONDecodeError) as error:
        return jsonify(error=str(error)), 400


@app.post('/api/process')
def process():
    hdfc = request.files.get('hdfc')
    icici = request.files.get('icici')
    if not (hdfc and hdfc.filename or icici and icici.filename):
        return jsonify(error='Choose at least one statement file.'), 400

    taxonomy_path = _path_arg('taxonomy', TAXONOMY_PATH)
    mappings_path = _path_arg('mappings', MAPPINGS_PATH)
    omissions_path = _path_arg('omissions', OMISSIONS_PATH)
    try:
        start_date = date.fromisoformat(request.form['start_date']) if request.form.get('start_date') else None
        end_date = date.fromisoformat(request.form['end_date']) if request.form.get('end_date') else None
        if start_date and end_date and start_date > end_date:
            raise ValueError('The start date must be on or before the end date.')
        if not taxonomy_path.is_file():
            raise FileNotFoundError(f'Toshl taxonomy not found: {taxonomy_path}')
        if not mappings_path.is_file():
            raise FileNotFoundError(f'Mappings file not found: {mappings_path}')
        with tempfile.TemporaryDirectory(prefix='masher-ui-') as temp_dir:
            parsed = []
            for uploaded, bank, parser, extensions in (
                (hdfc, 'hdfc', parse_hdfc_statement, {'.txt', '.csv'}),
                (icici, 'icici', parse_icici_statement, {'.xls', '.xlsx'}),
            ):
                if not uploaded or not uploaded.filename:
                    continue
                suffix = Path(uploaded.filename).suffix.lower()
                if suffix not in extensions:
                    raise ValueError(f'{bank.upper()} file must use {", ".join(sorted(extensions))}.')
                input_path = Path(temp_dir) / f'{bank}{suffix}'
                uploaded.save(input_path)
                parsed.append(parser(input_path, start_date, end_date))

            if not parsed:
                raise ValueError('No statement files were provided.')
            normalized = normalise_description(pd.concat(parsed, ignore_index=True))
            matched = match_transactions(
                normalized,
                mappings_path=mappings_path,
                taxonomy_path=taxonomy_path,
                omissions_path=omissions_path,
            ).reset_index(drop=True)

        taxonomy = _load_taxonomy(taxonomy_path)
        review_state.clear()
        review_state.update({
            'transactions': _frame_records(matched),
            'taxonomy': taxonomy,
            'taxonomy_path': taxonomy_path,
            'mappings_path': mappings_path,
            'omissions_path': omissions_path,
        })
        return jsonify(
            transactions=review_state['transactions'],
            categories=taxonomy['categories'],
            tags=taxonomy['tags'],
            category_tags=taxonomy['links'],
            unassigned_tag_ids=taxonomy['unassigned_tag_ids'],
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return jsonify(error=str(error)), 400


@app.post('/api/save')
def save():
    if not review_state:
        return jsonify(error='Process statement files before saving a review.'), 400
    payload = request.get_json(silent=True) or {}
    submitted = payload.get('transactions')
    if not isinstance(submitted, list) or len(submitted) != len(review_state['transactions']):
        return jsonify(error='The review data is incomplete. Process the statements again.'), 400

    original_rows = review_state['transactions']
    taxonomy = review_state['taxonomy']
    category_ids = {str(item['id']) for item in taxonomy['categories']}
    tags_by_id = {str(item['id']): item for item in taxonomy['tags']}
    links = {(str(item['category_id']), str(item['tag_id'])) for item in taxonomy['links']}
    unassigned = set(taxonomy['unassigned_tag_ids'])
    selections: dict[str, list[dict]] = {}
    omitted_keys: set[str] = set()
    always_omitted: dict[str, str] = {}
    omitted_rows: set[int] = set()
    reviewed = []
    mapping_updates = {}

    try:
        for position, original in enumerate(original_rows):
            row = submitted[position]
            if int(row.get('index', position)) != position:
                raise ValueError('Transaction row order changed. Refresh and try again.')
            key = description_key(original.get('normalised_description'))
            category_id = str(row.get('category_id') or '') or None
            tag_ids = [str(value) for value in row.get('tag_ids', [])]
            action = row.get('omit_action', 'Include')
            if action not in {'Include', 'Omit once', 'Omit always'}:
                raise ValueError(f'Unknown inclusion value: {action!r}')
            if category_id and category_id not in category_ids:
                raise ValueError(f'Unknown category ID: {category_id}')
            if tag_ids and not category_id:
                raise ValueError('Choose a category before selecting tags.')
            for tag_id in tag_ids:
                if tag_id not in tags_by_id:
                    raise ValueError(f'Unknown tag ID: {tag_id}')
                if tag_id not in unassigned and (category_id, tag_id) not in links:
                    raise ValueError(f'Tag {tags_by_id[tag_id]["name"]!r} is not available for the selected category.')
            amount = float(row['amount'])
            description = str(row.get('toshl_description') or '').strip()
            if action != 'Include':
                if key:
                    omitted_keys.add(key)
                    if action == 'Omit always':
                        always_omitted[key] = str(original.get('normalised_description') or '').strip()
                else:
                    omitted_rows.add(position)
            selection_key = key or f'__row_{position}'
            original_tags = original.get('tag_ids') or []
            selected = {
                'description': str(original.get('normalised_description') or '').strip(),
                'key': key,
                'category_id': category_id,
                'tag_ids': tag_ids,
                'toshl_description': description,
                'original_category_id': original.get('category_id') or None,
                'original_tag_ids': [str(value) for value in original_tags],
                'original_toshl_description': str(original.get('toshl_description') or '').strip(),
            }
            selections.setdefault(selection_key, []).append(selected)
            reviewed.append({**original, 'amount': amount, 'index': position})

        resolved = {}
        for key, rows in selections.items():
            if key in omitted_keys or key in {f'__row_{index}' for index in omitted_rows}:
                continue
            edits = {
                (row['category_id'], tuple(row['tag_ids']), row['toshl_description'])
                for row in rows
                if (row['category_id'], tuple(row['tag_ids']), row['toshl_description']) !=
                (row['original_category_id'], tuple(row['original_tag_ids']), row['original_toshl_description'])
            }
            if len(edits) > 1:
                raise ValueError(f'Rows for {rows[0]["description"]!r} have conflicting edits. Repeated descriptions must share category, tags, and Toshl description.')
            if edits:
                category_id, tag_ids, description = next(iter(edits))
            else:
                category_id = rows[0]['original_category_id']
                tag_ids = tuple(rows[0]['original_tag_ids'])
                description = rows[0]['original_toshl_description']
            resolved[key] = (category_id, list(tag_ids), description)
            if category_id and rows[0]['key']:
                mapping_updates[rows[0]['key']] = {
                    'description': rows[0]['description'],
                    'category_id': category_id,
                    'tag_ids': list(tag_ids),
                }

        output_rows = []
        for row in reviewed:
            position = row['index']
            key = description_key(row.get('normalised_description'))
            if key in omitted_keys or position in omitted_rows:
                continue
            category_id, tag_ids, description = resolved[key or f'__row_{position}']
            row.update(category_id=category_id, tag_ids=tag_ids, toshl_description=description,
                       match_method='manual' if category_id else 'unknown')
            output_rows.append(row)

        if mapping_updates:
            mapping_counts = _save_mappings(mapping_updates, review_state['mappings_path'], taxonomy)
        else:
            mapping_counts = {'added': 0, 'changed': 0, 'unchanged': 0}
        if always_omitted:
            _save_omissions(list(always_omitted.values()), review_state['omissions_path'])

        reviewed_frame = pd.DataFrame(output_rows)
        csv_frame = reviewed_frame.drop(columns=['index', 'omit_action'], errors='ignore').copy()
        csv_frame['tag_ids'] = csv_frame['tag_ids'].map(json.dumps)
        reviewed_bytes = csv_frame.to_csv(index=False).encode('utf-8-sig')
        with tempfile.TemporaryDirectory(prefix='masher-export-') as temp_dir:
            input_path = Path(temp_dir) / 'reviewed.csv'
            output_path = Path(temp_dir) / 'toshl.csv'
            csv_frame.to_csv(input_path, index=False)
            _, exported_count, skipped_count = export_to_toshl(
                input_path, output_path,
                taxonomy_path=review_state['taxonomy_path'],
                skip_unclassified=bool(payload.get('skip_unclassified')),
            )
            toshl_bytes = output_path.read_bytes()
        review_state['downloads'] = {'reviewed': reviewed_bytes, 'toshl': toshl_bytes}
        return jsonify(
            message='Review saved and Toshl import prepared.',
            mappings=mapping_counts,
            transactions=len(output_rows),
            exported=exported_count,
            skipped=skipped_count,
            unclassified=sum(not row.get('category_id') for row in output_rows),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        return jsonify(error=str(error)), 400


@app.get('/api/download/<kind>')
def download(kind: str):
    downloads = review_state.get('downloads', {})
    if kind not in downloads:
        return jsonify(error='Save the review before downloading files.'), 404
    filename = 'matched_reviewed.csv' if kind == 'reviewed' else 'matched_reviewed_toshl.csv'
    return send_file(io.BytesIO(downloads[kind]), mimetype='text/csv', as_attachment=True, download_name=filename)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8765, debug=False, threaded=True)
