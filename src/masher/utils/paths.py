"""Repository data paths used by the Masher command-line tools and UI."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / 'data'

TAXONOMY_PATH = DATA_DIR / 'toshl_categories_tags.json'
MAPPINGS_PATH = DATA_DIR / 'mappings.json'
OMISSIONS_PATH = DATA_DIR / 'omitted_descriptions.json'

