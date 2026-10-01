# CLAUDE.md — Maintenance industrielle

Ce thème couvre la maintenance industrielle : formes et opérations de maintenance, TPM, fiabilité, disponibilité, maintenabilité, gestion des pièces détachées, GMAO, normes (NF EN 13306, NF X60-…), réglementation (sécurité, ICPE, ATEX) et habilitations.

C'est le thème par défaut des skills (`theme` = `maintenance`).

Les règles de structure (types d'artefacts, formats, identifiants) sont partagées avec les autres thèmes. Voir `.claude/rules/` pour les formats.

Les chemins dans les rules sont relatifs à la racine du thème (`wiki/themes/maintenance/`).

## Objectif

Transformer des sources documentaires en artefacts de connaissance typés :

- sources ;
- notes atomiques ;
- entrées de glossaire ;
- fiches normes ;
- exigences normatives ;
- processus métier ;
- objets métier ;
- règles métier (BRL) ;
- textes réglementaires (REG) ;
- habilitations (HAB) ;
- indicateurs ;
- MOC / cartes de navigation.

## Principe central

Ne jamais produire une simple archive de résumés.

Le wiki doit produire des objets de connaissance réutilisables, sourcés, liés et maintenables.

## Commandes principales

- `/source_add` : ajoute une source PDF, extrait le texte et crée une fiche source minimale.
- `/source_ingest` : analyse une source TXT et crée ou met à jour les artefacts de connaissance.
- `/wiki_lint` : vérifie la cohérence du wiki.
- `/wiki_gap` : détecte et comble les artefacts manquants après ingestion.
- `/wiki-search` : retrouve les notes répondant à un besoin exprimé librement.
- `/wiki-check-links` : liste les liens `[[...]]` cassés (zéro token, script Python autonome).

## Règles de chargement

Ne pas charger toutes les règles à chaque tâche.

Charger uniquement les règles nécessaires :

- source : `.claude/rules/source-file.md`
- identifiants : `.claude/rules/id-conventions.md`
- note atomique : `.claude/rules/atomic-note.md`
- glossaire : `.claude/rules/glossary-term.md`
- norme : `.claude/rules/norm-reference.md`
- exigence normative : `.claude/rules/normative-requirement.md`
- processus métier : `.claude/rules/business-process.md`
- objet métier : `.claude/rules/domain-object.md`
- règle métier (BRL) : `.claude/rules/business-rule.md`
- texte réglementaire (REG) : `.claude/rules/regulatory-text.md`
- habilitation (HAB) : `.claude/rules/habilitation.md`
- indicateur : `.claude/rules/indicator.md`
- MOC : `.claude/rules/moc.md`
- liens : `.claude/rules/linking-rules.md`
- ingestion : `.claude/rules/ingestion-report.md`
- chunking : `.claude/rules/chunking-strategy.md`

## Règles impératives

1. Ne jamais créer une note sans source identifiable.
2. Ne jamais créer une note atomique si elle contient plusieurs idées principales.
3. Ne jamais fusionner automatiquement deux artefacts.
4. Toujours maintenir les index JSON après création ou modification.
5. Toujours ajouter un lien contextualisé quand un lien est créé.
6. Toujours mettre à jour la fiche source après ingestion.
7. Toujours créer un rapport d'ingestion après traitement d'une source.
8. Ne jamais recopier massivement une norme, un livre ou une source protégée.
9. Préférer peu d'artefacts de bonne qualité à beaucoup de fragments médiocres. Mais si le document est volumineux, ne pas réduire artificiellement le nombre de notes de qualité pour limiter le total par document. 
10. Toujours utiliser le format `[[ID-slug]]` pour les liens internes — jamais `[[ID]]` seul ni `[texte](chemin)`.

## Langue

Tous les artefacts (notes atomiques, glossaire, processus, etc.) doivent être rédigés en **français**, même lorsque la source est en anglais. Les termes anglais d'origine sont conservés dans les champs `term`, `aliases` et `english_equivalent` du frontmatter, mais le corps des notes est en français.
