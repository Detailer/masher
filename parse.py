import pandas as pd
import argparse
from datetime import date, datetime

def filter_dates(df, start_date=None, end_date=None):
    if start_date and end_date:
        return df[df["date"].between(start_date, end_date)]
    if start_date:
        return df[df["date"] >= start_date]
    if end_date:
        return df[df["date"] <= end_date]
    return df

def parse_hdfc_statement(input_file: str, start_date: date | None = None, end_date: date | None = None):
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
    hdfc_df = parse_hdfc_statement(hdfc_input_file, start_date, end_date)
    icici_df = parse_icici_statement(icici_input_file, start_date, end_date)

    return pd.concat([hdfc_df, icici_df], ignore_index=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('hdfc_input', help='HDFC statement in .txt format')
    parser.add_argument('icici_input', help='ICICI statement in .xls format')
    parser.add_argument('--start-date', help='start date of transactions to process')
    parser.add_argument('--end-date', help='end date of transactions to process')

    args = parser.parse_args()

    start_date = (
        datetime.strptime(args.start_date, '%d/%m/%Y').date()
        if args.start_date else None
    )
    end_date = (
        datetime.strptime(args.end_date, '%d/%m/%Y').date()
        if args.end_date else None
    )

    if start_date and end_date and start_date > end_date:
        parser.error('--start-date must be on or before --end-date')

    parsed_df = parse_statements(args.hdfc_input, args.icici_input, start_date, end_date)

    print(parsed_df.to_string())

if __name__ == '__main__':
    main()