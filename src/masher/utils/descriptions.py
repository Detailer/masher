"""Helpers for matching transaction descriptions consistently."""

import re

import pandas as pd


def description_key(value):
    """Collapse whitespace and case-fold a description for matching."""
    if pd.isna(value):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()

