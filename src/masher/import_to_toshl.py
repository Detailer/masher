"""Upload Masher's Toshl CSV through Toshl's bulk import API."""

import argparse
import base64
import csv
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

_API_BASE = 'https://api.toshl.com'
_ALLOWED_API_HOSTS = {'api.toshl.com', 'api2.toshl.com'}
_DATE_FORMAT = 'd/M/y'
_COLUMNS = [
    'Date', 'Category', 'Expense Amount', 'Tags',
    'Description', 'Account', 'Currency',
]
_COLUMN_HINTS = {
    'Date': 'date',
    'Category': 'category',
    'Expense Amount': 'expense',
    'Tags': 'tags',
    'Description': 'description',
    'Account': 'account',
    'Currency': 'currency',
}


class ToshlAPIError(RuntimeError):
    """An API request failed or returned an unusable response."""


def _read_export(csv_path):
    """Read and validate an exported Toshl CSV, removing only its UTF-8 BOM."""
    csv_path = Path(csv_path)
    contents = csv_path.read_bytes()
    if contents.startswith(b'\xef\xbb\xbf'):
        contents = contents[3:]

    try:
        text = contents.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'{csv_path} is not a UTF-8 CSV file') from error

    rows = list(csv.reader(io.StringIO(text, newline='')))
    if not rows or rows[0] != _COLUMNS:
        raise ValueError(
            'Input must use the CSV layout produced by masher.export_to_toshl: '
            + ', '.join(_COLUMNS)
        )
    transaction_rows = [
        row for row in rows[1:] if row and any(value.strip() for value in row)
    ]
    malformed_rows = [
        number for number, row in enumerate(transaction_rows, start=2)
        if len(row) != len(_COLUMNS)
    ]
    if malformed_rows:
        row_list = ', '.join(map(str, malformed_rows))
        raise ValueError(f'CSV row(s) {row_list} do not have {len(_COLUMNS)} fields.')
    transaction_count = len(transaction_rows)
    if transaction_count == 0:
        raise ValueError('The Toshl CSV has no transaction rows to upload.')
    return contents, transaction_count


def _authorization(token):
    credentials = base64.b64encode(f'{token}:'.encode('utf-8')).decode('ascii')
    return f'Basic {credentials}'


def _request_json(method, url, token, *, body=None, content_type=None):
    headers = {
        'Authorization': _authorization(token),
        'Accept': 'application/json',
    }
    if content_type:
        headers['Content-Type'] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read()
            response_headers = response.headers
    except HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        raise ToshlAPIError(
            f'Toshl returned HTTP {error.code} for {method} {urlparse(url).path}: {detail}'
        ) from None
    except URLError as error:
        raise ToshlAPIError(f'Could not reach Toshl: {error.reason}') from None

    if not response_body:
        return {}, response_headers
    try:
        return json.loads(response_body), response_headers
    except json.JSONDecodeError as error:
        raise ToshlAPIError('Toshl returned an invalid JSON response.') from error


def _safe_import_url(location):
    import_url = urljoin(f'{_API_BASE}/', location)
    parsed = urlparse(import_url)
    if parsed.scheme != 'https' or parsed.hostname not in _ALLOWED_API_HOSTS:
        raise ToshlAPIError('Toshl returned an unexpected import URL; refusing to follow it.')
    return import_url


def _make_multipart(csv_bytes, account_id):
    boundary = '----MasherToshlImportBoundary'
    body = (
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="account"\r\n\r\n'
        f'{account_id}\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="import"; filename="masher-toshl-import.csv"\r\n'
        'Content-Type: text/csv\r\n\r\n'
    ).encode('utf-8')
    body += csv_bytes + f'\r\n--{boundary}--\r\n'.encode('ascii')
    return body, f'multipart/form-data; boundary={boundary}'


def _configured_preview(import_data):
    """Use the known Masher column meanings instead of relying on guesses."""
    previews = import_data.get('preview') or []
    configured = []
    for preview in previews:
        headers = [str(value).lstrip('\ufeff').strip() for value in preview.get('header', [])]
        if headers != _COLUMNS:
            raise ToshlAPIError(
                f'Toshl preview headers did not match the Masher CSV layout: {headers!r}'
            )
        configured.append({
            'header': headers,
            'hints': [_COLUMN_HINTS[column] for column in headers],
        })
    if not configured:
        raise ToshlAPIError('Toshl returned no CSV column preview to configure.')
    return configured


def import_to_toshl(csv_path, account_id=None, token=None):
    """Upload an exported Toshl CSV and configure its bulk import.

    The API creates real entries in the selected Toshl account. The CSV itself
    is left untouched; only its BOM-free bytes are sent to Toshl so the Date
    header is detected correctly.
    """
    token = (token or os.environ.get('TOSHL_API_TOKEN', '')).strip()
    if not token:
        raise ValueError('TOSHL_API_TOKEN is missing; run with --env-file .env.')
    account_id = str(account_id or os.environ.get('TOSHL_ACCOUNT_ID', '')).strip()
    if not account_id:
        raise ValueError('TOSHL_ACCOUNT_ID is missing; set it in .env or pass --account-id.')

    csv_bytes, transaction_count = _read_export(csv_path)
    body, content_type = _make_multipart(csv_bytes, account_id)
    _, headers = _request_json(
        'POST', f'{_API_BASE}/bank/imports', token,
        body=body, content_type=content_type,
    )
    location = headers.get('Location')
    if not location:
        raise ToshlAPIError(
            'Toshl accepted the upload but returned no import URL. Check Toshl before retrying.'
        )

    import_url = _safe_import_url(location)
    import_id = urlparse(import_url).path.rstrip('/').split('/')[-1]
    try:
        import_data, _ = _request_json('GET', import_url, token)
        payload = {
            'account': account_id,
            'preview': _configured_preview(import_data),
            'formats': {'selected': _DATE_FORMAT},
        }
        result, _ = _request_json(
            'PUT', import_url, token,
            body=json.dumps(payload).encode('utf-8'),
            content_type='application/json',
        )
    except (ToshlAPIError, ValueError) as error:
        raise ToshlAPIError(
            f'Uploaded import {import_id}, but could not finish configuring it: {error}. '
            'Check this import in Toshl before retrying.'
        ) from error

    return {
        'import_id': str(result.get('id', import_id)),
        'status': result.get('status', 'unknown'),
        'transaction_count': transaction_count,
        'metadata': result.get('metadata'),
    }


def main():
    parser = argparse.ArgumentParser(
        description='Upload a Masher Toshl CSV through Toshl’s bulk import API.'
    )
    parser.add_argument('input_csv', help='CSV produced by masher.export_to_toshl')
    parser.add_argument(
        '--account-id', default=os.environ.get('TOSHL_ACCOUNT_ID', ''),
        help='destination Toshl account ID (defaults to TOSHL_ACCOUNT_ID)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='validate and count rows without uploading them',
    )
    parser.add_argument(
        '--confirm', action='store_true',
        help='confirm creation of entries in the selected Toshl account',
    )
    args = parser.parse_args()

    try:
        if args.dry_run:
            _, transaction_count = _read_export(args.input_csv)
            print(f'Validated {transaction_count} Toshl transaction row(s); nothing uploaded.')
            return
        if not args.confirm:
            parser.error('uploading creates Toshl entries; add --confirm to proceed')
        result = import_to_toshl(args.input_csv, args.account_id)
    except (OSError, ValueError, ToshlAPIError) as error:
        parser.error(str(error))

    print(
        f"Toshl import {result['import_id']} submitted: "
        f"{result['transaction_count']} row(s), status={result['status']}."
    )
    if result['status'] not in {'new', 'completed'}:
        print('Check the import in Toshl before retrying; the upload may already exist.')


if __name__ == '__main__':
    main()
