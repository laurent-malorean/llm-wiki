# CLAUDE.md — Wiki multi-thèmes

## Structure

Le wiki est organisé par thèmes. Chaque thème a sa propre arborescence complète (sources, notes, indexes, logs).

```
wiki/
  counters.json              compteurs globaux (partagés entre thèmes)
  memory/                    mémoire persistante du projet
  themes/
    maintenance/             maintenance industrielle
    product_management/      product management
    design_ux_ui/            design UX/UI
    ai_data_science/         intelligence artificielle et data science
    marketing/               marketing
    physique_quantique/      physique quantique
    sociologie/              sociologie (Bourdieu, etc.)
```

## Compteurs

Les compteurs d'identifiants (`counters.json`) sont **globaux** : un `ATO00660` est unique quel que soit le thème. Le fichier se trouve à `wiki/counters.json`.

## Thèmes disponibles

- `maintenance` — maintenance industrielle
- `product_management` — product management
- `design_ux_ui` — design UX/UI
- `ai_data_science` — intelligence artificielle et data science
- `marketing` — marketing
- `physique_quantique` — physique quantique
- `sociologie` — sociologie (Bourdieu, pratiques sociales, reproduction, distinction)

## Règles

Les règles de structure (`.claude/rules/`) sont partagées entre tous les thèmes. Les chemins mentionnés dans les rules sont relatifs à la racine du thème (`wiki/themes/{theme}/`).

Voir le CLAUDE.md de chaque thème pour les instructions spécifiques au domaine.
