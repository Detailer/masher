import pandas as pd
import argparse
from datetime import date

from .utils.cli_dates import add_date_arguments, parse_date_range

def filter_dates(df, start_date=None, end_date=None):
    """Return rows whose date falls within the optional inclusive bounds.

    Args:
        df: DataFrame with a ``date`` column containing Python date values.
        start_date: Earliest date to include, or None for no lower bound.
        end_date: Latest date to include, or None for no upper bound.

    Returns:
        A DataFrame containing the matching rows.
    """
    if start_date and end_date:
        return df[df["date"].between(start_date, end_date)]
    if start_date:
        return df[df["date"] >= start_date]
    if end_date:
        return df[df["date"] <= end_date]
    return df

def parse_hdfc_statement(input_file: str, start_date: date | None = None, end_date: date | None = None):
    """Read and normalize an HDFC text statement.

    Args:
        input_file: Path to the HDFC statement file.
        start_date: Optional inclusive lower date bound.
        end_date: Optional inclusive upper date bound.

    Returns:
        A DataFrame with date, description, amount, and type columns.
    """
    text_df = pd.read_csv(input_file)

    # drop columns
    text_df = text_df.iloc[:, [0, 1, 3]]

    # rename columns names
    text_df.columns = ['date', 'description', 'amount']

    # Remove rows with a zero amount.
    text_df = text_df[text_df.amount != 0]

    # convert date column from str to datetime
    text_df['date'] = pd.to_datetime(text_df['date'].str.strip(), format='%d/%m/%y').dt.date

    # strip blankspace
    text_df['description'] = text_df['description'].str.strip()

    # append type column
    text_df['type'] = 'bank_hdfc'

    return filter_dates(text_df, start_date, end_date)

def parse_icici_statement(input_file: str, start_date: date | None = None, end_date: date | None = None):
    """Read and normalize an ICICI spreadsheet statement.

    Args:
        input_file: Path to the ICICI statement spreadsheet.
        start_date: Optional inclusive lower date bound.
        end_date: Optional inclusive upper date bound.

    Returns:
        A DataFrame with date, description, amount, and type columns.
    """
    sheet_df = pd.read_excel(input_file, skiprows=15)

    # Keep the date, description, and amount columns; discard other columns.
    sheet_df = sheet_df.iloc[:, [1, 5, 9]]

    # drop null rows, header row with buffer
    sheet_df = sheet_df.dropna(how="all").reset_index(drop=True)

    # rename columns names
    sheet_df.columns = ['date', 'description', 'amount']

    # strip blankspace
    sheet_df['description'] = sheet_df['description'].str.strip()

    # Split the amount and transaction type (Cr./Dr.).
    sheet_df[['amount', 'type']] = sheet_df['amount'].str.split(' ', expand=True)

    # Remove credit entries.
    sheet_df = sheet_df[sheet_df.type != 'Cr.']

    # drop type column
    sheet_df = sheet_df.iloc[:, [0, 1, 2]]

    # convert date column from str to datetime
    sheet_df['date'] = pd.to_datetime(sheet_df['date'].str.strip(), format='%d-%m-%Y').dt.date

    # append type column
    sheet_df['type'] = 'credit_card_icici'

    return filter_dates(sheet_df, start_date, end_date)

def parse_statements(hdfc_input_file: str, icici_input_file: str, start_date: date | None = None, end_date: date | None = None):
    """Parse both bank statements and combine their transactions.

    Args:
        hdfc_input_file: Path to the HDFC text statement.
        icici_input_file: Path to the ICICI spreadsheet statement.
        start_date: Optional inclusive lower date bound for both statements.
        end_date: Optional inclusive upper date bound for both statements.

    Returns:
        A combined DataFrame with a fresh sequential index.
    """
    hdfc_df = parse_hdfc_statement(hdfc_input_file, start_date, end_date)
    icici_df = parse_icici_statement(icici_input_file, start_date, end_date)

    return pd.concat([hdfc_df, icici_df], ignore_index=True)

def main():
    """Parse CLI arguments, process both statements, and print the result."""
    parser = argparse.ArgumentParser()
    parser.add_argument('hdfc_input', help='HDFC statement in .txt format')
    parser.add_argument('icici_input', help='ICICI statement in .xls format')
    add_date_arguments(parser)

    args = parser.parse_args()
    start_date, end_date = parse_date_range(args, parser)

    parsed_df = parse_statements(args.hdfc_input, args.icici_input, start_date, end_date)

    print(parsed_df.to_string())

if __name__ == '__main__':
    main()
