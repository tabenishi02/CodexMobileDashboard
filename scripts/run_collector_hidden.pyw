"""Run the existing scheduled collector without allocating a console."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--powershell", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        return subprocess.run(
            [args.powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/run_collector.ps1"),
             "-ConfigPath", args.config, "-PythonPath", args.python],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        ).returncode
    except OSError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
