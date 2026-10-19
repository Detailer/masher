"""Local browser UI for statement parsing, review, and Toshl export."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / 'src'
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from masher.export_to_toshl import export_to_toshl
from masher.matcher import match_transactions
from masher.normalise import normalise_description
from masher.parse import parse_hdfc_statement, parse_icici_statement
from masher.reviewer import _save_mappings, _save_omissions
from masher.utils.descriptions import description_key as normalise_description_key
from masher.utils.paths import DATA_DIR

DEFAULT_DATA = DATA_DIR
PAGE_SIZE = 50

st.set_page_config(page_title='Statement review', layout='wide')
theme_css = Path(__file__).with_name('theme.css').read_text(encoding='utf-8')
st.markdown(f'<style>{theme_css}</style>', unsafe_allow_html=True)
st.markdown(
    '<div class="app-masthead"><div class="app-wordmark">Masher<br> '
    '<span class="app-wordmark-badge">Local</span></div>'
    '<div class="app-masthead-copy">'
    '<div class="app-masthead-title">Statement review</div>'
    '<div class="app-masthead-description">Review statement transactions, assign categories and tags, and prepare a Toshl import.</div>'
    '</div></div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header('Local data files')
    taxonomy_path = Path(st.text_input('Toshl categories and tags', str(DEFAULT_DATA / 'toshl_categories_tags.json'))).expanduser()
    mappings_path = Path(st.text_input('Saved mappings', str(DEFAULT_DATA / 'mappings.json'))).expanduser()
    omissions_path = Path(st.text_input('Omitted descriptions', str(DEFAULT_DATA / 'omitted_descriptions.json'))).expanduser()
    st.caption('The app reads and updates these files on this computer.')

    st.header('Transaction dates')
    filter_dates = st.checkbox('Limit date range', value=False)
    start_date = end_date = None
    if filter_dates:
        date_col1, date_col2 = st.columns(2)
        start_date = date_col1.date_input('From', value=None)
        end_date = date_col2.date_input('Through', value=None)
        if start_date and end_date and start_date > end_date:
            st.error('The start date must be on or before the end date.')

st.header('Add statements')
left, right = st.columns(2)
hdfc_file = left.file_uploader('HDFC statement (.txt or .csv)', type=['txt', 'csv'], key='hdfc_upload')
icici_file = right.file_uploader('ICICI statement (.xls or .xlsx)', type=['xls', 'xlsx'], key='icici_upload')


def _remember_page_edits(transactions, row_indices, run_id):
    """Keep edits from the visible page before Streamlit removes its widgets."""
    drafts = st.session_state.setdefault('review_edits', {})
    for index in row_indices:
        transaction = transactions.iloc[index]
        row_key = f'{run_id}_{index}'
        draft = drafts.get(index, {})

        category_value = transaction.get('category_id')
        baseline_category = (
            None if pd.isna(category_value) or not str(category_value).strip()
            else str(category_value)
        )
        category = st.session_state.get(
            f'category_{row_key}', draft.get('category_id', baseline_category)
        )

        raw_tags = transaction.get('tag_ids', [])
        if isinstance(raw_tags, str):
            try:
                raw_tags = json.loads(raw_tags)
            except json.JSONDecodeError:
                raw_tags = []
        baseline_tags = [str(tag_id) for tag_id in raw_tags] if isinstance(raw_tags, list) else []
        tag_key = f'tags_{row_key}_{category or "none"}'
        if tag_key in st.session_state:
            tag_ids = [str(tag_id) for tag_id in st.session_state[tag_key]]
        elif draft.get('category_id') == category and 'tag_ids' in draft:
            tag_ids = draft['tag_ids']
        elif category == baseline_category:
            tag_ids = baseline_tags
        else:
            tag_ids = []

        amount_key = f'amount_{row_key}'
        amount_value = transaction.get('amount')
        numeric_amount = pd.to_numeric(amount_value, errors='coerce')
        default_amount = float(numeric_amount) if not pd.isna(numeric_amount) else None
        amount = st.session_state.get(amount_key, draft.get('amount', default_amount))
        raw_toshl_description = transaction.get('toshl_description', '')
        baseline_toshl_description = (
            '' if pd.isna(raw_toshl_description) else str(raw_toshl_description).strip()
        )
        toshl_description = st.session_state.get(
            f'toshl_description_{row_key}',
            draft.get('toshl_description', baseline_toshl_description),
        )
        omit_action = st.session_state.get(
            f'omit_action_{row_key}', draft.get('omit_action', 'Include')
        )
        drafts[index] = {
            'category_id': category,
            'tag_ids': tag_ids,
            'amount': amount,
            'toshl_description': toshl_description,
            'omit_action': omit_action,
        }


def _review_edits_signature(edits):
    canonical = {str(index): edits[index] for index in sorted(edits, key=int)}
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()


def upload_signature():
    parts = [
        hdfc_file.name if hdfc_file else '',
        icici_file.name if icici_file else '',
        str(start_date),
        str(end_date),
    ]
    if hdfc_file:
        parts.append(hashlib.sha256(hdfc_file.getvalue()).hexdigest())
    if icici_file:
        parts.append(hashlib.sha256(icici_file.getvalue()).hexdigest())
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:12]


invalid_range = bool(start_date and end_date and start_date > end_date)
progress = None
if st.button('PROCESS', type='primary', disabled=not (hdfc_file or icici_file) or invalid_range):
    try:
        progress = st.progress(0, text='Checking statement files…')
        if not taxonomy_path.is_file():
            raise FileNotFoundError(f'Toshl taxonomy not found: {taxonomy_path}')
        if not mappings_path.is_file():
            raise FileNotFoundError(f'Mappings file not found: {mappings_path}')

        with tempfile.TemporaryDirectory(prefix='masher-ui-') as temp_dir:
            temp_dir = Path(temp_dir)
            parsed_frames = []
            statement_inputs = [
                (hdfc_file, parse_hdfc_statement, 'hdfc'),
                (icici_file, parse_icici_statement, 'icici'),
            ]
            statement_inputs = [item for item in statement_inputs if item[0] is not None]
            for position, (uploaded, parser, bank) in enumerate(statement_inputs, start=1):
                if uploaded is None:
                    continue
                progress.progress(
                    0.1 + 0.45 * (position - 1) / len(statement_inputs),
                    text=f'Parsing {bank.upper()} statement…',
                )
                extension = Path(uploaded.name).suffix.lower()
                accepted = {'.txt', '.csv'} if bank == 'hdfc' else {'.xls', '.xlsx'}
                if extension not in accepted:
                    raise ValueError(f'{bank.upper()} statement file must be one of: {", ".join(sorted(accepted))}.')
                input_path = temp_dir / f'{bank}{extension}'
                input_path.write_bytes(uploaded.getvalue())
                parsed_frames.append(parser(input_path, start_date, end_date))

            progress.progress(0.62, text='Normalizing transaction descriptions…')
            parsed = pd.concat(parsed_frames, ignore_index=True)
            normalized = normalise_description(parsed)
            progress.progress(0.82, text='Matching categories and tags…')
            matched = match_transactions(
                normalized,
                mappings_path=mappings_path,
                taxonomy_path=taxonomy_path,
                omissions_path=omissions_path,
            ).reset_index(drop=True)

        st.session_state['transactions'] = matched
        st.session_state['run_id'] = upload_signature()
        st.session_state['run_signature'] = upload_signature()
        st.session_state['taxonomy_path'] = str(taxonomy_path)
        st.session_state['mappings_path'] = str(mappings_path)
        st.session_state['omissions_path'] = str(omissions_path)
        st.session_state['review_page'] = 1
        st.session_state['review_edits'] = {}
        st.session_state['scroll_to_transaction_list'] = False
        st.session_state['sort_column'] = 'date'
        st.session_state['sort_ascending'] = False
        for key in ('reviewed_download', 'toshl_download', 'export_summary'):
            st.session_state.pop(key, None)
        progress.progress(1.0, text='Review is ready')
        st.success(f'Processed {len(matched)} transaction(s).')
    except Exception as error:
        if progress is not None:
            progress.empty()
        st.error(f'Could not process statements: {error}')

transactions = st.session_state.get('transactions')
if transactions is not None:
    if upload_signature() != st.session_state.get('run_signature'):
        st.warning('The selected files or date range changed. Run parsing and matching again to refresh this review.')
    else:
        taxonomy_path = Path(st.session_state['taxonomy_path'])
        mappings_path = Path(st.session_state['mappings_path'])
        omissions_path = Path(st.session_state['omissions_path'])
        taxonomy = json.loads(taxonomy_path.read_text(encoding='utf-8'))
        categories = taxonomy['categories']
        tags_by_id = {str(tag['id']): tag for tag in taxonomy['tags']}
        category_names = {str(item['id']): item['name'] for item in categories}
        links_by_category: dict[str, list[str]] = {}
        linked_tag_ids = set()
        for link in taxonomy['category_tags']:
            category_id, tag_id = str(link['category_id']), str(link['tag_id'])
            links_by_category.setdefault(category_id, []).append(tag_id)
            linked_tag_ids.add(tag_id)
        unassigned_tag_ids = [tag_id for tag_id in tags_by_id if tag_id not in linked_tag_ids]

        transactions = transactions.reset_index(drop=True).copy()
        if 'toshl_description' not in transactions:
            transactions['toshl_description'] = ''
        st.session_state['transactions'] = transactions
        keys = transactions['normalised_description'].map(normalise_description_key)
        st.header('Review transactions')
        matched_count = int(transactions['category_id'].notna().sum())
        metric1, metric2, metric3 = st.columns(3)
        metric1.metric('Transactions', len(transactions))
        metric2.metric('Matched', matched_count)
        metric3.metric('Need a category', len(transactions) - matched_count)
        st.caption('Each row is one transaction. Category, tags, Toshl description, and amount are editable; a mapping is learned by normalized description, so repeated descriptions share those values.')

        sort_column = st.session_state.get('sort_column', 'date')
        sort_ascending = st.session_state.get('sort_ascending', False)

        def tag_sort_label(value):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = []
            if not isinstance(value, list):
                return ''
            return ', '.join(tags_by_id.get(str(tag_id), {}).get('name', '') for tag_id in value).casefold()

        sort_values = pd.DataFrame(index=transactions.index)
        sort_values['date'] = pd.to_datetime(transactions['date'], errors='coerce')
        sort_values['transaction'] = transactions['normalised_description'].fillna('').astype(str).str.casefold()
        sort_values['amount'] = pd.to_numeric(transactions['amount'], errors='coerce')
        sort_values['category'] = transactions['category_id'].map(category_names).fillna('').astype(str).str.casefold()
        sort_values['tags'] = transactions['tag_ids'].map(tag_sort_label)
        sort_values['toshl_description'] = transactions['toshl_description'].fillna('').astype(str).str.casefold()
        sort_order = sort_values.sort_values(
            sort_column, ascending=sort_ascending, na_position='last', kind='stable'
        ).index.tolist()

        total_pages = max(1, (len(transactions) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(int(st.session_state.get('review_page', 1)), 1), total_pages)
        st.session_state['review_page'] = page
        start_index = (page - 1) * PAGE_SIZE
        end_index = min(start_index + PAGE_SIZE, len(transactions))
        st.markdown('<div id="transaction-list-start"></div>', unsafe_allow_html=True)
        if st.session_state.pop('scroll_to_transaction_list', False):
            components.html(
                '<script>window.parent.document.getElementById("transaction-list-start")?.scrollIntoView({behavior:"smooth",block:"start"});</script>',
                height=0,
            )
        st.caption(f'Showing transactions {start_index + 1}–{end_index} of {len(transactions)} · {PAGE_SIZE} rows per page')

        run_id = st.session_state['run_id']
        review_edits = st.session_state.setdefault('review_edits', {})
        head = st.columns([0.95, 2.0, 0.9, 1.65, 1.2, 1.4, 1.3])
        sortable_headers = (
            ('Date', 'date'),
            ('Transaction', 'transaction'),
            ('Amount', 'amount'),
            ('Category', 'category'),
            ('Tags', 'tags'),
            ('Description', 'toshl_description'),
        )
        for column, (label, key) in zip(head, sortable_headers):
            marker = (' ↓' if not sort_ascending else ' ↑') if key == sort_column else ''
            if column.button(f'{label}{marker}', key=f'sort_header_{key}', use_container_width=True):
                _remember_page_edits(transactions, sort_order[start_index:end_index], run_id)
                if key == sort_column:
                    st.session_state['sort_ascending'] = not sort_ascending
                else:
                    st.session_state['sort_column'] = key
                    st.session_state['sort_ascending'] = key not in {'date', 'amount'}
                st.session_state['scroll_to_transaction_list'] = True
                st.rerun()
        head[6].button(
            'Inclusion', use_container_width=True, disabled=True,
            key='inclusion_header',
        )

        for index in sort_order[start_index:end_index]:
            transaction = transactions.iloc[index]
            normalized_description = str(transaction.get('normalised_description') or '').strip()
            description_key = normalise_description_key(normalized_description)
            row_key = f'{run_id}_{index}'
            date_value = pd.to_datetime(transaction.get('date'), errors='coerce')
            date_label = date_value.strftime('%d-%m-%Y') if not pd.isna(date_value) else '—'
            amount = transaction.get('amount', '')
            numeric_amount = pd.to_numeric(amount, errors='coerce')
            amount_default = review_edits.get(index, {}).get(
                'amount', float(numeric_amount) if not pd.isna(numeric_amount) else None
            )
            category_value = transaction.get('category_id')
            existing_category = None if pd.isna(category_value) or not str(category_value).strip() else str(category_value)
            draft = review_edits.get(index, {})
            category_default = draft.get('category_id', existing_category)
            category_key = f'category_{row_key}'
            category_options = [None] + [str(item['id']) for item in categories]
            original_tags = transaction.get('tag_ids', [])
            if isinstance(original_tags, str):
                try:
                    original_tags = json.loads(original_tags)
                except json.JSONDecodeError:
                    original_tags = []
            if not isinstance(original_tags, list):
                original_tags = []
            original_tags = [str(tag_id) for tag_id in original_tags]
            raw_toshl_description = transaction.get('toshl_description', '')
            original_toshl_description = (
                '' if pd.isna(raw_toshl_description) else str(raw_toshl_description).strip()
            )
            toshl_description_default = draft.get(
                'toshl_description', original_toshl_description
            )

            row = st.columns([0.95, 2.0, 0.9, 1.65, 1.2, 1.4, 1.3], vertical_alignment='center')
            row[0].text(date_label)
            row[0].caption(str(transaction.get('type', '')))
            row[1].text(normalized_description or '(no normalized description)')
            row[1].caption(str(transaction.get('description', '')))
            if amount_default is not None:
                row[2].number_input(
                    'Amount', value=float(amount_default), step=0.01, format='%.2f',
                    key=f'amount_{row_key}', label_visibility='collapsed',
                )
            else:
                row[2].text('—')
            selected_category = row[3].selectbox(
                'Category', category_options,
                index=category_options.index(category_default) if category_default in category_options else 0,
                format_func=lambda value: 'Choose…' if value is None else category_names[value],
                key=category_key,
                label_visibility='collapsed',
            )
            tag_options = (links_by_category.get(selected_category, []) + unassigned_tag_ids) if selected_category else []
            if draft.get('category_id') == selected_category and 'tag_ids' in draft:
                suggested_tags = draft['tag_ids']
            else:
                suggested_tags = original_tags if selected_category == existing_category else []
            row[4].multiselect(
                'Tags', tag_options,
                default=[tag_id for tag_id in suggested_tags if tag_id in tag_options],
                format_func=lambda value: tags_by_id[value]['name'],
                key=f'tags_{row_key}_{selected_category or "none"}',
                disabled=not selected_category,
                placeholder='Choose tags',
                label_visibility='collapsed',
            )
            row[5].text_input(
                'Toshl description', value=toshl_description_default,
                key=f'toshl_description_{row_key}',
                placeholder='Description', label_visibility='collapsed',
            )
            omit_options = ['Include', 'Omit once', 'Omit always']
            omit_action = draft.get('omit_action', 'Include')
            row[6].selectbox(
                'Omission', omit_options,
                index=omit_options.index(omit_action) if omit_action in omit_options else 0,
                key=f'omit_action_{row_key}', label_visibility='collapsed',
            )
            st.divider()

        visible_indices = sort_order[start_index:end_index]
        _remember_page_edits(transactions, visible_indices, run_id)
        current_review_signature = _review_edits_signature(review_edits)
        saved_review_signature = st.session_state.get('saved_review_signature')
        if saved_review_signature and current_review_signature != saved_review_signature:
            for key in ('reviewed_download', 'toshl_download', 'export_summary'):
                st.session_state.pop(key, None)
            st.warning('You have unsaved changes. Save the review again to refresh the CSV downloads.')

        pager = st.columns([1.4, 2.2, 1.4])
        if pager[0].button('←  Previous', type='primary', use_container_width=True, disabled=page <= 1, key='previous_page'):
            _remember_page_edits(transactions, sort_order[start_index:end_index], run_id)
            st.session_state['review_page'] = page - 1
            st.session_state['scroll_to_transaction_list'] = True
            st.rerun()
        pager[1].markdown(f'<p align="center"><strong>Page {page} of {total_pages}</strong></p>', unsafe_allow_html=True)
        if pager[2].button('Next  →', type='primary', use_container_width=True, disabled=page >= total_pages, key='next_page'):
            _remember_page_edits(transactions, sort_order[start_index:end_index], run_id)
            st.session_state['review_page'] = page + 1
            st.session_state['scroll_to_transaction_list'] = True
            st.rerun()

        st.info(
            'Omit once excludes a transaction from these CSVs. Omit always also saves its normalized '
            'description so future runs skip it. Save the review to apply your changes and prepare the downloads.'
        )
        allow_skip = st.checkbox('Export classified transactions only if some rows have no category', value=False)
        save_clicked_bottom = st.button(
            'Save review and prepare Toshl CSV', type='primary', key='save_review_bottom'
        )
        save_progress = None
        if save_clicked_bottom:
            try:
                save_progress = st.progress(0, text='Collecting review changes…')
                _remember_page_edits(transactions, sort_order[start_index:end_index], run_id)
                review_edits = st.session_state.get('review_edits', {})
                save_progress.progress(0.2, text='Checking classifications and omissions…')
                selections = {}
                omitted_keys = set()
                always_omitted_keys = set()
                omitted_row_indices = set()
                for index, transaction in transactions.iterrows():
                    description = str(transaction.get('normalised_description') or '').strip()
                    description_key = normalise_description_key(description)
                    selection_key = description_key or ('row', index)
                    row_key = f'{run_id}_{index}'
                    category_value = transaction.get('category_id')
                    existing_category = None if pd.isna(category_value) or not str(category_value).strip() else str(category_value)
                    draft = review_edits.get(index, {})
                    category = draft.get('category_id', existing_category)
                    value = draft.get('tag_ids')
                    if value is None:
                        value = transaction.get('tag_ids', [])
                        try:
                            value = json.loads(value) if isinstance(value, str) else list(value or [])
                        except (json.JSONDecodeError, TypeError):
                            value = []
                    tag_ids = [str(tag_id) for tag_id in value]
                    raw_toshl_description = transaction.get('toshl_description', '')
                    original_toshl_description = (
                        '' if pd.isna(raw_toshl_description) else str(raw_toshl_description).strip()
                    )
                    toshl_description = str(
                        draft.get('toshl_description', original_toshl_description) or ''
                    ).strip()
                    omit_action = draft.get('omit_action', 'Include')
                    if omit_action in {'Omit once', 'Omit always'}:
                        if description_key:
                            omitted_keys.add(description_key)
                            if omit_action == 'Omit always':
                                always_omitted_keys.add(description_key)
                        else:
                            omitted_row_indices.add(index)
                    original_category_value = transaction.get('category_id')
                    original_category = None if pd.isna(original_category_value) or not str(original_category_value).strip() else str(original_category_value)
                    original_tag_value = transaction.get('tag_ids', [])
                    try:
                        original_tag_ids = json.loads(original_tag_value) if isinstance(original_tag_value, str) else list(original_tag_value or [])
                    except (json.JSONDecodeError, TypeError):
                        original_tag_ids = []
                    selections.setdefault(selection_key, []).append({
                        'description': description,
                        'normalised_key': description_key,
                        'category_id': category,
                        'tag_ids': tag_ids,
                        'toshl_description': toshl_description,
                        'original_toshl_description': original_toshl_description,
                        'original_category_id': original_category,
                        'original_tag_ids': [str(tag_id) for tag_id in original_tag_ids],
                        'omit_action': omit_action,
                    })

                mapping_updates = {}
                resolved_classifications = {}
                for selection_key, rows in selections.items():
                    if (
                        selection_key in omitted_keys
                        or (isinstance(selection_key, tuple) and selection_key[1] in omitted_row_indices)
                    ):
                        continue
                    edited_values = {
                        (row['category_id'], tuple(row['tag_ids']), row['toshl_description'])
                        for row in rows
                        if (
                            row['category_id'], tuple(row['tag_ids']), row['toshl_description']
                        ) != (
                            row['original_category_id'], tuple(row['original_tag_ids']),
                            row['original_toshl_description'],
                        )
                    }
                    if len(edited_values) > 1:
                        raise ValueError(
                            f'Rows for {rows[0]["description"]!r} have conflicting edits. '
                            'Use one category and tag selection for repeated descriptions.'
                        )
                    if edited_values:
                        category_id, tag_ids, toshl_description = next(iter(edited_values))
                    else:
                        original_values = {
                            (
                                row['original_category_id'], tuple(row['original_tag_ids']),
                                row['original_toshl_description'],
                            )
                            for row in rows
                        }
                        if len(original_values) > 1:
                            raise ValueError(
                                f'Rows for {rows[0]["description"]!r} have conflicting existing classifications.'
                            )
                        category_id, tag_ids, toshl_description = next(iter(original_values))
                    resolved_classifications[selection_key] = (
                        category_id, list(tag_ids), toshl_description
                    )
                    if category_id and rows[0]['normalised_key']:
                        mapping_updates[rows[0]['normalised_key']] = {
                            'description': rows[0]['description'],
                            'category_id': category_id,
                            'tag_ids': list(tag_ids),
                        }

                reviewed = transactions.copy()
                for index, transaction in transactions.iterrows():
                    draft = review_edits.get(index, {})
                    if 'amount' in draft and draft['amount'] is not None:
                        reviewed.at[index, 'amount'] = draft['amount']
                    description_key = normalise_description_key(transaction.get('normalised_description'))
                    selection_key = description_key or ('row', index)
                    if description_key in omitted_keys or index in omitted_row_indices:
                        continue
                    if selection_key not in resolved_classifications:
                        continue
                    category_id, tag_ids, toshl_description = resolved_classifications[selection_key]
                    reviewed.at[index, 'category_id'] = category_id if category_id else pd.NA
                    reviewed.at[index, 'tag_ids'] = json.dumps(tag_ids)
                    reviewed.at[index, 'toshl_description'] = toshl_description
                    reviewed.at[index, 'match_method'] = 'manual' if category_id else 'unknown'
                if omitted_keys:
                    reviewed = reviewed.loc[~keys.isin(omitted_keys)].copy()
                if omitted_row_indices:
                    reviewed = reviewed.drop(index=list(omitted_row_indices))
                if mapping_updates:
                    save_progress.progress(0.48, text='Saving category and tag mappings…')
                    mapping_counts = _save_mappings(mapping_updates, mappings_path, taxonomy)
                    st.info(
                        f'Saved mappings: {mapping_counts["added"]} added, '
                        f'{mapping_counts["changed"]} changed, '
                        f'{mapping_counts["unchanged"]} unchanged.'
                    )
                if always_omitted_keys:
                    omitted_descriptions = [
                        rows[0]['description'] for key, rows in selections.items()
                        if key in always_omitted_keys
                    ]
                    added_omissions = _save_omissions(omitted_descriptions, omissions_path)
                    st.info(f'Saved {added_omissions} new permanent omission(s).')

                unpersistable_always_count = sum(
                    1 for rows in selections.values()
                    if not rows[0]['normalised_key']
                    and rows[0]['omit_action'] == 'Omit always'
                )
                if unpersistable_always_count:
                    st.warning(
                        f'{unpersistable_always_count} row(s) marked Omit always had no normalized description. '
                        'They were omitted from this export only because they cannot be saved as reusable omissions.'
                    )

                blank_description_categories = sum(
                    1 for index, transaction in transactions.iterrows()
                    if index in reviewed.index
                    and not normalise_description_key(transaction.get('normalised_description'))
                    and pd.notna(reviewed.at[index, 'category_id'])
                )
                if blank_description_categories:
                    st.warning(
                        f'{blank_description_categories} category selection(s) were saved to the reviewed CSV, '
                        'but have no normalized description to use as a reusable mapping.'
                    )

                save_progress.progress(0.72, text='Preparing CSV downloads…')
                with tempfile.TemporaryDirectory(prefix='masher-export-') as temp_dir:
                    temp_dir = Path(temp_dir)
                    reviewed_path = temp_dir / 'reviewed.csv'
                    export_input = reviewed.copy()
                    export_input['tag_ids'] = export_input['tag_ids'].map(
                        lambda value: json.dumps(value) if isinstance(value, list) else value
                    )
                    export_input.to_csv(reviewed_path, index=False)
                    toshl_path, exported_count, skipped_count = export_to_toshl(
                        reviewed_path,
                        temp_dir / 'toshl_import.csv',
                        taxonomy_path=taxonomy_path,
                        skip_unclassified=allow_skip,
                    )
                    st.session_state['reviewed_download'] = export_input.to_csv(index=False).encode('utf-8-sig')
                    st.session_state['toshl_download'] = toshl_path.read_bytes()
                    st.session_state['export_summary'] = (exported_count, skipped_count)
                    st.session_state['saved_review_signature'] = _review_edits_signature(review_edits)
                save_progress.progress(1.0, text='Review saved and exports are ready')
                st.success('Review saved and Toshl import file prepared.')
            except Exception as error:
                if save_progress is not None:
                    save_progress.empty()
                st.error(f'Could not save or export review: {error}')

        if 'toshl_download' in st.session_state:
            st.header('Download files')
            download1, download2 = st.columns(2)
            download1.download_button(
                'Download reviewed transactions CSV',
                st.session_state['reviewed_download'],
                file_name='matched_reviewed.csv',
                mime='text/csv',
            )
            download2.download_button(
                'Download Toshl import CSV',
                st.session_state['toshl_download'],
                file_name='matched_reviewed_toshl.csv',
                mime='text/csv',
                type='primary',
            )
            exported_count, skipped_count = st.session_state['export_summary']
            st.caption(f'{exported_count} transaction(s) ready for import; {skipped_count} unclassified transaction(s) skipped.')
