"""Generate the France-only dashboard from an existing experiment (zero API calls)."""
import argparse
from pathlib import Path
from scripts.dashboard import build

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run", type=Path, help="Existing output/runs/<run_id> directory")
    cli.add_argument("--reuse-from", type=Path,
                     help="Include historical results in the current reviewer dashboard without new API requests")
    args = cli.parse_args()
    build(args.run.resolve(), args.reuse_from)
