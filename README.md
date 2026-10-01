# LDE_WIKI_IA

Un wiki de connaissances que vous construisez à partir de vos documents PDF (livres, normes, guides, cours), avec l'aide de Claude Code.

Vous ajoutez un document, Claude le lit et en tire des fiches courtes et reliées entre elles : des idées clés, des définitions, des indicateurs, des processus… Vous pouvez ensuite interroger ce wiki en langage courant, l'exposer à d'autres outils via une API, et réviser vos fiches sur un petit site web.

Le wiki est rangé par thèmes (maintenance, marketing, product management, design UX/UI, data science, physique quantique, sociologie). Tout se passe en français.

---

## Installation

### Ce qu'il vous faut

- [Claude Code](https://claude.com/claude-code).
- Python 3.11 (3.10 minimum).
- Recommandé : `jq`. Sans lui, le suivi automatique de vos modifications est désactivé.

Sur Mac avec Homebrew : `brew install python@3.11 jq`

### Mise en place

1. Copiez le dossier du projet où vous voulez sur votre machine.
2. Ouvrez un terminal dans ce dossier et installez l'environnement Python du projet :

   ```bash
   python3.11 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Ouvrez le dossier dans Claude Code.

C'est prêt. Toutes les actions décrites ci-dessous se font en tapant des commandes dans Claude Code.

> Au premier usage de la recherche, environ 2,5 Go de modèles d'IA sont téléchargés. Ils restent dans le dossier du projet.

---

## Parcours 1 — Ajouter et ingérer un document

**But : transformer un PDF en fiches de connaissance.**

1. **Ajoutez le document.**

   ```
   /source_add chemin/vers/mon-livre.pdf --theme maintenance
   ```

   Le PDF est rangé dans le wiki et son texte est extrait. Rien n'est encore analysé.

2. **Faites-le analyser.** Utilisez l'identifiant donné à l'étape 1 (par exemple `SRC00001`) :

   ```
   /source_ingest SRC00001 --theme maintenance
   ```

   Claude lit le document morceau par morceau et crée les fiches. Si une idée existe déjà dans le wiki, il complète la fiche existante au lieu d'en créer une nouvelle. Comptez de quelques minutes à plusieurs heures selon la taille du document.

   Pour analyser plusieurs documents d'affilée :

   ```
   /source-ingest-batch SRC00001 SRC00002 --theme maintenance
   ```

3. **Mettez la recherche à jour.** Sans cette étape, les nouvelles fiches ne sont pas trouvées.

   ```
   /db-rag-update --theme maintenance
   /graphify_update --theme maintenance
   ```

   Au tout premier usage, utilisez `/graphify_build` au lieu de `/graphify_update`.

4. **Optionnel : contrôlez et complétez.**

   | Commande | À quoi elle sert |
   |---|---|
   | `/wiki_lint` | Vérifie que les fiches sont bien formées |
   | `/wiki-check-links` | Liste les liens cassés |
   | `/wiki_gap` | Repère les fiches qui manquent et vous propose de les créer |

> Les PDF scannés (images sans texte) ne donnent rien. Passez-les d'abord dans un logiciel de reconnaissance de caractères (OCR).

---

## Parcours 2 — Interroger le wiki en local

**But : retrouver ce que vous savez sur un sujet.**

Posez votre question en langage courant :

```
/wiki-search pourquoi nos machines tombent souvent en panne --theme maintenance
```

Vous obtenez les fiches les plus pertinentes, classées en définitions, idées clés et processus, avec un niveau de confiance pour chacune. C'est la commande à utiliser dans la plupart des cas.

Autres commandes, pour des besoins précis :

| Commande | Usage |
|---|---|
| `/db-rag-search ind "performance des équipements"` | Recherche limitée à un type de fiche (`ato` idées, `glo` définitions, `ind` indicateurs…) |
| `/graphify_explain <fiche>` | Montre à quoi une fiche est reliée |
| `/graphify_path <fiche A> <fiche B>` | Montre comment deux fiches sont reliées |

Le dossier peut aussi être ouvert dans [Obsidian](https://obsidian.md) : les liens entre fiches y sont cliquables.

---

## Parcours 3 — Utiliser l'API

**But : interroger le wiki depuis un autre outil (script, application, agent).**

1. **Démarrez l'API.**

   ```
   /wiki_api_start
   ```

   Elle répond sur `http://127.0.0.1:8090` au bout d'environ 20 secondes, le temps de charger les modèles.

2. **Interrogez-la.** Par exemple, depuis un terminal :

   ```bash
   curl "http://127.0.0.1:8090/search?theme=maintenance&q=micro-arrêts&include=excerpt"
   ```

   Vous recevez en JSON les fiches correspondant au sujet. Par défaut, la question est d'abord reformulée par Claude ; ajoutez `&rephrase=false` pour une réponse immédiate.

   Principaux appels :

   | Appel | Renvoie |
   |---|---|
   | `/search?theme=…&q=…` | Les fiches liées à un sujet |
   | `/note/ATO00001?theme=…` | Une fiche complète |
   | `/notes?theme=…&ids=ATO00001,GLO00002` | Plusieurs fiches (50 au maximum) |
   | `/themes` | Les thèmes disponibles |

   La documentation complète s'affiche en ouvrant `http://127.0.0.1:8090` dans un navigateur.

3. **Arrêtez-la** quand vous avez fini :

   ```
   /wiki_api_stop
   ```

> L'API n'est accessible que depuis votre machine et n'a pas de mot de passe. Le thème interrogé doit avoir été indexé (parcours 1, étape 3).

---

## Parcours 4 — Réviser sur le site web

**But : mémoriser vos fiches grâce à la révision espacée.**

1. **Démarrez le site** pour le thème de votre choix :

   ```
   /web_start maintenance
   ```

2. **Ouvrez `http://127.0.0.1:8083`** dans votre navigateur.

3. **Révisez.** Une fiche s'affiche ; après l'avoir lue, cliquez sur :
   - **Mémorisé** : la fiche reviendra plus tard ;
   - **Pas mémorisé** : elle reviendra plus tôt ;
   - **Passer** : elle reste en attente.

   Chaque fiche passe par trois niveaux (`short`, `middle`, `long`). Le délai avant la prochaine révision s'allonge à chaque niveau. Vous pouvez filtrer par type de fiche et passer en mode sombre. Une barre suit votre objectif de 20 fiches par jour.

4. **Arrêtez le site** :

   ```
   /web_stop
   ```

Votre progression est conservée d'une session à l'autre. Les délais de révision (10, 30 et 60 jours par défaut) se règlent dans `website/time.ini.md`.

---

## Bon à savoir

- **Thèmes.** Chaque commande accepte `--theme` ; sans précision, c'est `maintenance`. Pour créer un nouveau thème, ajoutez son nom dans `wiki/themes.json` et recopiez l'arborescence d'un thème existant.
- **Vos fichiers.** Les fiches sont de simples fichiers Markdown dans `wiki/themes/<thème>/notes/`. Les PDF que vous ajoutez restent soumis à leurs droits d'auteur.
- **Pour aller plus loin.** Les règles suivies par Claude pour rédiger les fiches sont dans `.claude/rules/`, et les consignes de travail dans `CLAUDE.md`.
