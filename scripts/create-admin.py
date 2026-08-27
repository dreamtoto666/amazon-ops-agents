"""Create the first Amazon Ops administrator without relying on editable installs."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amazon_ops.auth import main


if __name__ == "__main__":
    main()
