#!/usr/bin/env python3
"""
Indexation incrémentale des notes du wiki dans LanceDB.

Usage:
    .venv/bin/python _RAG/rag_update.py [--theme THEME]
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Modèles HuggingFace téléchargés dans le projet (.cache/huggingface), pas dans ~/.cache
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / ".cache" / "huggingface"))

import lancedb
from sentence_transformers import SentenceTransformer

from rag_utils import (
    MODEL_NAME, PROJECT_ROOT, SCHEMA, VALID_THEMES,
    get_theme_paths, has_table, load_manifest, parse_note, save_manifest,
)


def write_log(log_dir: Path, db_dir: Path, manifest_path: Path, theme: str,
              added: list, updated: list, removed: list, total: int, duration: float) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    log_path = log_dir / f"{now.strftime('%Y-%m-%d')}-rag-update.md"

    lines = [
        f"# Log RAG update — {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Résumé",
        "",
        f"- Thème : {theme}",
        f"- Notes scannées : {total + len(removed)}",
        f"- Notes ajoutées : {len(added)}",
        f"- Notes mises à jour : {len(updated)}",
        f"- Notes supprimées : {len(removed)}",
        f"- Total indexées : {total}",
        f"- Durée : {duration:.1f}s",
        "",
    ]

    lines += ["## Détail des ajouts", ""]
    lines += [f"- {i['id']} — {i['title']}" for i in added] or ["- (aucun)"]
    lines.append("")

    lines += ["## Détail des mises à jour", ""]
    lines += [f"- {i['id']} — {i['title']}" for i in updated] or ["- (aucune)"]
    lines.append("")

    lines += ["## Détail des suppressions", ""]
    lines += [f"- {p}" for p in removed] or ["- (aucune)"]
    lines.append("")

    lines += [
        "## Paramètres",
        "",
        f"- Modèle : {MODEL_NAME}",
        f"- DB : {db_dir.relative_to(PROJECT_ROOT)}",
        f"- Manifest : {manifest_path.relative_to(PROJECT_ROOT)}",
        "",
    ]

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return log_path


def main():
    parser = argparse.ArgumentParser(description="Indexation RAG — wiki LDE_WIKI_IA")
    parser.add_argument("--theme", default="maintenance", choices=VALID_THEMES,
                        help="Thème cible (défaut : maintenance)")
    args = parser.parse_args()

    theme_root, db_dir, manifest_path, log_dir = get_theme_paths(args.theme)

    start = time.time()
    print(f"Chargement du modèle {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)

    print(f"Ouverture de la base LanceDB ({args.theme})...")
    db_dir.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_dir))

    if has_table(db, "notes"):
        table = db.open_table("notes")
    else:
        table = db.create_table("notes", schema=SCHEMA)

    manifest = load_manifest(manifest_path)
    notes_dir = theme_root / "notes"

    all_files = list(notes_dir.rglob("*.md"))
    current_paths = set()
    added = []
    updated = []
    records_to_upsert = []

    print(f"Scan de {len(all_files)} fichiers...")
    for path in all_files:
        rel = str(path.relative_to(PROJECT_ROOT))
        mtime = str(path.stat().st_mtime)
        current_paths.add(rel)

        prev = manifest.get(rel)
        if prev and prev.get("mtime") == mtime:
            continue

        note = parse_note(path)
        if note is None:
            continue

        vector = model.encode(
            f"passage: {note['text']}",
            normalize_embeddings=True
        ).tolist()

        records_to_upsert.append({**note, "vector": vector})

        if prev:
            updated.append({"id": note["id"], "title": note["title"]})
        else:
            added.append({"id": note["id"], "title": note["title"]})

        manifest[rel] = {"mtime": mtime, "id": note["id"]}

    removed_paths = [p for p in manifest if p not in current_paths]
    removed_ids = [manifest[p]["id"] for p in removed_paths]
    for p in removed_paths:
        del manifest[p]

    if records_to_upsert:
        print(f"Indexation de {len(records_to_upsert)} notes ({len(added)} nouvelles, {len(updated)} mises à jour)...")
        ids_to_delete = [r["id"] for r in records_to_upsert]
        if ids_to_delete:
            id_list = ", ".join(f"'{i}'" for i in ids_to_delete)
            try:
                table.delete(f"id IN ({id_list})")
            except Exception:
                pass
        table.add(records_to_upsert)

    if removed_ids:
        print(f"Suppression de {len(removed_ids)} notes...")
        id_list = ", ".join(f"'{i}'" for i in removed_ids)
        try:
            table.delete(f"id IN ({id_list})")
        except Exception:
            pass

    total = len(manifest)
    save_manifest(manifest, manifest_path)

    duration = time.time() - start
    log_path = write_log(log_dir, db_dir, manifest_path, args.theme,
                         added, updated, removed_paths, total, duration)

    print()
    print(f"Terminé en {duration:.1f}s")
    print(f"  Ajoutées     : {len(added)}")
    print(f"  Mises à jour : {len(updated)}")
    print(f"  Supprimées   : {len(removed_paths)}")
    print(f"  Total indexées : {total}")
    print(f"  Log : {log_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
