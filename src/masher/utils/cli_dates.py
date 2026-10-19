"""Shared parsing for optional date-range CLI arguments."""

import argparse
from datetime import datetime


def add_date_arguments(parser):
    """Add the common inclusive start and end date options to a parser."""
    parser.add_argument('--start-date', help='start date in DD/MM/YYYY format')
    parser.add_argument('--end-date', help='end date in DD/MM/YYYY format')


def parse_date_range(args, parser):
    """Parse and validate date options, reporting errors through argparse."""
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

    return start_date, end_date

