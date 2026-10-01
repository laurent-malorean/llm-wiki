# Memory — LDE_WIKI_IA

## Conventions de recherche (IMPORTANT)

- Pour **retrouver du contenu dans le wiki**, toujours utiliser **`/wiki-search`** : il combine RAG **+ traversée de graphe + fusion/classement**. Le graphe remonte des artefacts structurellement liés que le RAG seul manque.
- **Ne jamais appeler `_RAG/rag_search.py` seul** comme raccourci de recherche. Un hook `PreToolUse` (`.claude/hooks/warn-rag-solo.sh`) le rappelle automatiquement.
- Usages légitimes de `rag_search.py` isolé (debug, `db-rag-update`, ou le pipeline `/wiki-search` lui-même qui l'appelle avec `graphify`) : ajouter `--solo-rag` à la commande pour supprimer le rappel du hook.

## Project Structure (multi-theme)

- Wiki root: `wiki/` — organized by theme
- Themes: `maintenance`, `product_management`, `design_ux_ui`, `ai_data_science`, `marketing`, `physique_quantique`, `sociologie`
- Counters: `wiki/counters.json` (GLOBAL, shared across themes)
- Per-theme structure: `wiki/themes/{theme}/` contains:
  - `sources/` (records, raw/pdf, raw/txt)
  - `indexes/` (sources.index.json, artifacts.index.json, backlinks.index.json)
  - `notes/` (atomiques, glossaire, normes, exigences-normatives, processus, objets-metier, regles-metier, indicateurs, moc, regles-reglementaires, habilitations)
  - `inbox/`, `archive/`
  - `CLAUDE.md` (theme-specific)
- Logs: `_LOGS/wiki/{theme}/` (source-add, ingestion)
- RAG: `_RAG/wiki/{theme}/` (index.lance, manifest.json) — scripts Python dans `_RAG/`
- Rules: `.claude/rules/` (shared, path-agnostic)
- Skills: `.claude/skills/` (all accept `--theme`, default `maintenance`)
- Memory: `wiki/memory/MEMORY.md` (dans le projet, versionné)
- Ingestion scripts: `.claude/skills/source-ingest/scripts/update_sources_index.py --theme {theme}` et `generate_backlinks.py --theme {theme}`

## Key Conventions

- All wiki content lives under `wiki/themes/{theme}/`. Never create sources, indexes or logs at project root.
- Counters are global — IDs are unique across all themes.

## Tests du projet

- Pour tout test du projet, appliquer le protocole `wiki/memory/test-protocol.md` (Claude = testeur utilisateur : skills uniquement, vérification par lecture, aucun code de substitution).

## Liens cassés

- **Script de scan**: `.claude/scripts/wiki-broken-links.py`
- **Commande**: `.venv/bin/python .claude/scripts/wiki-broken-links.py`
- **Fréquence recommandée**: une fois par semaine ou après ingestion massive
