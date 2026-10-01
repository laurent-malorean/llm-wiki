#!/usr/bin/env python3
"""
Constantes et utilitaires partagés entre rag_update.py et rag_search.py.
"""

import json
from pathlib import Path
from typing import Optional

import frontmatter
import pyarrow as pa

# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent          # _RAG/ → project root
WIKI_ROOT = PROJECT_ROOT / "wiki"
RAG_ROOT = SCRIPT_DIR / "wiki"           # _RAG/wiki/

VALID_THEMES = json.loads((PROJECT_ROOT / "wiki" / "themes.json").read_text())

# Valeurs par défaut pour le thème maintenance
DB_DIR = RAG_ROOT / "maintenance" / "index.lance"
MANIFEST_PATH = RAG_ROOT / "maintenance" / "manifest.json"
LOG_DIR = PROJECT_ROOT / "_LOGS" / "wiki" / "maintenance" / "rag"


def has_table(db, name: str) -> bool:
    """Indique si la table existe (compatible avec toutes les versions de lancedb)."""
    try:
        db.open_table(name)
        return True
    except Exception:
        return False


def get_theme_paths(theme: str = "maintenance"):
    """Retourne (theme_root, db_dir, manifest_path, log_dir) pour un thème donné."""
    theme_root = WIKI_ROOT / "themes" / theme
    db_dir = RAG_ROOT / theme / "index.lance"
    manifest_path = RAG_ROOT / theme / "manifest.json"
    log_dir = PROJECT_ROOT / "_LOGS" / "wiki" / theme / "rag"
    return theme_root, db_dir, manifest_path, log_dir


# ---------------------------------------------------------------------------
# Mapping préfixe court → type frontmatter
# ---------------------------------------------------------------------------

TYPE_MAP = {
    "ato": "atomic_note",
    "glo": "glossary_term",
    "nor": "norm_reference",
    "exn": "normative_requirement",
    "pro": "business_process",
    "obj": "domain_object",
    "brl": "business_rule",
    "reg": "regulatory_text",
    "hab": "habilitation",
    "ind": "indicator",
    "moc": "moc",
}

VALID_TYPES = set(TYPE_MAP.values())

MODEL_NAME = "intfloat/multilingual-e5-large"

# ---------------------------------------------------------------------------
# Schéma LanceDB
# ---------------------------------------------------------------------------

SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("uid", pa.string()),
    pa.field("type", pa.string()),
    pa.field("title", pa.string()),
    pa.field("status", pa.string()),
    pa.field("file_path", pa.string()),
    pa.field("source_ids", pa.string()),   # JSON array sérialisé
    pa.field("domain", pa.string()),       # JSON array sérialisé
    pa.field("text", pa.string()),
    pa.field("text_title", pa.string()),   # titre seul pour re-ranking
    pa.field("text_body", pa.string()),    # corps seul pour re-ranking
    pa.field("vector", pa.list_(pa.float32(), 1024)),  # e5-large = 1024 dims
])

# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def resolve_type(arg: str) -> Optional[str]:
    """Résout un préfixe court ou un nom complet en type frontmatter."""
    arg = arg.lower()
    if arg == "all":
        return "all"
    if arg in TYPE_MAP:
        return TYPE_MAP[arg]
    if arg in VALID_TYPES:
        return arg
    return None


def parse_note(path: Path) -> Optional[dict]:
    """Parse une note Markdown avec frontmatter YAML. Retourne None si invalide."""
    try:
        post = frontmatter.load(str(path))
    except Exception:
        return None

    meta = post.metadata
    body = post.content.strip()

    note_id = meta.get("id", "")
    uid = meta.get("uid", "")
    note_type = meta.get("type", "")
    status = meta.get("status", "draft")

    # Titre : priorité au frontmatter, sinon premier H1
    title = meta.get("title", "") or meta.get("name", "") or meta.get("term", "")
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

    if not note_id or not note_type:
        return None

    source_ids = meta.get("source_ids", [])
    domain = meta.get("domain", [])

    # text_title : ce qui identifie la note (id + titre + type)
    text_title = f"{note_id} {title} [{note_type}]"
    # text_body : corps complet de la note
    text_body = body
    # embed_text : combinaison pour l'embedding — titre pondéré en tête
    embed_text = f"{text_title}\n\n{text_body}"

    return {
        "id": note_id,
        "uid": uid,
        "type": note_type,
        "title": title,
        "status": status,
        "file_path": str(path.relative_to(PROJECT_ROOT)),
        "source_ids": json.dumps(source_ids if isinstance(source_ids, list) else [source_ids]),
        "domain": json.dumps(domain if isinstance(domain, list) else [domain]),
        "text": embed_text,
        "text_title": text_title,
        "text_body": text_body,
    }


def load_manifest(manifest_path: Path = None) -> dict:
    mp = manifest_path or MANIFEST_PATH
    if mp.exists():
        with open(mp, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict, manifest_path: Path = None) -> None:
    mp = manifest_path or MANIFEST_PATH
    mp.parent.mkdir(parents=True, exist_ok=True)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
