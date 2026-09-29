"""Project paths, prompts, configuration, and small file helpers."""
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "clean-6-structured-issues"
CRITERIA = ("semantic_fidelity", "technical_accuracy", "spanish_quality", "structure_preservation")
STAGES = ("examine", "translate")
OUTPUT_LIMIT = {"examine": 6500, "translate": 3000}
PROMPTS = {stage: (ROOT / "prompts" / f"{stage}.md").read_text(encoding="utf-8")
           for stage in STAGES}


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_config(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg["documents"] or not cfg["models"] or len(set(cfg["models"])) != len(cfg["models"]):
        raise ValueError("Configure at least one document and unique model identifiers")
    if not isinstance(cfg["random_seed"], int):
        raise ValueError("random_seed must be an integer")
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", k) for k in cfg["documents"]):
        raise ValueError("Document IDs may contain only letters, numbers, _ and -")
    return cfg


