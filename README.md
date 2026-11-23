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

Le pilote de forme utilise tous les matchs confirmés disposant d'un historique d'équipe suffisant. Les noms d'équipes sont normalisés via des alias exacts documentés, tout en séparant strictement les équipes masculines, féminines, jeunes et réserves. Les caractéristiques de forme sont calculées uniquement à partir de matchs antérieurs au coup d'envoi. La collecte SportScore vise une fenêtre glissante de 365 jours, reprend les dates non terminées au prochain passage et ne relie jamais automatiquement ses résultats aux matchs Betclic : chaque rapprochement reste soumis à validation humaine. L'entraînement final est autorisé dès lors que les trois classes (domicile, nul, extérieur) sont représentées. Une classe singleton autorise un entraînement exploratoire, mais bloque la validation walk-forward tant que les trois classes ne sont pas présentes dans la fenêtre d'entraînement initiale : le modèle et ses sorties restent marqués expérimentaux/non validés.

L'évaluation utilise une fenêtre chronologique expanding-window (70 % initiaux, puis prédictions séquentielles sur le futur) : chaque prédiction ne voit que les observations antérieures. Aucune performance, rentabilité ou calibration n'est revendiquée avant un échantillon de test suffisant. Le système ne déclenche aucun pari automatique. Les prédictions sont produites uniquement pour les affiches futures qui disposent de l'historique d'équipe requis.

## Seuil de maturité

Le lancement opérationnel expérimental est autorisé à partir de 30 matchs uniques confirmés disposant de cotes pré-match. Ce seuil permet de démarrer la collecte en conditions réelles, de produire des prédictions expérimentales et d'observer le comportement du moteur ; il ne prouve ni rentabilité, ni calibration, ni supériorité au marché. En dessous de 30 matchs, la collecte et le développement continuent, mais le statut reste « collecte en cours ». Le seuil de 200 matchs est conservé uniquement comme objectif ultérieur de validation statistique robuste : il ne bloque plus le lancement expérimental ni la collecte. Le recalibrage du marché 1X2 est évalué séparément sur toutes les observations 1X2 mappées, sans exiger de forme d'équipe ; il reste explicitement dépendant du marché. Les vecteurs 1X2 sont contrôlés strictement (trois classes, valeurs finies entre 0 et 1, somme égale à 1) avant le calcul des métriques et l'export des prédictions. L'ECE top-label est publiée comme indicateur descriptif complémentaire au Brier et au log-loss ; elle ne constitue pas un recalibrage. Le modèle de forme reste explicitement non calibré tant qu'un échantillon de validation suffisamment large ne permet pas de justifier une méthode de calibration hors période. Ces contrôles ne bloquent pas la collecte, les alertes, le tableau de bord ni l'entraînement exploratoire.

## Artefacts

Les rapports CSV et le modèle expérimental sont publiés comme artefacts GitHub Actions pendant 30 jours. La base SQLite est sauvegardée dans le dépôt privé via la release `betclic-db-latest`.


## Marchés buts expérimentaux (Over/Under et BTTS)

Le rapport de prédictions inclut aussi Over/Under 1.5, 2.5 et 3.5 ainsi que BTTS Oui/Non. Ces probabilités proviennent d'une matrice de scores Poisson construite à partir des buts marqués/encaissés avant le coup d'envoi, avec séparation domicile/extérieur et rétrécissement vers la moyenne globale disponible. Il faut au moins trois matchs antérieurs pour chaque équipe et dix matchs antérieurs dans le corpus pour produire une estimation. Sinon, les champs restent vides avec un statut explicite NO_BET. Les sorties sont marquées EXPERIMENTAL_NOT_VALIDATED : elles ne sont ni calibrées, ni comparées à des cotes de marché, et ne constituent pas des recommandations de pari. Les marchés buts doivent être évalués séparément sur des résultats confirmés.
