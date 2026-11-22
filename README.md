# Betclic Bookmaker Intelligence V8.1

Moteur de collecte et d'analyse des cotes Betclic, conservation privée de l'historique, rapprochement des résultats, alertes de mouvements de cotes et pilote prédictif expérimental.

## Architecture et confidentialité

- Dépôt public : code source et workflow uniquement.
- Dépôt privé `DAVID-MERMOZ/BETCLIC-BOOKMAKER-INTELLIGENCE-V8` : base SQLite et sauvegarde durable.
- GitHub Actions : collecte complète horaire à la minute 30 UTC.
- Workflow pilote séparé : tests et entraînement depuis la dernière sauvegarde privée, sans relancer la collecte Betclic.
- Secrets requis dans les Actions du runner public : `PRIVATE_REPO_TOKEN` et `FOOTBALL_DATA_API_TOKEN`.
- Ne jamais ajouter la base SQLite, les historiques bruts ou les secrets au dépôt public.

## Exécution du tableau de bord local

Le tableau de bord Streamlit lit la base SQLite en lecture seule. Il ne téléverse aucune donnée et ne modifie pas l'historique.

```bash
pip install -r requirements.txt
streamlit run dashboard.py
```

La base privée doit être disponible localement dans `data/betclic_history.db`. Le tableau de bord affiche les volumes, matchs à venir, alertes, historiques de cotes et éventuelles prédictions publiées.

## Pilote prédictif

Le pilote de forme utilise les 15 matchs admissibles disponibles et les caractéristiques de forme calculées uniquement à partir de matchs antérieurs au coup d'envoi. L'entraînement final est autorisé dès lors que les trois classes (domicile, nul, extérieur) sont représentées. Une classe singleton autorise un entraînement exploratoire, mais interdit la validation croisée : le modèle et ses sorties restent marqués expérimentaux/non validés.

Aucune performance, rentabilité ou calibration n'est revendiquée sur cet échantillon. Le système ne déclenche aucun pari automatique. Les prédictions sont produites uniquement pour les affiches futures qui disposent de l'historique d'équipe requis.

## Seuil de maturité

Le seuil de 200 matchs uniques confirmés avec cotes pré-match reste le jalon de validation statistique du modèle en production. Il ne bloque pas le fonctionnement de la collecte, des alertes, du tableau de bord ni de l'entraînement exploratoire.

## Artefacts

Les rapports CSV et le modèle expérimental sont publiés comme artefacts GitHub Actions pendant 30 jours. La base SQLite est sauvegardée dans le dépôt privé via la release `betclic-db-latest`.
