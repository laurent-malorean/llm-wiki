# CLAUDE.md — LDE_WIKI_IA

## Environnement Python

Ce projet est auto-porté. Il ne doit dépendre d'aucune librairie, outil ou configuration extérieure au projet.

### Utilisation obligatoire du .venv

Toujours utiliser l'interpréteur Python du `.venv` racine du projet.

Ne jamais utiliser `python`, `pip`, `python3` ou `pip3` sans préfixe de chemin.

Commandes correctes :
```
.venv/bin/python <script>
.venv/bin/pip install -r requirements.txt
```

Commandes interdites :
```
python <script>        # utilise l'interpréteur système ou Anaconda
pip install ...        # installe dans l'environnement global
```

### Dépendances

Toutes les dépendances du projet sont dans `requirements.txt` à la racine.

Ne jamais créer de `requirements.txt` dans un sous-dossier.

Si une nouvelle dépendance est nécessaire, l'ajouter dans `requirements.txt` à la racine avec la section appropriée.

### Indépendance du projet

Ce projet ne doit pas dépendre de :
- l'environnement Anaconda de la machine ;
- des packages installés globalement sur le système ;
- des fichiers ou configurations situés hors du répertoire du projet.

Tout ce qui est nécessaire au fonctionnement du projet doit être dans le projet.

## Structure du projet

```
.venv/                  environnement Python isolé (Python 3.11, voir .python-version)
.python-version         version de Python attendue
requirements.txt        toutes les dépendances (extraction PDF, RAG, graphe, API, site)
wiki/                   contenu du wiki, organisé par thème
  counters.json         compteurs globaux (partagés entre thèmes)
  themes.json           liste des thèmes valides
  memory/               mémoire persistante du projet
  themes/
    maintenance/        maintenance industrielle (thème par défaut)
    product_management/ product management
    design_ux_ui/       design UX/UI
    ai_data_science/    intelligence artificielle et data science
    marketing/          marketing
    physique_quantique/ physique quantique
    sociologie/         sociologie
_RAG/                   scripts RAG (rag_update.py, rag_search.py, rag_utils.py)
  wiki/{theme}/         base vectorielle LanceDB générée (index.lance + manifest.json)
_GRAPHITY/{theme}/      graphe de liens généré (graph.json)
_LOGS/wiki/{theme}/     logs et rapports générés (source-add, ingestion, lint, rag, gap-analysis)
_API/                   API locale d'exploration du wiki (wiki_api.py, port 8090)
website/                serveur local de révision espacée (server.py, port 8083)
tmp/                    fichiers temporaires (PID, logs serveurs, journal des hooks)
.cache/                 cache des modèles HuggingFace
.claude/                skills, commandes, règles, hooks, scripts
```

Les dossiers `_RAG/wiki/`, `_GRAPHITY/`, `_LOGS/`, `tmp/` et `.cache/` sont générés et ignorés par git.

## Wiki

Le contenu du wiki est dans `wiki/`, organisé par thèmes. Voir `wiki/CLAUDE.md` pour la structure multi-thèmes et le CLAUDE.md de chaque thème pour les règles spécifiques au domaine.

## Thèmes

Les skills acceptent un paramètre `theme` (défaut : `maintenance`). Thèmes valides : `maintenance`, `product_management`, `design_ux_ui`, `ai_data_science`, `marketing`, `physique_quantique`, `sociologie`.

## Liens wiki

Les liens internes doivent toujours utiliser le format `[[ID-slug]]` compatible Obsidian.

Ne jamais produire de liens en format Markdown `[texte](chemin)` pour les références internes au wiki.

Correct :
```
[[GLO00093-diagramme-de-gantt]]
```

Incorrect :
```
[Diagramme de Gantt](notes/glossaire/GLO00093-diagramme-de-gantt.md)
```

## Règles de comportement

Ne jamais ajouter spontanément du contenu non demandé (sections, résumés, reformulations, suggestions inline). Proposer explicitement si pertinent, mais ne jamais produire sans validation de l'utilisateur.

## Chemins dans les fichiers générés

Tous les fichiers générés par les skills (logs, rapports, index) doivent utiliser des **chemins relatifs** à la racine du projet.

Ne jamais écrire de chemin absolu dans un fichier versionné ou loggué.

Correct :
```
wiki/themes/maintenance/notes/atomiques/ATO00001-exemple.md
```

Incorrect :
```
/Users/x/projets/LDE_WIKI_IA/wiki/themes/maintenance/notes/atomiques/ATO00001-exemple.md
```

Cette règle s'applique à tous les fichiers produits : rapports lint, rapports d'ingestion, index, logs.

## Périmètre d'écriture

Il est **interdit** d'écrire des fichiers en dehors du répertoire du projet.

Tout fichier créé ou modifié par Claude doit être dans le répertoire racine du projet.

Ne jamais écrire dans `~/.claude/` ou tout autre chemin hors du projet.

La mémoire persistante du projet se trouve dans `wiki/memory/MEMORY.md` (versionné dans le projet).
