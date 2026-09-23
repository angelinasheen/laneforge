"""`python -m laneforge.web`: development server on port 8000."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

PORT = 8000


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    from laneforge.web.app import create_app

    app = create_app()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", PORT)),
            debug=os.environ.get("FLASK_DEBUG") == "1")


if __name__ == "__main__":
    main()
