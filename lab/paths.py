"""Where the Lab reads its configuration and inputs. Paths come from environment variables and
default to the files shipped with the repo (fictional demo data):

    LAB_CONFIG_DIR  config/lab/            config.json, blocks.json, scenarios.json, macro_rules.json
    LEDGER_CSV      sample_data/ledger.csv operations ledger (clean CSV, see lab/ledger.py)
    PORTFOLIO_CSV   sample_data/portfolio.csv  current holdings, used to reconcile the ledger

Runtime caches (downloaded prices, decision journal) live in data/lab/, which is git-ignored."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.environ.get('LAB_CONFIG_DIR') or os.path.join(ROOT, 'config', 'lab')
DATA_DIR = os.path.join(ROOT, 'data', 'lab')
LEDGER_CSV = os.environ.get('LEDGER_CSV') or os.path.join(ROOT, 'sample_data', 'ledger.csv')
PORTFOLIO_CSV = os.environ.get('PORTFOLIO_CSV') or os.path.join(ROOT, 'sample_data', 'portfolio.csv')


def config_file(name):
    return os.path.join(CONFIG_DIR, name)
