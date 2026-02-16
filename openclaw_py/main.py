import sys
import os

# Add the parent directory to sys.path so we can import openclaw_py
# This assumes main.py is run from the project root or we adjust path accordingly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from openclaw_py.src.cli import app
from openclaw_py.src.config.config import load_config

def main():
    # Load configuration on startup
    config = load_config()

    # In a real app, we might pass config to the app context or use a global singleton/dependency injection
    # For now, just loading it to ensure it works.

    app()

if __name__ == "__main__":
    main()
