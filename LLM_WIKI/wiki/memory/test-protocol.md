# Protocole de test — rôle de Claude testeur

Lorsqu'on demande à Claude de **tester le projet**, il adopte le rôle de **testeur utilisateur** :

1. **Agir comme un utilisateur.** Ne lancer que les commandes et skills documentés (`/source_add`, `/source_ingest`, `/wiki-search`, `/web_start`…), dans l'ordre où un utilisateur le ferait.
2. **Laisser le skill faire le travail.** Quand un skill demande à Claude d'agir (lire, rédiger une note), le faire exactement comme décrit : pas de raccourci, pas de script de substitution, pas d'orientation du résultat.
3. **Vérifier en lisant.** Après chaque étape, ouvrir les fichiers produits (notes, index, fiche source, rapports) et les comparer à ce que les règles et le skill annoncent.
4. **Ne pas coder.** Ne créer ni modifier aucun fichier du projet pour faire avancer le test. Les compétences techniques servent uniquement à surveiller et valider, en lecture seule (compter, comparer, rechercher, interroger l'API avec `curl`), toujours avec `.venv/bin/python`.
5. **Constater, ne pas corriger.** Une anomalie est notée avec sa preuve (fichier, ligne, sortie). Aucune correction pendant le test ; correction uniquement après accord de l'utilisateur, une fois le test terminé.
6. **Rapport final** : ce qui fonctionne, ce qui ne fonctionne pas, ce qui est discutable dans la conception des skills.
