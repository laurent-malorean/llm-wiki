#!/usr/bin/env python3
"""
API d'exploration du wiki — LDE_WIKI_IA.

Répond à la question : « pour un sujet donné, donne-moi l'ensemble fini des
notes correspondantes ».

Pipeline (par requête) :
    1. reformulation LLM optionnelle (CLI `claude` en subprocess, fallback brut)
    2. recherche sémantique RAG (LanceDB + e5-large + re-ranking cross-encoder)
    3. ancrage : les meilleurs résultats RAG deviennent les nœuds pivots
    4. clôture : traversée bornée (BFS distance N) du graphe autour des pivots
    5. sortie JSON : ensemble fini de notes, déterministe

Un seul thème par requête (?theme=). Modèles chargés une fois au boot.

Lancement :
    .venv/bin/python _API/wiki_api.py            # port 8090
    .venv/bin/python _API/wiki_api.py --port 9000

Endpoints :
    GET  /health
    GET  /themes
    GET  /search?theme=marketing&q=positionnement produit SaaS
         &top_k=10&depth=1&rephrase=true&max_nodes=60
    POST /search   (mêmes paramètres en JSON)
"""

import argparse
import os
import json
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

# Modèles HuggingFace téléchargés dans le projet (.cache/huggingface), pas dans ~/.cache
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / ".cache" / "huggingface"))

import frontmatter
import lancedb
from flask import Flask, Response, jsonify, request
from sentence_transformers import CrossEncoder, SentenceTransformer

# rag_utils vit dans _RAG/ — on l'ajoute au path pour réutiliser ses constantes
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "_RAG"))
from rag_utils import MODEL_NAME, VALID_THEMES, get_theme_paths, has_table  # noqa: E402

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANKER_CANDIDATES = 50
GRAPH_DIR = PROJECT_ROOT / "_GRAPHITY"

DEFAULT_TOP_K = 10        # nb de pivots RAG conservés après re-ranking
DEFAULT_DEPTH = 1         # distance de traversée graphe autour des pivots
DEFAULT_MAX_NODES = 80    # plafond de sécurité sur l'ensemble renvoyé
CLAUDE_MODEL = "claude-haiku-4-5"
CLAUDE_TIMEOUT = 30       # secondes

INCLUDE_LEVELS = ("meta", "excerpt", "full")  # niveaux de détail de /search
EXCERPT_MAX_CHARS = 400   # longueur max d'un extrait
MAX_BATCH_IDS = 50        # plafond de sécurité sur /notes (batch fetch)

# [[ID-slug]] ou [[ID]] — capture uniquement l'ID (ex: GLO00643, ATO01534)
WIKI_LINK_RE = re.compile(r"\[\[([A-Z]{3}\d{5})(?:-[^\]]*)?\]\]")

# ---------------------------------------------------------------------------
# Modèles chargés une seule fois (au boot)
# ---------------------------------------------------------------------------
print(f"[boot] chargement du modèle d'embedding {MODEL_NAME}...", flush=True)
EMBED_MODEL = SentenceTransformer(MODEL_NAME)
print(f"[boot] chargement du re-ranker {RERANKER_MODEL}...", flush=True)
RERANKER = CrossEncoder(RERANKER_MODEL)
print("[boot] modèles prêts.", flush=True)

# Caches par thème (chargés à la demande, gardés en mémoire)
_TABLE_CACHE = {}   # theme -> lancedb Table
_GRAPH_CACHE = {}   # theme -> dict indexé (voir load_graph)

app = Flask(__name__)


@app.after_request
def add_cors(resp):
    """CORS permissif — usage local ; à restreindre si exposé à un tiers."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ---------------------------------------------------------------------------
# Chargement LanceDB / graphe (paresseux, mis en cache)
# ---------------------------------------------------------------------------
def get_table(theme):
    if theme in _TABLE_CACHE:
        return _TABLE_CACHE[theme]
    _, db_dir, _, _ = get_theme_paths(theme)
    if not db_dir.exists():
        raise FileNotFoundError(
            f"Base RAG absente pour le thème '{theme}'. "
            f"Lancer d'abord : rag_update.py --theme {theme}"
        )
    db = lancedb.connect(str(db_dir))
    if not has_table(db, "notes"):
        raise FileNotFoundError(f"Table 'notes' vide pour le thème '{theme}'.")
    table = db.open_table("notes")
    _TABLE_CACHE[theme] = table
    return table


def load_graph(theme):
    """Charge graph.json et l'indexe pour une traversée rapide.

    Retourne un dict :
        wiki_to_node : {wiki_id -> node_id}        (ex: GLO00677 -> GLO00677-slug)
        nodes        : {node_id -> node dict}
        adjacency    : {node_id -> [(voisin_id, relation), ...]}  (non orienté)
    Retourne None si le graphe n'existe pas pour ce thème.
    """
    if theme in _GRAPH_CACHE:
        return _GRAPH_CACHE[theme]
    graph_path = GRAPH_DIR / theme / "graph.json"
    if not graph_path.exists():
        _GRAPH_CACHE[theme] = None
        return None
    data = json.loads(graph_path.read_text(encoding="utf-8"))

    nodes = {}
    wiki_to_node = {}
    for n in data.get("nodes", []):
        nid = n.get("id")
        if not nid:
            continue
        nodes[nid] = n
        wid = n.get("wiki_id")
        if wid:
            wiki_to_node[wid] = nid

    adjacency = {nid: [] for nid in nodes}
    for link in data.get("links", []):
        s, t = link.get("source"), link.get("target")
        rel = link.get("relation", "")
        if s not in adjacency or t not in adjacency:
            continue
        # graphe orienté à l'origine, mais pour « tout ce qui gravite autour »
        # on traverse dans les deux sens.
        adjacency[s].append((t, rel))
        adjacency[t].append((s, rel))

    indexed = {"wiki_to_node": wiki_to_node, "nodes": nodes, "adjacency": adjacency}
    _GRAPH_CACHE[theme] = indexed
    return indexed


# ---------------------------------------------------------------------------
# Étape 1 — reformulation LLM via la CLI claude (fallback sur la question brute)
# ---------------------------------------------------------------------------
def rephrase_query(question, theme):
    """Reformule la question en une requête sémantiquement riche via `claude -p`.

    Retourne (query_used, rephrased_bool). En cas d'échec (pas de CLI, timeout,
    JSON invalide), retourne (question, False) — l'API ne plante jamais.
    """
    prompt = (
        f"Tu aides à interroger un wiki sur le thème « {theme} ». "
        f"Reformule la question ci-dessous en UNE requête de recherche sémantique "
        f"dense et riche (synonymes, concepts sous-jacents, termes du domaine), "
        f"sur une seule ligne. Réponds UNIQUEMENT en JSON strict, sans texte autour :\n"
        f'{{"query": "..."}}\n\n'
        f"Question : {question}"
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", CLAUDE_MODEL,
             "--output-format", "json"],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return question, False

    if proc.returncode != 0:
        return question, False

    # La CLI enveloppe la réponse : {"result": "<texte du modèle>", ...}
    try:
        envelope = json.loads(proc.stdout)
        inner = envelope.get("result", "") if isinstance(envelope, dict) else ""
    except json.JSONDecodeError:
        inner = proc.stdout

    inner = inner.strip()
    # Le modèle peut entourer son JSON de ``` — on tente d'extraire l'objet.
    start, end = inner.find("{"), inner.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(inner[start:end + 1])
            q = parsed.get("query", "").strip()
            if q:
                return q, True
        except json.JSONDecodeError:
            pass
    return question, False


# ---------------------------------------------------------------------------
# Étape 2-3 — recherche RAG + re-ranking → pivots
# ---------------------------------------------------------------------------
def rag_search(table, query, top_k):
    """Recherche vectorielle + re-ranking cross-encoder. Retourne une liste de dicts."""
    vec = EMBED_MODEL.encode(f"query: {query}", normalize_embeddings=True).tolist()
    ann_limit = max(top_k, RERANKER_CANDIDATES)
    candidates = table.search(vec).limit(ann_limit).to_list()
    if not candidates:
        return []
    pairs = [(query, row.get("text", "")) for row in candidates]
    scores = RERANKER.predict(pairs)
    for row, sc in zip(candidates, scores):
        row["_rerank_score"] = float(sc)
    candidates.sort(key=lambda r: r["_rerank_score"], reverse=True)
    return candidates[:top_k]


# ---------------------------------------------------------------------------
# Étape 4 — clôture : traversée graphe bornée (BFS) autour des pivots
# ---------------------------------------------------------------------------
def graph_closure(graph, pivot_wiki_ids, depth, max_nodes):
    """BFS borné à partir des pivots. Retourne un dict {node_id -> distance}.

    - depth = 0 → uniquement les pivots eux-mêmes
    - depth = 1 → pivots + voisins directs
    - depth = N → jusqu'à N sauts
    Le parcours s'arrête à max_nodes (plafond de sécurité).
    """
    wiki_to_node = graph["wiki_to_node"]
    adjacency = graph["adjacency"]

    seeds = [wiki_to_node[w] for w in pivot_wiki_ids if w in wiki_to_node]
    dist = {nid: 0 for nid in seeds}
    queue = deque(seeds)

    while queue and len(dist) < max_nodes:
        cur = queue.popleft()
        d = dist[cur]
        if d >= depth:
            continue
        for neighbor, _rel in adjacency.get(cur, []):
            if neighbor not in dist:
                dist[neighbor] = d + 1
                queue.append(neighbor)
                if len(dist) >= max_nodes:
                    break
    return dist


# ---------------------------------------------------------------------------
# Lecture du contenu d'une note (fichier .md — source de vérité)
# ---------------------------------------------------------------------------
def read_note(theme, wiki_id):
    """Lit une note par son ID (ex: GLO00643) dans le thème donné.

    Retourne un dict {id, uid, theme, type, title, status, path, frontmatter,
    body, linked_ids} ou None si introuvable. Le corps est du Markdown brut ;
    le frontmatter est parsé en JSON. linked_ids liste les ID `[[ID-slug]]`
    référencés dans le corps, dans l'ordre d'apparition. Le fichier .md est la
    source de vérité (pas LanceDB).
    """
    theme_root, _, _, _ = get_theme_paths(theme)
    notes_dir = theme_root / "notes"
    if not notes_dir.exists():
        return None
    # les fichiers sont nommés ID-slug.md ; on cherche le préfixe ID-
    matches = list(notes_dir.rglob(f"{wiki_id}-*.md")) + list(notes_dir.rglob(f"{wiki_id}.md"))
    if not matches:
        return None
    path = matches[0]
    try:
        post = frontmatter.load(str(path))
    except Exception:
        return None
    meta = dict(post.metadata)
    body = post.content.strip()
    title = (meta.get("title") or meta.get("name") or meta.get("term") or "")
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    return {
        "id": meta.get("id", wiki_id),
        "uid": meta.get("uid", ""),
        "theme": theme,
        "type": meta.get("type", ""),
        "title": title,
        "status": meta.get("status", ""),
        "path": str(path.relative_to(PROJECT_ROOT)),
        "frontmatter": meta,
        "body": body,
        "linked_ids": extract_linked_ids(body),
    }


def extract_linked_ids(body):
    """Extrait, dans l'ordre d'apparition et sans doublon, les ID `[[ID-slug]]` du corps."""
    seen = []
    for match in WIKI_LINK_RE.finditer(body):
        wid = match.group(1)
        if wid not in seen:
            seen.append(wid)
    return seen


def make_excerpt(body):
    """Extrait le premier paragraphe de contenu (après le H1 et les sous-titres).

    Renvoie une chaîne courte (<= EXCERPT_MAX_CHARS) : typiquement l'idée
    principale / la définition courte de la note.
    """
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if line.startswith(("- ", "* ", "|")):
            continue
        if len(line) > EXCERPT_MAX_CHARS:
            return line[:EXCERPT_MAX_CHARS].rstrip() + "…"
        return line
    return ""


# ---------------------------------------------------------------------------
# Assemblage de la réponse
# ---------------------------------------------------------------------------
CATEGORY_MAP = {
    "glossary_term": "glossaire",
    "atomic_note": "atomique",
    "moc": "navigation",
    "business_process": "processus",
    "indicator": "indicateur",
}


def build_results(theme, table, rag_pivots, graph, closure_dist):
    """Fusionne pivots RAG et nœuds du graphe en une liste unique d'entrées.

    Chaque entrée : id, type, title, path, rag_score, graph_distance, sources.
    """
    by_id = {}

    # a) pivots RAG (on connaît leur score et leurs métadonnées directement)
    for row in rag_pivots:
        wid = row.get("id")
        by_id[wid] = {
            "id": wid,
            "theme": theme,
            "type": row.get("type", ""),
            "title": row.get("title", ""),
            "path": row.get("file_path", ""),
            "rag_score": round(row.get("_rerank_score", 0.0), 3),
            "graph_distance": 0 if graph else None,
            "sources": ["rag"],
        }

    # b) nœuds ramassés par la traversée graphe
    if graph:
        node_by_id = graph["nodes"]
        # index inverse node_id -> wiki_id via les nodes
        for node_id, d in closure_dist.items():
            node = node_by_id.get(node_id, {})
            wid = node.get("wiki_id", node_id)
            if wid in by_id:
                # déjà présent via RAG : on complète le signal graphe
                by_id[wid]["graph_distance"] = d
                if "graph" not in by_id[wid]["sources"]:
                    by_id[wid]["sources"].append("graph")
            else:
                by_id[wid] = {
                    "id": wid,
                    "theme": theme,
                    "type": node.get("type", ""),
                    "title": node.get("title", ""),
                    "path": node.get("source_file", ""),
                    "rag_score": None,
                    "graph_distance": d,
                    "sources": ["graph"],
                }

    results = list(by_id.values())
    for r in results:
        r["category"] = CATEGORY_MAP.get(r["type"], r["type"])

    # tri : d'abord présence dans les deux sources, puis distance graphe, puis score RAG
    def sort_key(r):
        both = len(r["sources"]) == 2
        dist = r["graph_distance"] if r["graph_distance"] is not None else 99
        rag = r["rag_score"] if r["rag_score"] is not None else -99
        return (not both, dist, -rag)

    results.sort(key=sort_key)
    return results


# ---------------------------------------------------------------------------
# Spec OpenAPI — générée depuis les constantes du code (jamais désynchronisée)
# ---------------------------------------------------------------------------
def build_openapi():
    """Construit la spec OpenAPI 3.1 à partir des constantes du serveur.

    Source de vérité unique : si un défaut ou la liste des thèmes change,
    la spec (et la page HTML qui en dérive) suit automatiquement.
    """
    result_item = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "example": "GLO00643"},
            "theme": {"type": "string", "example": "marketing",
                      "description": "thème d'origine du résultat (utile en recherche multi-thème)"},
            "type": {"type": "string", "example": "glossary_term"},
            "title": {"type": "string"},
            "path": {"type": "string",
                     "description": "chemin du fichier .md relatif à la racine du projet"},
            "category": {"type": "string", "example": "glossaire"},
            "rag_score": {"type": ["number", "null"],
                          "description": "score de re-ranking ; null si absent du RAG"},
            "graph_distance": {"type": ["integer", "null"],
                               "description": "distance (sauts) au pivot le plus proche ; "
                                              "0 = pivot RAG, null si graphe indisponible"},
            "sources": {"type": "array", "items": {"type": "string", "enum": ["rag", "graph"]}},
            "linked_ids": {"type": ["array", "null"], "items": {"type": "string"},
                           "description": "IDs `[[ID-slug]]` référencés dans le corps, dans "
                                          "l'ordre d'apparition ; présent seulement si "
                                          "include=excerpt|full."},
        },
    }
    search_response_example = {
        "query": {
            "theme": "marketing",
            "raw": "positionnement produit SaaS B2B",
            "used": "positionnement produit SaaS B2B, segmentation, "
                    "proposition de valeur, différenciation acheteur",
            "rephrased": True,
        },
        "params": {"top_k": 10, "depth": 1, "max_nodes": 80, "rephrase": True},
        "graph_available": True,
        "total": 12,
        "results": [
            {
                "id": "GLO00643", "type": "glossary_term", "title": "good-better-best",
                "path": "wiki/themes/marketing/notes/glossaire/GLO00643-good-better-best.md",
                "category": "glossaire", "rag_score": 2.635, "graph_distance": 0,
                "sources": ["rag", "graph"],
            },
            {
                "id": "ATO01534", "type": "atomic_note",
                "title": "La solution à un problème de positionnement se trouve "
                         "dans l'esprit du prospect et non dans le produit lui-même",
                "path": "wiki/themes/marketing/notes/atomiques/ATO01534-...-prospect.md",
                "category": "atomique", "rag_score": 0.657, "graph_distance": 0,
                "sources": ["rag", "graph"],
            },
            {
                "id": "GLO00634", "type": "glossary_term", "title": "willingness-to-pay",
                "path": "wiki/themes/marketing/notes/glossaire/GLO00634-willingness-to-pay.md",
                "category": "glossaire", "rag_score": None, "graph_distance": 1,
                "sources": ["graph"],
            },
        ],
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "API d'exploration du wiki — LDE_WIKI_IA",
            "version": "1.0.0",
            "description": (
                "Pour un sujet donné, renvoie l'ensemble fini des notes du wiki "
                "correspondantes. Pipeline : reformulation LLM optionnelle (CLI claude) "
                "→ recherche sémantique RAG (LanceDB + e5-large + re-ranking) → ancrage "
                "sur les meilleurs résultats → clôture par traversée bornée du graphe de "
                "connaissances. Un seul thème par requête."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": {
            "/search": {
                "get": {
                    "summary": "Rechercher l'ensemble fini des notes d'un sujet",
                    "operationId": "search",
                    "parameters": [
                        {"name": "theme", "in": "query", "required": True,
                         "description": "Thème(s) cible(s). Un seul thème par défaut ; "
                                        "plusieurs thèmes séparés par une virgule "
                                        "(ex : marketing,product_management) déclenchent une "
                                        "recherche multi-thème dont les résultats sont fusionnés "
                                        "et triés ensemble (chaque résultat porte son `theme`).",
                         "schema": {"type": "string", "example": "marketing,product_management"}},
                        {"name": "q", "in": "query", "required": True,
                         "description": "Question ou sujet en langage libre. Alias : query.",
                         "schema": {"type": "string"}},
                        {"name": "top_k", "in": "query",
                         "description": "Nb de pivots RAG conservés (points d'ancrage).",
                         "schema": {"type": "integer", "default": DEFAULT_TOP_K}},
                        {"name": "depth", "in": "query",
                         "description": "Distance de traversée du graphe autour des pivots "
                                        "(0 = pivots seuls, 1 = + voisins directs, etc.).",
                         "schema": {"type": "integer", "default": DEFAULT_DEPTH}},
                        {"name": "max_nodes", "in": "query",
                         "description": "Plafond de sécurité sur la taille de l'ensemble.",
                         "schema": {"type": "integer", "default": DEFAULT_MAX_NODES}},
                        {"name": "rephrase", "in": "query",
                         "description": "Reformuler la question via la CLI claude avant la "
                                        "recherche. Fallback silencieux sur la question brute "
                                        "si indisponible.",
                         "schema": {"type": "boolean", "default": True}},
                        {"name": "include", "in": "query",
                         "description": "Niveau de détail par note. meta = métadonnées seules "
                                        "(défaut, léger) ; excerpt = + un extrait (idée "
                                        "principale) ; full = + le corps Markdown complet et "
                                        "le frontmatter.",
                         "schema": {"type": "string", "enum": list(INCLUDE_LEVELS),
                                    "default": "meta"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Ensemble fini de notes, trié par pertinence.",
                            "content": {"application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "object", "properties": {
                                            "theme": {"oneOf": [
                                                {"type": "string"},
                                                {"type": "array", "items": {"type": "string"}}],
                                                "description": "chaîne si un seul thème demandé, "
                                                               "tableau en recherche multi-thème"},
                                            "raw": {"type": "string"},
                                            "used": {"type": "string",
                                                     "description": "requête réellement envoyée au RAG"},
                                            "rephrased": {"type": "boolean"},
                                        }},
                                        "params": {"type": "object"},
                                        "graph_available": {"oneOf": [
                                            {"type": "boolean"},
                                            {"type": "object",
                                             "additionalProperties": {"type": "boolean"}}],
                                            "description": "booléen si un seul thème, objet "
                                                           "{theme: booléen} en multi-thème"},
                                        "total": {"type": "integer"},
                                        "results": {"type": "array", "items": result_item},
                                    },
                                },
                                "example": search_response_example,
                            }},
                        },
                        "400": {"description": "Thème inconnu ou paramètre q manquant."},
                        "503": {"description": "Base RAG absente pour ce thème."},
                    },
                },
                "post": {
                    "summary": "Rechercher (mêmes paramètres en corps JSON)",
                    "operationId": "searchPost",
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["theme", "q"],
                        "properties": {
                            "theme": {"type": "string",
                                      "description": "Thème(s), séparés par une virgule pour "
                                                     "une recherche multi-thème.",
                                      "example": "marketing,product_management"},
                            "q": {"type": "string"},
                            "top_k": {"type": "integer", "default": DEFAULT_TOP_K},
                            "depth": {"type": "integer", "default": DEFAULT_DEPTH},
                            "max_nodes": {"type": "integer", "default": DEFAULT_MAX_NODES},
                            "rephrase": {"type": "boolean", "default": True},
                            "include": {"type": "string", "enum": list(INCLUDE_LEVELS),
                                        "default": "meta"},
                        },
                    }}}},
                    "responses": {"200": {"description": "Voir GET /search."}},
                },
            },
            "/note/{id}": {
                "get": {
                    "summary": "Récupérer une note complète (Markdown + frontmatter)",
                    "operationId": "note",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True,
                         "description": "ID de la note (ex : GLO00643, ATO01534).",
                         "schema": {"type": "string"}},
                        {"name": "theme", "in": "query", "required": True,
                         "description": "Thème de la note.",
                         "schema": {"type": "string", "enum": VALID_THEMES,
                                    "default": "maintenance"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Note complète : corps Markdown brut + "
                                           "frontmatter parsé.",
                            "content": {"application/json": {"example": {
                                "id": "GLO00643", "uid": "g0643000-...", "theme": "marketing",
                                "type": "glossary_term", "title": "Good-Better-Best",
                                "status": "draft",
                                "path": "wiki/themes/marketing/notes/glossaire/GLO00643-good-better-best.md",
                                "frontmatter": {"term": "good-better-best",
                                                "domain": ["marketing", "pricing"],
                                                "source_ids": ["SRC00055"]},
                                "body": "# Good-Better-Best\n\n## Définition courte\n\n…",
                                "linked_ids": ["GLO00634", "ATO01534"],
                            }}},
                        },
                        "404": {"description": "Note introuvable pour cet ID / thème."},
                    },
                },
            },
            "/notes": {
                "get": {
                    "summary": "Récupérer plusieurs notes complètes en un seul appel (batch fetch)",
                    "operationId": "notesBatch",
                    "parameters": [
                        {"name": "ids", "in": "query", "required": True,
                         "description": f"IDs séparés par une virgule (max {MAX_BATCH_IDS}).",
                         "schema": {"type": "string", "example": "GLO00643,ATO01534"}},
                        {"name": "theme", "in": "query", "required": True,
                         "description": "Thème commun aux IDs demandés.",
                         "schema": {"type": "string", "enum": VALID_THEMES,
                                    "default": "maintenance"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Notes trouvées + liste des IDs introuvables.",
                            "content": {"application/json": {"example": {
                                "theme": "marketing", "total": 2,
                                "notes": [
                                    {"id": "GLO00643", "theme": "marketing",
                                     "type": "glossary_term", "title": "Good-Better-Best",
                                     "linked_ids": ["GLO00634"]},
                                    {"id": "ATO01534", "theme": "marketing",
                                     "type": "atomic_note", "title": "…", "linked_ids": []},
                                ],
                                "missing": ["GLO99999"],
                            }}},
                        },
                        "400": {"description": "Thème inconnu, 'ids' manquant, ou plafond dépassé."},
                    },
                },
                "post": {
                    "summary": "Batch fetch (mêmes paramètres en corps JSON)",
                    "operationId": "notesBatchPost",
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["theme", "ids"],
                        "properties": {
                            "theme": {"type": "string", "enum": VALID_THEMES},
                            "ids": {"type": "array", "items": {"type": "string"},
                                    "example": ["GLO00643", "ATO01534"]},
                        },
                    }}}},
                    "responses": {"200": {"description": "Voir GET /notes."}},
                },
            },
            "/themes": {
                "get": {
                    "summary": "Lister les thèmes disponibles",
                    "operationId": "themes",
                    "responses": {"200": {"description": "Tableau des thèmes valides.",
                                          "content": {"application/json": {"schema": {
                                              "type": "array",
                                              "items": {"type": "string"}},
                                              "example": VALID_THEMES}}}},
                },
            },
            "/health": {
                "get": {
                    "summary": "Vérifier que l'API est prête",
                    "operationId": "health",
                    "responses": {"200": {"description": "L'API répond ; modèles chargés.",
                                          "content": {"application/json": {
                                              "example": {"status": "ok",
                                                          "themes": VALID_THEMES}}}}},
                },
            },
            "/openapi.json": {
                "get": {
                    "summary": "Spec OpenAPI de cette API",
                    "operationId": "openapi",
                    "responses": {"200": {"description": "Document OpenAPI 3.1."}},
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Page HTML de documentation — dérivée de la spec, sans dépendance externe
# ---------------------------------------------------------------------------
def render_doc_html(spec):
    """Rend une page HTML autonome (aucun CDN) documentant l'API depuis la spec."""
    info = spec["info"]
    rows = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            params = op.get("parameters", [])
            if params:
                plist = "".join(
                    f"<li><code>{p['name']}</code>"
                    f"{' <span class=req>requis</span>' if p.get('required') else ''}"
                    f" — {p.get('description', '')}"
                    f"{_schema_hint(p.get('schema', {}))}</li>"
                    for p in params
                )
                params_html = f"<ul class=params>{plist}</ul>"
            else:
                params_html = "<span class=muted>aucun paramètre</span>"

            # Bloc réponse : exemple JSON du 200 s'il existe dans la spec
            resp_html = ""
            ok = op.get("responses", {}).get("200", {})
            example = None
            for _mime, media in ok.get("content", {}).items():
                if "example" in media:
                    example = media["example"]
                    break
            if example is not None:
                pretty = json.dumps(example, ensure_ascii=False, indent=2)
                pretty = pretty.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                resp_html = (
                    f"<div class=resp-label>Réponse "
                    f"<span class=muted>({ok.get('description', '')})</span></div>"
                    f"<pre class=resp>{pretty}</pre>"
                )

            rows.append(
                f"<div class=endpoint>"
                f"<div class=head><span class='verb {method}'>{method.upper()}</span>"
                f"<code class=path>{path}</code></div>"
                f"<div class=summary>{op.get('summary', '')}</div>"
                f"{params_html}"
                f"{resp_html}</div>"
            )
    themes_badges = "".join(f"<span class=badge>{t}</span>" for t in VALID_THEMES)
    example = ("/search?theme=marketing&amp;q=positionnement%20produit%20SaaS%20B2B&amp;depth=1")
    return f"""<!DOCTYPE html>
<html lang=fr><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{info['title']}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff; --fg: #0f172a; --card-bg: #f8fafc; --card-border: #cbd5e1;
    --muted: #475569; --code-bg: #e2e8f0; --code-fg: #0f172a; --link: #4f46e5;
    --try-bg: #eef2ff; --path: #1e293b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f172a; --fg: #e2e8f0; --card-bg: #1e293b; --card-border: #334155;
      --muted: #94a3b8; --code-bg: #334155; --code-fg: #e2e8f0; --link: #818cf8;
      --try-bg: #1e1b4b; --path: #e2e8f0;
    }}
  }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          max-width: 860px; margin: 0 auto; padding: 32px 20px; line-height: 1.55;
          background: var(--bg); color: var(--fg); }}
  a {{ color: var(--link); }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .desc {{ color: var(--muted); margin-bottom: 20px; }}
  .badge {{ display: inline-block; background: #6366f1; color: white; border-radius: 12px;
            padding: 2px 10px; font-size: .75rem; margin: 2px; }}
  .endpoint {{ border: 1px solid var(--card-border); border-radius: 10px; padding: 14px 16px;
               margin: 12px 0; background: var(--card-bg); }}
  .head {{ display: flex; align-items: center; gap: 10px; }}
  .verb {{ font-weight: 700; font-size: .72rem; padding: 3px 8px; border-radius: 6px;
           color: white; }}
  .verb.get {{ background: #10b981; }} .verb.post {{ background: #f59e0b; }}
  .path {{ font-size: 1rem; color: var(--path); font-weight: 600; }}
  .summary {{ margin: 6px 0 4px; font-weight: 600; color: var(--fg); }}
  code {{ background: var(--code-bg); color: var(--code-fg); padding: 1px 6px;
          border-radius: 4px; font-size: .88em; }}
  ul.params {{ margin: 6px 0 0; padding-left: 18px; font-size: .9rem; color: var(--fg); }}
  ul.params li {{ margin: 3px 0; }}
  .req {{ color: #dc2626; font-size: .72rem; font-weight: 700; }}
  .muted {{ color: var(--muted); font-size: .85rem; }}
  .resp-label {{ margin-top: 12px; font-size: .8rem; font-weight: 700;
                 text-transform: uppercase; letter-spacing: .4px; color: var(--fg); }}
  pre.resp {{ background: var(--code-bg); color: var(--code-fg); border-radius: 8px;
              padding: 12px 14px; margin: 6px 0 0; overflow-x: auto;
              font-size: .82rem; line-height: 1.45; }}
  .footer {{ margin-top: 28px; font-size: .85rem; color: var(--muted); }}
  .try {{ background: var(--try-bg); border-radius: 8px; padding: 10px 14px; margin: 16px 0;
          font-size: .88rem; }}
</style></head><body>
<h1>{info['title']}</h1>
<div class=desc>{info['description']}</div>
<div><strong>Thèmes :</strong> {themes_badges}</div>
<div class=try>Spec machine : <a href="/openapi.json">/openapi.json</a>
&nbsp;·&nbsp; Exemple : <a href="{example}"><code>{example}</code></a></div>
<h2 style="font-size:1.1rem">Endpoints</h2>
{''.join(rows)}
<div class=footer>OpenAPI {spec['openapi']} · version {info['version']} ·
Cette page et la spec sont générées depuis le code — elles ne se périment pas.</div>
</body></html>"""


def _schema_hint(schema):
    """Petit rappel du type / défaut / enum pour la page HTML."""
    bits = []
    if "enum" in schema and len(schema["enum"]) <= 3:
        bits.append("valeurs : " + ", ".join(schema["enum"]))
    elif "type" in schema:
        t = schema["type"]
        bits.append(t if isinstance(t, str) else "/".join(t))
    if "default" in schema:
        bits.append(f"défaut : {schema['default']}")
    return f" <span class=muted>({'; '.join(bits)})</span>" if bits else ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.route("/")
def root():
    """Négociation de contenu : HTML pour un navigateur, OpenAPI JSON sinon.

    Un humain (Accept: text/html) reçoit la page de doc ; une IA ou un client
    programmatique reçoit directement la spec OpenAPI.
    """
    spec = build_openapi()
    accept = request.headers.get("Accept", "")
    if "text/html" in accept:
        return Response(render_doc_html(spec), mimetype="text/html")
    return jsonify(spec)


@app.route("/openapi.json")
def openapi_json():
    return jsonify(build_openapi())


@app.route("/health")
def health():
    return jsonify({"status": "ok", "themes": VALID_THEMES})


@app.route("/themes")
def themes():
    return jsonify(VALID_THEMES)


def run_search_for_theme(theme, query_used, top_k, depth, max_nodes, include):
    """Exécute le pipeline RAG + graphe pour UN thème. Retourne (results, graph_available)."""
    table = get_table(theme)  # peut lever FileNotFoundError — géré par l'appelant
    rag_pivots = rag_search(table, query_used, top_k)
    pivot_wiki_ids = [r.get("id") for r in rag_pivots if r.get("id")]

    graph = load_graph(theme)
    closure_dist = {}
    graph_available = graph is not None
    if graph_available:
        closure_dist = graph_closure(graph, pivot_wiki_ids, depth, max_nodes)

    results = build_results(theme, table, rag_pivots, graph, closure_dist)

    if include in ("excerpt", "full"):
        for r in results:
            note = read_note(theme, r["id"])
            if not note:
                r["excerpt"] = None
                if include == "full":
                    r["body"] = None
                    r["frontmatter"] = None
                    r["linked_ids"] = None
                continue
            r["excerpt"] = make_excerpt(note["body"])
            if include == "full":
                r["body"] = note["body"]
                r["frontmatter"] = note["frontmatter"]
                r["linked_ids"] = note["linked_ids"]

    return results, graph_available


@app.route("/search", methods=["GET", "POST", "OPTIONS"])
def search():
    if request.method == "OPTIONS":
        return ("", 204)

    # paramètres — GET query string ou POST JSON
    if request.method == "POST" and request.is_json:
        p = request.get_json()
    else:
        p = request.args

    def get(name, default=None):
        val = p.get(name, default)
        return val

    theme_raw = get("theme", "maintenance")
    # multi-thème : "marketing,product_management" — un seul thème reste le cas normal
    if isinstance(theme_raw, list):
        themes_requested = [str(t).strip() for t in theme_raw if str(t).strip()]
    else:
        themes_requested = [t.strip() for t in str(theme_raw).split(",") if t.strip()]

    unknown = [t for t in themes_requested if t not in VALID_THEMES]
    if unknown or not themes_requested:
        return jsonify({"error": f"thème(s) inconnu(s) : {unknown or theme_raw}",
                        "valid_themes": VALID_THEMES}), 400

    question = (get("q") or get("query") or "").strip()
    if not question:
        return jsonify({"error": "paramètre 'q' manquant"}), 400

    try:
        top_k = int(get("top_k", DEFAULT_TOP_K))
        depth = int(get("depth", DEFAULT_DEPTH))
        max_nodes = int(get("max_nodes", DEFAULT_MAX_NODES))
    except (TypeError, ValueError):
        return jsonify({"error": "top_k, depth, max_nodes doivent être entiers"}), 400

    rephrase_flag = str(get("rephrase", "true")).lower() not in ("false", "0", "no")

    include = str(get("include", "meta")).lower()
    if include not in INCLUDE_LEVELS:
        return jsonify({"error": f"include invalide : {include}",
                        "valid": list(INCLUDE_LEVELS)}), 400

    # 1. reformulation — une seule fois ; le thème passé au reformulateur est le
    #    premier demandé (sert uniquement à cadrer le vocabulaire du prompt).
    if rephrase_flag:
        query_used, rephrased = rephrase_query(question, themes_requested[0])
    else:
        query_used, rephrased = question, False

    # 2-5. pipeline par thème, puis fusion — chaque résultat porte son "theme"
    all_results = []
    graph_availability = {}
    for theme in themes_requested:
        try:
            results, graph_available = run_search_for_theme(
                theme, query_used, top_k, depth, max_nodes, include)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 503
        all_results.extend(results)
        graph_availability[theme] = graph_available

    # tri global : présence dans les deux sources, puis distance graphe, puis score RAG
    def sort_key(r):
        both = len(r["sources"]) == 2
        dist = r["graph_distance"] if r["graph_distance"] is not None else 99
        rag = r["rag_score"] if r["rag_score"] is not None else -99
        return (not both, dist, -rag)

    all_results.sort(key=sort_key)

    multi = len(themes_requested) > 1
    return jsonify({
        "query": {
            "theme": themes_requested if multi else themes_requested[0],
            "raw": question,
            "used": query_used,
            "rephrased": rephrased,
        },
        "params": {"top_k": top_k, "depth": depth, "max_nodes": max_nodes,
                   "rephrase": rephrase_flag, "include": include},
        "graph_available": graph_availability if multi else graph_availability[themes_requested[0]],
        "total": len(all_results),
        "results": all_results,
    })


@app.route("/note/<wiki_id>", methods=["GET", "OPTIONS"])
def note(wiki_id):
    """Récupère une note complète par son ID (Markdown brut + frontmatter parsé)."""
    if request.method == "OPTIONS":
        return ("", 204)
    theme = request.args.get("theme", "maintenance")
    if theme not in VALID_THEMES:
        return jsonify({"error": f"thème inconnu : {theme}",
                        "valid_themes": VALID_THEMES}), 400
    result = read_note(theme, wiki_id.strip())
    if result is None:
        return jsonify({"error": f"note introuvable : {wiki_id} (thème {theme})"}), 404
    return jsonify(result)


@app.route("/notes", methods=["GET", "POST", "OPTIONS"])
def notes_batch():
    """Récupère plusieurs notes complètes en un seul appel.

    GET  /notes?theme=marketing&ids=GLO00643,ATO01534
    POST /notes  {"theme": "marketing", "ids": ["GLO00643", "ATO01534"]}

    Les notes introuvables sont incluses dans `missing` plutôt que de faire
    échouer l'appel entier. Plafonné à MAX_BATCH_IDS pour éviter un abus.
    """
    if request.method == "OPTIONS":
        return ("", 204)

    if request.method == "POST" and request.is_json:
        p = request.get_json()
        raw_ids = p.get("ids", [])
        theme = p.get("theme", "maintenance")
    else:
        p = request.args
        raw_ids = p.get("ids", "")
        theme = p.get("theme", "maintenance")

    if isinstance(raw_ids, str):
        ids = [i.strip() for i in raw_ids.split(",") if i.strip()]
    else:
        ids = [str(i).strip() for i in raw_ids if str(i).strip()]

    if theme not in VALID_THEMES:
        return jsonify({"error": f"thème inconnu : {theme}",
                        "valid_themes": VALID_THEMES}), 400
    if not ids:
        return jsonify({"error": "paramètre 'ids' manquant ou vide"}), 400
    if len(ids) > MAX_BATCH_IDS:
        return jsonify({"error": f"trop d'ids demandés ({len(ids)}) ; "
                                  f"plafond {MAX_BATCH_IDS}"}), 400

    found, missing = [], []
    for wid in ids:
        note = read_note(theme, wid)
        if note is None:
            missing.append(wid)
        else:
            found.append(note)

    return jsonify({"theme": theme, "total": len(found),
                     "notes": found, "missing": missing})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="API d'exploration du wiki")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Adresse d'écoute (défaut : 127.0.0.1 ; 0.0.0.0 pour exposer sur le réseau local)")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
