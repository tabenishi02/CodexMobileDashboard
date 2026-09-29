"""Run scheduled Snapshot retention without opening a console window."""
import argparse
from pathlib import Path
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        return subprocess.run(
            [args.python, "-m", "tools.snapshot_retention_coordinator", "--config", args.config],
            cwd=Path(__file__).resolve().parent.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        ).returncode
    except OSError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())