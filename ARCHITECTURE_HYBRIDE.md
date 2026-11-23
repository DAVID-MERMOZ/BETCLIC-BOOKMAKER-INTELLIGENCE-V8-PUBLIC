# Architecture hybride — Betclic Bookmaker Intelligence V8.1

## Objectif

Conserver la fraîcheur des informations utiles aux prédictions pendant la journée sans exécuter à chaque contrôle toute la chaîne lourde (restauration SQLite, collecte historique, entraînement, audits, rapports et sauvegarde).

Cette architecture sépare **l'acquisition des changements**, **le recalcul ciblé** et **l'apprentissage historique**. Elle ne promet ni surveillance continue ni disponibilité d'une information que les fournisseurs ne publient pas.

## 1. Vue d'ensemble

```text
Sources disponibles
  ├─ Fixtures / horaires
  ├─ Cotes et marchés
  └─ Compositions, absences et événements (uniquement si une source fiable les expose)
          |
          v
Collecte légère et ciblée
  ├─ Enregistre un instantané horodaté
  ├─ Compare avec le dernier instantané
  └─ Signale seulement les changements significatifs
          |
          v
Mise à jour du match concerné
  ├─ Actualise ses données
  ├─ Recalcule ses probabilités si les entrées ont changé
  └─ Marque la prédiction comme périmée si une donnée essentielle manque
          |
          +------------------------------+
          |                              |
          v                              v
Dashboard / notification          Historique SQLite privé
                                         |
                                         v
Pipeline lourd quotidien / manuel
  ├─ Contrôles qualité et intégrité
  ├─ Évaluation walk-forward et benchmark
  ├─ Entraînement expérimental
  ├─ Rapport de résultats
  └─ Sauvegarde durable privée
```

## 2. Trois niveaux d'exécution

### A. Surveillance légère — pendant la journée

- Cible seulement les matchs à venir et les matchs dont le coup d'envoi approche.
- Interroge les fournisseurs à une fréquence raisonnable, avec temporisation et respect de leurs limites.
- Enregistre chaque observation avec son horodatage UTC, sa source et son identifiant de match.
- Compare les instantanés : aucun recalcul si les données sont identiques.
- Déclenche une actualisation du match uniquement après un changement utile : cote, marché, horaire, statut du match ou composition confirmée.
- Une composition absente ou non confirmée reste « inconnue » ; elle ne doit jamais être inventée ni interprétée comme une composition inchangée.

**Cadence de départ proposée après vérification des quotas :**
- matchs éloignés du coup d'envoi : contrôle espacé (par exemple toutes les 3 à 6 heures) ;
- fenêtre pré-match : contrôles plus rapprochés uniquement si le fournisseur autorise cette fréquence et si les données sont réellement disponibles ;
- après le coup d'envoi : arrêter les mises à jour pré-match et basculer vers un statut live séparé.

Un cron GitHub Actions ne constitue pas un service temps réel : il peut être retardé et chaque exécution consomme des minutes. La cadence ci-dessus est une politique cible, pas une fréquence actuellement active.

### B. Recalcul ciblé — sur changement

- Ne recalcule que le match touché, pas tous les matchs du jour.
- Conserve la prédiction précédente, la nouvelle prédiction et les données qui ont changé.
- Affiche l'heure de dernière actualisation et l'âge de chaque donnée importante.
- Si les informations sont trop anciennes, contradictoires ou incomplètes, affiche « données à actualiser » et bloque toute conclusion exploitable.
- Les probabilités du modèle restent expérimentales tant que la validation hors période et la calibration ne démontrent pas le contraire.

### C. Pipeline lourd — une fois par jour ou à la demande

Réserve les tâches coûteuses à un seul passage quotidien ou à un lancement manuel :
- restauration et vérification de la base privée ;
- collecte/réconciliation des résultats terminés ;
- audit de qualité et de disponibilité des données ;
- benchmark du marché et évaluation walk-forward ;
- entraînement expérimental ;
- génération des rapports et échantillons quotidiens ;
- sauvegarde SQLite vers la release privée.

Les tests du code doivent être déclenchés par les changements de code, mais ils ne doivent pas lancer inutilement une collecte complète ni réentraîner le modèle.

## 3. Gestion des changements et traçabilité

Chaque instantané doit au minimum conserver :
- identifiant du match et source ;
- type de donnée (cote, horaire, composition, absence, statut) ;
- valeur observée et horodatage UTC ;
- statut de confirmation et éventuelle raison d'indisponibilité ;
- empreinte de la donnée normalisée pour détecter les doublons.

Les mises à jour doivent être idempotentes : rejouer le même instantané ne doit pas créer de doublons ni écraser un instantané plus récent. Une modification de composition doit être reliée à sa source et à son heure de publication.

## 4. Protection de la base privée

- La base `data/betclic_history.db` ne doit jamais être commitée dans le dépôt public.
- La release privée `betclic-db-latest` reste la source de sauvegarde durable.
- Toute tâche qui écrit dans cette base doit utiliser un verrou/concurrency commun pour éviter deux restaurations, écritures ou sauvegardes simultanées.
- Les tâches de lecture et de calcul doivent ouvrir SQLite en lecture seule autant que possible.
- Ne jamais remplacer la sauvegarde privée par une base vide ou par une copie incomplète.
- En cas d'échec de restauration, arrêter le traitement et préserver la sauvegarde existante.

## 5. Déploiement progressif et critères d'acceptation

### Étape 1 — économie immédiate
- Vérifier le quota réel GitHub Actions restant avant de changer les fréquences.
- Éviter les exécutions horaires de la chaîne lourde.
- Ajouter le cache pip et limiter les déclenchements push aux fichiers qui nécessitent réellement le pipeline concerné.
- Conserver `workflow_dispatch` pour les contrôles manuels.

### Étape 2 — collecteur léger
À implémenter uniquement après confirmation des fournisseurs et des champs réellement accessibles :
- collecteur incrémental séparé de `betclic_probe.py` ;
- écriture d'instantanés horodatés sans réentraîner le modèle ;
- détection de changements et actualisation ciblée ;
- limites de fréquence, reprise sur erreur et journalisation.

### Étape 3 — recalcul et notifications
- recalcul uniquement sur changement significatif ;
- notification seulement si la prédiction a réellement changé ou si une information importante est confirmée ;
- inclure la fraîcheur des données dans chaque notification.

### Étape 4 — apprentissage et contrôle
- apprentissage quotidien ou après un volume suffisant de nouveaux résultats confirmés ;
- comparaison au benchmark du marché sur les mêmes matchs ;
- aucune revendication de performance sans échantillon hors période suffisant ;
- maintien de « NO BET » lorsque les contrôles sont insuffisants.

## 6. Limites connues à ce stade

- Le workflow principal actuel exécute encore une chaîne complète sur son déclencheur planifié horaire.
- Le workflow pilote restaure la base et lance le collecteur Betclic avant de produire l'échantillon : ce n'est pas un collecteur léger.
- Les scripts existants calculent des probabilités expérimentales et exploitent les cotes disponibles ; la présence d'un module de recherche d'actualités ne prouve pas qu'une source fiable de compositions officielles soit intégrée.
- Aucune surveillance permanente ni mise à jour de composition en temps réel n'est considérée comme opérationnelle avant d'avoir un collecteur dédié, une source vérifiée et des exécutions réussies.

**Règle de sécurité :** mieux vaut signaler explicitement une donnée manquante ou ancienne que présenter une prédiction comme actualisée alors qu'elle ne l'est pas.
