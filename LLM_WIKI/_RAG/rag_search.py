#!/usr/bin/env python3
"""
Recherche sémantique dans la base RAG du wiki.

Usage:
    .venv/bin/python _RAG/rag_search.py [--theme THEME] {all|<type>} "texte de requête" [--top-k N] [--no-rerank] [--solo-rag]

Types acceptés (préfixe court ou nom complet) :
    ato / atomic_note           glo / glossary_term
    nor / norm_reference        exn / normative_requirement
    pro / business_process      obj / domain_object
    brl / business_rule         reg / regulatory_text
    hab / habilitation          ind / indicator
    moc / moc
"""

import argparse
import os
from pathlib import Path
import sys

# Modèles HuggingFace téléchargés dans le projet (.cache/huggingface), pas dans ~/.cache
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / ".cache" / "huggingface"))

import lancedb
from sentence_transformers import SentenceTransformer, CrossEncoder

from rag_utils import MODEL_NAME, TYPE_MAP, VALID_THEMES, get_theme_paths, has_table, resolve_type

RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANKER_CANDIDATES = 50  # nombre de candidats ANN soumis au re-ranking


def main():
    parser = argparse.ArgumentParser(description="Recherche RAG — wiki LDE_WIKI_IA")
    parser.add_argument("--theme", default="maintenance", choices=VALID_THEMES,
                        help="Thème cible (défaut : maintenance)")
    parser.add_argument("type_filter", help="Filtre de type : all, ato, glo, nor, exn, pro, obj, brl, reg, hab, ind, moc")
    parser.add_argument("query", help="Texte de la requête")
    parser.add_argument("--top-k", type=int, default=10, help="Nombre de résultats (défaut : 10)")
    parser.add_argument("--no-rerank", action="store_true", help="Désactiver le re-ranking cross-encoder")
    parser.add_argument("--solo-rag", action="store_true",
                        help="Marqueur d'appel RAG isolé volontaire (sans effet ; neutralise le rappel du hook warn-rag-solo)")
    args = parser.parse_args()

    resolved_type = resolve_type(args.type_filter)
    if resolved_type is None:
        valid = ", ".join(sorted(list(TYPE_MAP.keys()) + ["all"]))
        print(f"Type inconnu : '{args.type_filter}'. Types valides : {valid}", file=sys.stderr)
        sys.exit(1)

    _, db_dir, _, _ = get_theme_paths(args.theme)

    if not db_dir.exists():
        print(f"La base RAG pour le thème '{args.theme}' n'existe pas encore. Lancez d'abord : rag_update.py --theme {args.theme}", file=sys.stderr)
        sys.exit(1)

    print(f"Chargement du modèle {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)

    use_rerank = not args.no_rerank
    if use_rerank:
        print(f"Chargement du re-ranker {RERANKER_MODEL}...")
        reranker = CrossEncoder(RERANKER_MODEL)

    db = lancedb.connect(str(db_dir))
    if not has_table(db, "notes"):
        print(f"La table 'notes' est vide pour le thème '{args.theme}'. Lancez d'abord : rag_update.py --theme {args.theme}", file=sys.stderr)
        sys.exit(1)

    table = db.open_table("notes")

    query_vector = model.encode(
        f"query: {args.query}",
        normalize_embeddings=True
    ).tolist()

    # Passe 1 : ANN — récupérer plus de candidats si re-ranking activé
    ann_limit = max(args.top_k, RERANKER_CANDIDATES) if use_rerank else args.top_k
    search = table.search(query_vector).limit(ann_limit)
    if resolved_type != "all":
        search = search.where(f"type = '{resolved_type}'", prefilter=True)

    results = search.to_list()

    # Passe 2 : re-ranking cross-encoder
    if use_rerank and results:
        pairs = [(args.query, row.get("text", "")) for row in results]
        scores = reranker.predict(pairs)
        for row, score in zip(results, scores):
            row["_rerank_score"] = float(score)
        results.sort(key=lambda r: r["_rerank_score"], reverse=True)
        results = results[:args.top_k]

    print()
    rerank_label = "" if use_rerank else " — ANN seul"
    print(f"## Résultats — \"{args.query}\" (thème: {args.theme}, type: {args.type_filter}{rerank_label})")
    print()

    if not results:
        print("Aucun résultat.")
        return

    col_type = 22
    col_score = 7
    col_title = 55

    header = f"| {'#':>2} | {'ID':<10} | {'Type':<{col_type}} | {'Score':>{col_score}} | {'Titre':<{col_title}} |"
    sep    = f"|{'-'*4}|{'-'*12}|{'-'*(col_type+2)}|{'-'*(col_score+2)}|{'-'*(col_title+2)}|"

    print(header)
    print(sep)

    for i, row in enumerate(results, 1):
        if "_rerank_score" in row:
            score = row["_rerank_score"]
            score_str = f"{score:>{col_score}.3f}"
        else:
            score = max(0.0, 1.0 - row.get("_distance", 0.0))
            score_str = f"{score:>{col_score}.2f} "
        note_id = row.get("id", "")
        note_type = row.get("type", "")[:col_type]
        title = row.get("title", "")[:col_title]
        file_path = row.get("file_path", "")
        id_link = f"[{note_id}]({file_path})"
        print(f"| {i:>2} | {id_link:<10} | {note_type:<{col_type}} | {score_str} | {title:<{col_title}} |")



if __name__ == "__main__":
    main()
