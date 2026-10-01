#!/usr/bin/env python3
"""
Serveur de révision espacée — Wiki (un thème à la fois)
Port : 8083

Usage :
    .venv/bin/python website/server.py [--theme THEME] [--host HOST] [--port PORT]
"""
import argparse
import json
import os
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, make_response, redirect, render_template_string, request, url_for

# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
VALID_THEMES = json.loads((BASE_DIR / "wiki" / "themes.json").read_text(encoding="utf-8"))
THEME = "maintenance"
THEME_ROOT = BASE_DIR / "wiki" / "themes" / THEME
ARTIFACTS_INDEX = THEME_ROOT / "indexes" / "artifacts.index.json"
TIME_INI = Path(__file__).parent / "time.ini.md"
DATA_DIR = Path(__file__).parent / "data"
STATE_FILE = DATA_DIR / "review_state.json"

app = Flask(__name__)

@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response

# ---------------------------------------------------------------------------
# Cache en mémoire invalidé par mtime
# ---------------------------------------------------------------------------
_cache = {
    "notes": [],        # liste de dicts issus de l'index artifacts
    "state": {"notes": {}, "generated_at": "", "config": {}},  # contenu de review_state.json
    "artifacts_mtime": -1.0,  # -1 : force le premier chargement, même si le fichier n'existe pas
    "state_mtime": -1.0,
}


def load_notes_catalog(index_path: Path) -> list:
    """Charge le catalogue depuis artifacts.index.json, exclut les source_record."""
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    notes = []
    for e in entries:
        if e.get("type") in ("source_record", "moc"):
            continue
        uid = e.get("uid", "")
        if not uid:
            continue
        notes.append({
            "uid": uid,
            "id": e.get("id", ""),
            "title": e.get("title", e.get("id", "")),
            "type": e.get("type", "unknown"),
            "filepath": e.get("path", e.get("file", "")),  # relatif à wiki/
        })
    return notes


def refresh_cache_if_needed():
    """Recharge uniquement les fichiers dont le mtime a changé."""
    new_amt = ARTIFACTS_INDEX.stat().st_mtime if ARTIFACTS_INDEX.exists() else 0.0
    new_smt = STATE_FILE.stat().st_mtime if STATE_FILE.exists() else 0.0

    if new_amt != _cache["artifacts_mtime"]:
        _cache["notes"] = load_notes_catalog(ARTIFACTS_INDEX)
        _cache["artifacts_mtime"] = new_amt

    if new_smt != _cache["state_mtime"]:
        _cache["state"] = load_state()
        _cache["state_mtime"] = new_smt


# ---------------------------------------------------------------------------
# Couleurs par type de note
# ---------------------------------------------------------------------------
TYPE_COLORS = {
    "atomic_note": "#3b82f6",       # bleu
    "glossary_term": "#10b981",     # vert
    "regulatory_text": "#f59e0b",   # orange
    "habilitation": "#8b5cf6",      # violet
    "norm_reference": "#ec4899",    # rose
    "business_process": "#06b6d4",  # cyan
    "business_rule": "#ef4444",     # rouge
    "indicator": "#84cc16",         # vert clair
    "moc": "#6b7280",               # gris
    "normative_requirement": "#f97316",  # orange foncé
    "domain_object": "#14b8a6",     # teal
}

TYPE_LABELS = {
    "atomic_note": "Note atomique",
    "glossary_term": "Glossaire",
    "regulatory_text": "Réglementaire",
    "habilitation": "Habilitation",
    "norm_reference": "Norme",
    "business_process": "Processus",
    "business_rule": "Règle métier",
    "indicator": "Indicateur",
    "moc": "MOC",
    "normative_requirement": "Exigence",
    "domain_object": "Objet métier",
}

STATE_COLORS = {
    "short": "#ef4444",
    "middle": "#f59e0b",
    "long": "#10b981",
}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def parse_time_config() -> dict:
    config = {"short": 10, "middle": 30, "long": 60}
    try:
        for line in TIME_INI.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\w+)\s*=\s*(\d+)$", line.strip())
            if m:
                config[m.group(1)] = int(m.group(2))
    except Exception:
        pass
    return config


# ---------------------------------------------------------------------------
# Index slug → titre (construit depuis le cache)
# ---------------------------------------------------------------------------
def build_slug_title_index() -> dict:
    """Retourne un dict {slug: title} pour résoudre les [[liens]] wikilink."""
    index = {}
    for note in _cache["notes"]:
        nid = note.get("id", "")
        title = note.get("title", "")
        filepath = note.get("filepath", "")
        if not title:
            continue
        # Clé = nom de fichier sans extension
        if filepath:
            slug = Path(filepath).stem
            index[slug] = title
        # Clé = id seul (ex: ATO00045)
        if nid:
            index[nid] = title
    return index


# ---------------------------------------------------------------------------
# Lecture du corps d'une note (lazy — uniquement pour la note affichée)
# ---------------------------------------------------------------------------
def read_note_body(filepath_rel: str) -> str:
    """Lit uniquement le corps Markdown d'une note (après le frontmatter)."""
    try:
        # filepath_rel est relatif au PROJECT_ROOT (ex: wiki/themes/maintenance/notes/...)
        full_path = BASE_DIR / filepath_rel
        if not full_path.exists():
            # Fallback : chemin relatif à la racine du thème
            full_path = THEME_ROOT / filepath_rel
        text = full_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ""
    return parts[2].strip()


# ---------------------------------------------------------------------------
# Résolution des [[wikilinks]] vers titres lisibles
# ---------------------------------------------------------------------------
def resolve_wikilinks(body: str, slug_index: dict) -> str:
    """Remplace [[slug]] par le titre de la note si connu, sinon garde le slug."""
    def replacer(m):
        ref = m.group(1).strip()
        return slug_index.get(ref, ref)
    return re.sub(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", replacer, body)


# ---------------------------------------------------------------------------
# Rendu Markdown vers HTML
# ---------------------------------------------------------------------------
def render_body(body: str, slug_index: dict) -> str:
    body = resolve_wikilinks(body, slug_index)
    try:
        import markdown as md_lib
        return md_lib.markdown(body, extensions=["extra", "nl2br"])
    except ImportError:
        body = body.replace("&", "&amp;")
        lines = body.split("\n")
        result = []
        for line in lines:
            if line.startswith("## "):
                result.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("# "):
                result.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("- "):
                result.append(f"<li>{line[2:]}</li>")
            elif line.strip():
                result.append(f"<p>{line}</p>")
        return "\n".join(result)


# ---------------------------------------------------------------------------
# État de révision
# ---------------------------------------------------------------------------
def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    state.setdefault("notes", {})
    state.setdefault("generated_at", "")
    state.setdefault("config", {})
    return state


def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)
    # Invalider le cache state pour forcer rechargement au prochain GET
    _cache["state_mtime"] = -1.0


# ---------------------------------------------------------------------------
# Calcul du statut d'une note
# ---------------------------------------------------------------------------
def compute_status(note: dict, state_data: dict, config: dict) -> dict:
    uid = note["uid"]
    entry = state_data["notes"].get(uid, {})
    state = entry.get("state", "short")
    next_review = entry.get("next_review")
    if not next_review:
        # Note jamais vue : due immédiatement
        next_review = date.today().isoformat()
    try:
        nr_date = date.fromisoformat(next_review)
    except Exception:
        nr_date = date.today()
    is_due = nr_date <= date.today()
    return {"state": state, "next_review": next_review, "is_due": is_due}


# ---------------------------------------------------------------------------
# Construction de la liste des notes dues (depuis le cache)
# ---------------------------------------------------------------------------
def get_all_notes_with_status():
    refresh_cache_if_needed()
    config = parse_time_config()
    state_data = _cache["state"]

    due_long = []
    due_middle = []
    due_short = []

    counters = {
        "long_due": 0, "long_total": 0,
        "middle_due": 0, "middle_total": 0,
        "short_due": 0, "short_total": 0,
    }
    next_dates = []

    for note in _cache["notes"]:
        status = compute_status(note, state_data, config)
        note_with_status = dict(note, **status)

        s = note_with_status["state"]
        counters[f"{s}_total"] += 1

        if note_with_status["is_due"]:
            counters[f"{s}_due"] += 1
            if s == "long":
                due_long.append(note_with_status)
            elif s == "middle":
                due_middle.append(note_with_status)
            else:
                due_short.append(note_with_status)
        else:
            try:
                next_dates.append(date.fromisoformat(note_with_status["next_review"]))
            except Exception:
                pass

    random.shuffle(due_long)
    random.shuffle(due_middle)
    random.shuffle(due_short)

    due_all = due_long + due_middle + due_short

    next_session = None
    if next_dates and not due_all:
        next_session = min(next_dates).isoformat()

    return due_all, counters, next_session, config


# ---------------------------------------------------------------------------
# Transitions d'état
# ---------------------------------------------------------------------------
TRANSITIONS = {
    ("short",  True):  ("middle", "middle"),
    ("short",  False): ("short",  "short"),
    ("middle", True):  ("long",   "long"),
    ("middle", False): ("short",  "short"),
    ("long",   True):  ("long",   "long"),
    ("long",   False): ("middle", "middle"),
}


# ---------------------------------------------------------------------------
# Template HTML
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr" data-theme="{{ theme }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wiki {{ wiki_theme }} — Révision</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; }

  :root {
    --bg: #ffffff;
    --fg: #1e293b;
    --header-bg: #1e293b;
    --header-fg: white;
    --card-border: #e2e8f0;
    --meta-border: #f1f5f9;
    --title-color: #0f172a;
    --body-color: #334155;
    --h2-color: #475569;
    --code-bg: #f1f5f9;
    --link-color: #6366f1;
    --progress-color: #94a3b8;
    --no-due-color: #64748b;
    --toggle-bg: #334155;
    --toggle-fg: #94a3b8;
    --progressbar-bg: #e2e8f0;
    --progressbar-fill: #3b82f6;
    --skip-bg: #334155;
    --skip-fg: #94a3b8;
    --next-review-color: #64748b;
  }
  [data-theme="dark"] {
    --bg: #0f172a;
    --fg: #e2e8f0;
    --header-bg: #020617;
    --header-fg: #e2e8f0;
    --card-border: #1e293b;
    --meta-border: #1e293b;
    --title-color: #f1f5f9;
    --body-color: #cbd5e1;
    --h2-color: #94a3b8;
    --code-bg: #1e293b;
    --link-color: #818cf8;
    --progress-color: #64748b;
    --no-due-color: #94a3b8;
    --toggle-bg: #1e293b;
    --toggle-fg: #fbbf24;
    --progressbar-bg: #1e293b;
    --progressbar-fill: #3b82f6;
    --skip-bg: #1e293b;
    --skip-fg: #64748b;
    --next-review-color: #475569;
  }

  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--fg);
         display: flex; flex-direction: column; }

  /* Header */
  .header { background: var(--header-bg); color: var(--header-fg); padding: 10px 20px;
            display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
            flex-shrink: 0; }
  .header-title { font-weight: 700; font-size: 0.9rem; letter-spacing: .5px; }
  .counters { display: flex; gap: 8px; flex-wrap: wrap; }
  .counter-badge { padding: 3px 10px; border-radius: 20px; font-size: 0.74rem;
                   font-weight: 600; display: flex; align-items: center; gap: 5px; }
  .counter-badge.long  { background: #064e3b; color: #6ee7b7; }
  .counter-badge.middle { background: #78350f; color: #fde68a; }
  .counter-badge.short { background: #7f1d1d; color: #fca5a5; }
  .counter-badge .due  { font-size: 0.95rem; }

  /* Compteurs dans card-actions */
  .side-counters { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
  .side-counters .counter-badge { border-radius: 6px; padding: 6px 4px;
                                   flex-direction: column; gap: 1px;
                                   align-items: center; font-size: 0.7rem;
                                   text-align: center; }
  .side-counters .counter-badge .due { font-size: 1.2rem; font-weight: 700; display: block; line-height: 1.1; }

  /* Filtres type */
  .type-filters { display: flex; gap: 6px; flex-wrap: wrap; margin-left: auto; }
  .type-filter-btn { padding: 3px 9px; border-radius: 20px; font-size: 0.7rem;
                     font-weight: 600; cursor: pointer; border: none;
                     opacity: 0.45; transition: opacity .15s; color: white; }
  .type-filter-btn.active { opacity: 1; }
  .type-filter-btn:hover { opacity: 0.85; }

  /* Toggle dark mode */
  .theme-toggle { background: var(--toggle-bg); color: var(--toggle-fg);
                  border: none; border-radius: 20px; padding: 4px 12px;
                  font-size: 0.78rem; font-weight: 600; cursor: pointer; }

  /* Barre de progression session */
  .session-bar-wrap { flex-shrink: 0; padding: 6px 0 2px; }
  .session-bar-track { height: 8px; background: var(--progressbar-bg); border-radius: 4px; overflow: hidden; }
  .session-bar-fill  { height: 100%; border-radius: 2px;
                       transition: width .3s ease, background .3s ease; }
  .session-label { text-align: center; font-size: 0.74rem; color: var(--progress-color);
                   padding: 4px 0 6px; }

  /* Main */
  .main { flex: 1; display: flex; flex-direction: column;
          max-width: 860px; width: 100%; margin: 0 auto;
          padding: 8px 16px; min-height: 0; }
  .main-content { flex: 1; display: flex; flex-direction: column; min-height: 0; }

  /* No-due */
  .no-due { text-align: center; padding: 60px 0; color: var(--no-due-color); }
  .no-due h2 { font-size: 1.3rem; margin-bottom: 8px; }

  /* Card — fade */
  .card { display: flex; flex-direction: row; flex: 1; min-height: 0;
          animation: fadeIn .18s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); }
                      to   { opacity: 1; transform: translateY(0); } }

  /* Colonne gauche (contenu) */
  .card-content { display: flex; flex-direction: column; flex: 1; min-width: 0;
                  border-right: 1px solid var(--card-border); }
  .card-meta { padding: 10px 16px; border-bottom: 1px solid var(--meta-border);
               display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
               flex-shrink: 0; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 0.7rem;
           font-weight: 600; color: white; }
  .note-id { color: #94a3b8; font-size: 0.74rem; font-family: monospace; }
  .state-badge { padding: 2px 8px; border-radius: 10px; font-size: 0.7rem;
                 font-weight: 700; color: white; }

  .card-title { padding: 12px 16px 6px; font-size: 1.265rem; font-weight: 700;
                line-height: 1.35; color: var(--title-color); flex-shrink: 0; }

  .card-body { padding: 4px 16px 12px; color: var(--body-color); font-size: 1.01rem;
               line-height: 1.65; overflow-y: auto; flex: 1; min-height: 0; }
  .card-body h1 { font-size: 0.95rem; font-weight: 700; margin: 12px 0 3px; color: var(--title-color); }
  .card-body h2 { font-size: 0.82rem; font-weight: 700; margin: 10px 0 3px;
                  color: var(--h2-color); text-transform: uppercase; letter-spacing: .4px; }
  .card-body p  { margin: 0 0 6px; }
  .card-body li { margin: 2px 0 2px 16px; }
  .card-body em { color: var(--link-color); font-style: normal; }
  .card-body code { background: var(--code-bg); padding: 1px 4px; border-radius: 3px;
                    font-size: 0.83em; }
  .card-body pre { background: var(--code-bg); padding: 8px; border-radius: 5px;
                   overflow-x: auto; font-size: 0.8em; margin: 6px 0; }

  /* Section validation atomique masquée */
  .card-body .validation-section { display: none; }

  /* Colonne droite */
  .card-actions { display: flex; flex-direction: column; gap: 48px;
                  padding: 20px 14px; width: 130px; flex-shrink: 0;
                  justify-content: center; align-items: stretch; }
  .btn { padding: 14px 10px; border: none; border-radius: 8px; font-size: 0.88rem;
         font-weight: 600; cursor: pointer; width: 100%; transition: opacity .15s;
         line-height: 1.3; }
  .btn:hover { opacity: .88; }
  .btn-yes  { background: #10b981; color: white; }
  .btn-no   { background: #ef4444; color: white; }
  .btn-skip { background: var(--skip-bg); color: var(--skip-fg);
              font-size: 0.78rem; padding: 8px 10px; margin-top: -70px; }

  /* Next review hint */
  .next-hint { font-size: 0.68rem; color: var(--next-review-color);
               text-align: center; margin-top: 4px; }
</style>
</head>
<body>

<div class="header">
  <span class="header-title">WIKI {{ wiki_theme | upper }}</span>

  <div class="type-filters">
    {% for ttype, tlabel, tcolor in type_filters %}
      <a href="/?type={{ ttype if active_type != ttype else '' }}">
        <button class="type-filter-btn {{ 'active' if active_type == ttype else '' }}"
                style="background: {{ tcolor }}">{{ tlabel }}</button>
      </a>
    {% endfor %}
  </div>

  <form method="post" action="/theme">
    <button type="submit" class="theme-toggle">
      {% if theme == "dark" %}☀{% else %}☾{% endif %}
    </button>
  </form>
</div>

<div class="main">
  <div class="main-content">
  {% if note %}
    <div class="session-bar-wrap">
      <div class="session-bar-track">
        <div class="session-bar-fill" style="width: {{ session_pct }}%; background: {{ session_bar_color }}"></div>
      </div>
      <div class="session-label">{{ session_done }} / {{ session_total }} aujourd'hui</div>
    </div>

    <div class="card" id="card">
      <div class="card-content">
        <div class="card-meta">
          <span class="note-id">{{ note.id }}</span>
          <span class="badge" style="background: {{ type_color }}">{{ type_label }}</span>
          <span class="state-badge" style="background: {{ state_color }}">{{ note.state }}</span>
        </div>
        <div class="card-title">{{ note.title }}</div>
        <div class="card-body" id="card-body">{{ body_html | safe }}</div>
      </div>
      <div class="card-actions">
        <div class="side-counters">
          <div class="counter-badge long"><span class="due">{{ counters.long_due }}</span>/{{ counters.long_total }}<br>long</div>
          <div class="counter-badge middle"><span class="due">{{ counters.middle_due }}</span>/{{ counters.middle_total }}<br>middle</div>
          <div class="counter-badge short"><span class="due">{{ counters.short_due }}</span>/{{ counters.short_total }}<br>short</div>
        </div>
        <form method="post" action="/review/{{ note.uid }}{% if active_type %}?type={{ active_type }}{% endif %}">
          <input type="hidden" name="memorized" value="true">
          <button type="submit" class="btn btn-yes" id="btn-yes">Mémorisé</button>
        </form>
        <form method="post" action="/review/{{ note.uid }}{% if active_type %}?type={{ active_type }}{% endif %}">
          <input type="hidden" name="memorized" value="false">
          <button type="submit" class="btn btn-no" id="btn-no">Pas mémorisé</button>
        </form>
        <form method="post" action="/skip/{{ note.uid }}{% if active_type %}?type={{ active_type }}{% endif %}">
          <button type="submit" class="btn btn-skip" id="btn-skip">Passer</button>
        </form>
      </div>
    </div>

  {% else %}
    <div class="no-due">
      <h2>Toutes les notes sont à jour.</h2>
      {% if next_session %}
        <p>Prochaine révision : {{ next_session }}</p>
      {% endif %}
    </div>
  {% endif %}
  </div>
</div>

<script>
// Masquer la section "Validation atomique" dans le corps
document.addEventListener('DOMContentLoaded', function() {
  var body = document.getElementById('card-body');
  if (!body) return;
  var headings = body.querySelectorAll('h2');
  headings.forEach(function(h) {
    if (h.textContent.trim().toLowerCase().includes('validation atomique')) {
      var el = h;
      while (el && el.nextElementSibling && el.nextElementSibling.tagName !== 'H2') {
        var next = el.nextElementSibling;
        el.style.display = 'none';
        el = next;
      }
      el.style.display = 'none';
      h.style.display = 'none';
    }
  });
});
</script>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers routes
# ---------------------------------------------------------------------------
def build_type_filters(active_type: str) -> list:
    """Retourne la liste des types présents dans le catalogue avec couleur."""
    types_present = {n["type"] for n in _cache["notes"]}
    result = []
    for ttype, tlabel in TYPE_LABELS.items():
        if ttype in types_present:
            result.append((ttype, tlabel, TYPE_COLORS.get(ttype, "#6b7280")))
    return result


def compute_next_hint(current_state: str, memorized: bool, config: dict) -> str:
    """Retourne une courte indication de la prochaine révision."""
    new_state, delay_key = TRANSITIONS.get((current_state, memorized), ("short", "short"))
    days = config[delay_key]
    next_date = (date.today() + timedelta(days=days)).isoformat()
    return f"{new_state} · {next_date}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    due_all, counters, next_session, config = get_all_notes_with_status()
    theme = request.cookies.get("theme", "light")
    active_type = request.args.get("type", "")

    # Filtre par type si actif
    filtered = [n for n in due_all if not active_type or n["type"] == active_type]

    # Compteurs de session — persistance serveur (review_state.json)
    state_data = load_state()
    today = date.today().isoformat()
    session_info = state_data.get("session", {})
    current_total = len(filtered)

    DAILY_GOAL = 20

    if session_info.get("date") != today:
        # Nouveau jour ou première session
        session_done = 0
        state_data["session"] = {"date": today, "done": 0}
        save_state(state_data)
    else:
        session_done = session_info.get("done", 0)

    session_total = DAILY_GOAL
    session_pct = min(int(session_done / DAILY_GOAL * 100), 100)

    if session_pct >= 80:
        session_bar_color = "#10b981"   # vert
    elif session_pct >= 40:
        session_bar_color = "#f97316"   # orange
    else:
        session_bar_color = "#ef4444"   # rouge

    note = filtered[0] if filtered else None
    body_html = ""
    type_color = "#6b7280"
    type_label = ""
    state_color = "#6b7280"
    next_yes = ""
    next_no  = ""

    if note:
        body = read_note_body(note["filepath"])
        slug_index = build_slug_title_index()
        body_html = render_body(body, slug_index)
        type_color = TYPE_COLORS.get(note["type"], "#6b7280")
        type_label = TYPE_LABELS.get(note["type"], note["type"])
        state_color = STATE_COLORS.get(note["state"], "#6b7280")
        next_yes = compute_next_hint(note["state"], True,  config)
        next_no  = compute_next_hint(note["state"], False, config)

    type_filters = build_type_filters(active_type)

    resp = make_response(render_template_string(
        HTML_TEMPLATE,
        note=note,
        body_html=body_html,
        counters=counters,
        total_due=len(filtered),
        next_session=next_session,
        type_color=type_color,
        type_label=type_label,
        state_color=state_color,
        theme=theme,
        wiki_theme=THEME,
        active_type=active_type,
        type_filters=type_filters,
        session_total=session_total,
        session_done=session_done,
        session_pct=session_pct,
        session_bar_color=session_bar_color,
        next_yes=next_yes,
        next_no=next_no,
    ))
    return resp


@app.route("/theme", methods=["POST"])
def toggle_theme():
    current = request.cookies.get("theme", "light")
    new_theme = "dark" if current == "light" else "light"
    resp = make_response(redirect(url_for("index")))
    resp.set_cookie("theme", new_theme, max_age=60 * 60 * 24 * 365)
    return resp


@app.route("/skip/<uid>", methods=["POST"])
def skip(uid):
    """Passe la note sans modifier son état — la remet en fin de file."""
    active_type = request.args.get("type", "")
    # On ne touche pas au state_file, on redirige simplement
    # La note sera re-présentée à la prochaine session
    target = url_for("index") + (f"?type={active_type}" if active_type else "")
    return redirect(target)


@app.route("/review/<uid>", methods=["POST"])
def review(uid):
    memorized = request.form.get("memorized", "false").lower() == "true"
    active_type = request.args.get("type", "")
    config = parse_time_config()
    state_data = load_state()

    entry = state_data["notes"].get(uid, {})
    current_state = entry.get("state", "short")

    new_state, delay_key = TRANSITIONS.get(
        (current_state, memorized), ("short", "short")
    )
    next_review_date = (date.today() + timedelta(days=config[delay_key])).isoformat()

    # Retrouver id et filepath depuis le cache
    note_id = entry.get("id", uid)
    note_file = entry.get("file", "")
    if not note_file:
        for n in _cache["notes"]:
            if n["uid"] == uid:
                note_id = n["id"]
                note_file = n["filepath"]
                break

    state_data["notes"][uid] = {
        "id": note_id,
        "file": note_file,
        "state": new_state,
        "next_review": next_review_date,
    }
    state_data["generated_at"] = datetime.now().isoformat()
    state_data["config"] = config

    # Incrémenter session_done côté serveur
    today = date.today().isoformat()
    session_info = state_data.get("session", {})
    if session_info.get("date") == today:
        session_info["done"] = session_info.get("done", 0) + 1
        state_data["session"] = session_info

    save_state(state_data)

    target = url_for("index") + (f"?type={active_type}" if active_type else "")
    return redirect(target)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serveur de révision espacée du wiki")
    parser.add_argument("--theme", default="maintenance", choices=VALID_THEMES,
                        help="Thème à réviser (défaut : maintenance)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Adresse d'écoute (défaut : 127.0.0.1 ; 0.0.0.0 pour exposer sur le réseau local)")
    parser.add_argument("--port", type=int, default=8083)
    args = parser.parse_args()

    THEME = args.theme
    THEME_ROOT = BASE_DIR / "wiki" / "themes" / THEME
    ARTIFACTS_INDEX = THEME_ROOT / "indexes" / "artifacts.index.json"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host=args.host, port=args.port, debug=False)
