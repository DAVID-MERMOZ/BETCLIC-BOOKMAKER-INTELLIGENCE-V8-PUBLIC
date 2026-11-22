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

Le pilote de forme utilise tous les matchs confirmés disposant d'un historique d'équipe suffisant. Les noms d'équipes sont normalisés via des alias exacts documentés, tout en séparant strictement les équipes masculines, féminines, jeunes et réserves. Les caractéristiques de forme sont calculées uniquement à partir de matchs antérieurs au coup d'envoi. La collecte SportScore couvre désormais 180 jours. L'entraînement final est autorisé dès lors que les trois classes (domicile, nul, extérieur) sont représentées. Une classe singleton autorise un entraînement exploratoire, mais bloque la validation walk-forward tant que les trois classes ne sont pas présentes dans la fenêtre d'entraînement initiale : le modèle et ses sorties restent marqués expérimentaux/non validés.

L'évaluation utilise une fenêtre chronologique expanding-window (70 % initiaux, puis prédictions séquentielles sur le futur) : chaque prédiction ne voit que les observations antérieures. Aucune performance, rentabilité ou calibration n'est revendiquée avant un échantillon de test suffisant. Le système ne déclenche aucun pari automatique. Les prédictions sont produites uniquement pour les affiches futures qui disposent de l'historique d'équipe requis.

## Seuil de maturité

Le seuil de 200 matchs uniques confirmés avec cotes pré-match reste le jalon de validation statistique du modèle en production. Le recalibrage du marché 1X2 est évalué séparément sur toutes les observations 1X2 mappées, sans exiger de forme d'équipe ; il reste explicitement dépendant du marché. L'ECE top-label est publiée comme indicateur descriptif complémentaire au Brier et au log-loss. Il ne bloque pas le fonctionnement de la collecte, des alertes, du tableau de bord ni de l'entraînement exploratoire.

## Artefacts

Les rapports CSV et le modèle expérimental sont publiés comme artefacts GitHub Actions pendant 30 jours. La base SQLite est sauvegardée dans le dépôt privé via la release `betclic-db-latest`.
