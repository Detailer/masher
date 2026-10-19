import argparse
from datetime import datetime
from .parse import parse_statements
from .utils.cli_dates import add_date_arguments, parse_date_range

def normalise_description(parsed_df):
    """Return a copy with descriptions simplified by statement type."""
    result = parsed_df.copy()
    descriptions = result['description'].fillna('').astype(str).str.strip()

    # HDFC UPI descriptions have the payee between the first two hyphens.
    hdfc = result['type'].eq('bank_hdfc')
    result['normalised_description'] = descriptions
    result.loc[hdfc, 'normalised_description'] = (
        descriptions[hdfc].str.extract(r'^[^-]*-([^-]*)-', expand=False)
        .fillna(descriptions[hdfc])
        .str.strip()
    )

    # ICICI merchant descriptions end with a space, hyphen, and location.
    icici = result['type'].eq('credit_card_icici')
    result.loc[icici, 'normalised_description'] = (
        descriptions[icici].str.replace(r'\s+-.*$', '', regex=True).str.strip()
    )

    return result

def export_to_csv(normalised_df, filepath=None):
    """Export normalized transactions to a CSV file.

    If filepath is omitted, create a timestamped filename in the current
    directory. Returns the path of the created CSV file.
    """
    if filepath:
        filename = filepath
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{timestamp}_normalised_statement.csv'
    columns = ['date', 'description', 'normalised_description', 'amount', 'type']
    normalised_df.to_csv(filename, columns=columns, index=False)
    return filename


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('hdfc_input', help='HDFC statement in .txt format')
    parser.add_argument('icici_input', help='ICICI statement in .xls format')
    add_date_arguments(parser)
    parser.add_argument(
        '--export-to-csv',
        nargs='?',
        const='',
        default=None,
        metavar='FILEPATH',
        help='export to CSV, optionally specifying the output filepath',
    )

    args = parser.parse_args()

    start_date, end_date = parse_date_range(args, parser)

    parsed_df = parse_statements(args.hdfc_input, args.icici_input, start_date, end_date)
    normalised_df = normalise_description(parsed_df)
    print(normalised_df.to_string(index=False))

    if args.export_to_csv is not None:
        filename = export_to_csv(normalised_df, args.export_to_csv)
        print(f'Exported CSV: {filename}')


if __name__ == '__main__':
    main()
