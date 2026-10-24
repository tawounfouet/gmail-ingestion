# GMAIL_INGESTION_REQUIREMENTS_ANALYSIS

## 0. Document Control

| Champ | Valeur |
|---|---|
| Document | `GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md` |
| Domaine | Data Engineering / Data Ingestion |
| Source | Gmail / Google Workspace |
| Framework cible | PyIngestKit |
| Statut | Requirements Analysis |
| Version documentaire | 1.0 |
| Date de référence | 2026-10-24 |

---

# 1. Objet du document

Ce document formalise l’analyse détaillée des exigences du connecteur **Gmail Ingestion**.

Il transforme l’expression du besoin définie dans :

```text
00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN.md
```

en un ensemble structuré d’exigences :

- fonctionnelles ;
- techniques ;
- data ;
- sécurité ;
- conformité ;
- performance ;
- observabilité ;
- exploitation ;
- résilience ;
- compatibilité PyIngestKit.

L’objectif est de disposer d’un référentiel suffisamment précis pour permettre ensuite :

```text
Requirements
    ↓
Architecture
    ↓
Data Model
    ↓
Release Strategy
    ↓
Implementation
    ↓
Qualification
```

Ce document ne décrit pas encore l’implémentation détaillée.

Il définit **ce que la solution doit garantir**.

---

# 2. Résumé exécutif

Le connecteur Gmail doit permettre de transformer une boîte Gmail en une source de données :

```text
observable
+
incremental
+
versioned
+
reproducible
+
auditable
+
recoverable
```

La difficulté principale ne réside pas dans la simple récupération d’e-mails.

Gmail combine plusieurs caractéristiques :

- données documentaires ;
- données relationnelles ;
- messages MIME ;
- pièces jointes binaires ;
- conversations ;
- labels mutables ;
- historique événementiel ;
- synchronisation incrémentale ;
- authentification OAuth ;
- données personnelles potentiellement sensibles ;
- quotas API ;
- notifications push pouvant être perdues.

Le connecteur doit donc être conçu comme un **système de synchronisation de datasets**, et non comme un script de téléchargement.

Le modèle directeur retenu est :

```text
Gmail API
    ↓
Discovery
    ↓
Extraction
    ↓
RAW Preservation
    ↓
Normalization
    ↓
Validation
    ↓
Versioning
    ↓
Publication
    ↓
Reconciliation
```

---

# 3. Principes directeurs

Les exigences doivent être évaluées selon l’ordre de priorité suivant :

```text
1. Correctness
2. Data Safety
3. Recoverability
4. Reproducibility
5. Security
6. Incrementality
7. Observability
8. Performance
9. Analytics
```

Les optimisations de performance ne doivent jamais compromettre :

- la complétude ;
- l’idempotence ;
- la traçabilité ;
- la capacité de reprise.

---

# 4. Terminologie

## 4.1 Mailbox

Boîte Gmail associée à un utilisateur.

---

## 4.2 Message

E-mail individuel identifié par un identifiant Gmail stable.

---

## 4.3 Thread

Conversation Gmail regroupant plusieurs messages.

---

## 4.4 Label

Classification Gmail appliquée à un message ou un thread.

---

## 4.5 History Record

Événement représentant un changement survenu dans la boîte Gmail.

---

## 4.6 historyId

Identifiant croissant de l’historique Gmail utilisé comme curseur de synchronisation.

Il ne doit pas être interprété comme une séquence continue.

---

## 4.7 Full Sync

Synchronisation complète d’un périmètre Gmail.

---

## 4.8 Partial / Incremental Sync

Synchronisation des changements depuis un `historyId`.

---

## 4.9 RAW

Représentation la plus fidèle possible de la donnée source.

---

## 4.10 Normalized

Représentation structurée destinée aux traitements analytiques et applicatifs.

---

## 4.11 Checkpoint

État confirmé permettant de reprendre une ingestion sans relire inutilement toute la source.

---

## 4.12 Manifest

Métadonnées décrivant précisément une exécution d’ingestion.

---

## 4.13 Published Dataset

Version du dataset qualifiée et exposée comme version de référence.

---

# 5. Périmètre fonctionnel

Le connecteur doit prendre en charge les ressources suivantes.

```text
Gmail Account
├── Profile
├── Labels
├── Messages
│   ├── Headers
│   ├── Payload
│   ├── MIME Parts
│   ├── Text Body
│   ├── HTML Body
│   └── Attachments
├── Threads
└── History
```

---

# 6. Exigences fonctionnelles

# 6.1 Authentification

### GMAIL-FR-AUTH-001 — OAuth 2.0

Le connecteur DOIT utiliser OAuth 2.0 pour l’accès aux comptes Gmail standards.

### GMAIL-FR-AUTH-002 — Least Privilege

Le connecteur DOIT demander uniquement les scopes nécessaires au mode d’ingestion configuré.

### GMAIL-FR-AUTH-003 — Read-only First

Le mode d’ingestion standard DOIT utiliser un accès en lecture seule.

### GMAIL-FR-AUTH-004 — Credential Isolation

Les credentials d’un compte NE DOIVENT PAS être réutilisés implicitement pour un autre compte.

### GMAIL-FR-AUTH-005 — Token Refresh

Le connecteur DOIT gérer le renouvellement des tokens OAuth lorsque possible.

### GMAIL-FR-AUTH-006 — Revoked Credentials

La révocation ou l’expiration définitive des credentials DOIT produire une erreur explicite et non un échec silencieux.

---

# 6.2 Profil Gmail

### GMAIL-FR-PROFILE-001

Le connecteur DOIT pouvoir récupérer le profil Gmail du compte authentifié.

Les propriétés minimales sont :

```text
email_address
messages_total
threads_total
history_id
```

### GMAIL-FR-PROFILE-002

Le profil DOIT être enregistré dans le manifest ou dans un dataset de métadonnées d’exécution.

### GMAIL-FR-PROFILE-003

L’adresse du compte DOIT être utilisée comme dimension d’isolation logique du dataset.

---

# 6.3 Labels

### GMAIL-FR-LABEL-001

Le connecteur DOIT récupérer les labels Gmail.

### GMAIL-FR-LABEL-002

Il DOIT distinguer :

```text
SYSTEM
USER
```

lorsque cette information est disponible.

### GMAIL-FR-LABEL-003

Les relations message ↔ label DOIVENT être conservées séparément du message lorsque le modèle normalisé l’exige.

### GMAIL-FR-LABEL-004

Un changement de label DOIT être considéré comme une mutation de l’état du message.

### GMAIL-FR-LABEL-005

Les suppressions de labels NE DOIVENT PAS provoquer la perte silencieuse de l’historique local.

---

# 6.4 Découverte des messages

### GMAIL-FR-MSG-001

Le connecteur DOIT supporter la pagination de `messages.list`.

### GMAIL-FR-MSG-002

Il DOIT être capable de parcourir toutes les pages du périmètre demandé.

### GMAIL-FR-MSG-003

Il DOIT supporter l’utilisation de requêtes Gmail lorsque le mode filtré est activé.

Exemples :

```text
after:2026/01/01
before:2026/02/01
from:example.org
has:attachment
label:important
```

### GMAIL-FR-MSG-004

Le connecteur DOIT pouvoir configurer l’inclusion ou l’exclusion de Spam et Trash.

### GMAIL-FR-MSG-005

La liste des identifiants découverts DOIT pouvoir être persistée avant la phase de récupération détaillée.

Cela doit permettre :

```text
discovery
    ↓
checkpoint intermédiaire
    ↓
fetch
```

---

# 6.5 Récupération des messages

### GMAIL-FR-MSG-010

Chaque message découvert DOIT pouvoir être récupéré individuellement.

### GMAIL-FR-MSG-011

Le connecteur DOIT supporter au minimum les formats :

```text
minimal
metadata
full
raw
```

selon les besoins du pipeline.

### GMAIL-FR-MSG-012

Le format utilisé DOIT être enregistré dans le manifest d’exécution.

### GMAIL-FR-MSG-013

Les données RAW NE DOIVENT PAS être reconstruites artificiellement à partir du modèle normalisé.

### GMAIL-FR-MSG-014

Les champs Gmail suivants DOIVENT être conservés lorsqu’ils sont disponibles :

```text
id
threadId
labelIds
snippet
historyId
internalDate
payload
sizeEstimate
raw
```

### GMAIL-FR-MSG-015

Les messages sans body exploitable NE DOIVENT PAS être considérés comme invalides si l’API les considère valides.

---

# 6.6 Headers

### GMAIL-FR-HDR-001

Le connecteur DOIT préserver les headers Gmail reçus dans le payload.

### GMAIL-FR-HDR-002

Il DOIT normaliser au minimum :

```text
From
To
Cc
Bcc
Reply-To
Subject
Date
Message-ID
In-Reply-To
References
Content-Type
```

### GMAIL-FR-HDR-003

Les headers inconnus NE DOIVENT PAS être supprimés de la couche RAW.

### GMAIL-FR-HDR-004

Les headers multiples portant le même nom DOIVENT pouvoir être représentés.

---

# 6.7 Corps des messages

### GMAIL-FR-BODY-001

Le connecteur DOIT pouvoir extraire les contenus `text/plain`.

### GMAIL-FR-BODY-002

Le connecteur DOIT pouvoir extraire les contenus `text/html`.

### GMAIL-FR-BODY-003

L’absence de `text/plain` NE DOIT PAS entraîner la perte du contenu HTML.

### GMAIL-FR-BODY-004

Le connecteur DOIT préserver la structure MIME d’origine dans la couche RAW.

### GMAIL-FR-BODY-005

Le connecteur NE DOIT PAS supposer que le body principal se trouve au premier niveau du payload.

### GMAIL-FR-BODY-006

Les parties MIME imbriquées DOIVENT être parcourues récursivement.

---

# 6.8 Pièces jointes

### GMAIL-FR-ATT-001

Le connecteur DOIT détecter les pièces jointes.

### GMAIL-FR-ATT-002

Le téléchargement des pièces jointes DOIT être configurable.

### GMAIL-FR-ATT-003

Le connecteur DOIT permettre un mode :

```text
metadata_only
```

sans récupération du contenu binaire.

### GMAIL-FR-ATT-004

Lorsqu’une pièce jointe est téléchargée, les métadonnées suivantes DOIVENT être enregistrées :

```text
attachment_id
message_id
filename
mime_type
size
content_hash
storage_uri
```

### GMAIL-FR-ATT-005

Le contenu binaire NE DOIT PAS être stocké directement dans les tables analytiques.

### GMAIL-FR-ATT-006

Le système DOIT pouvoir calculer un hash de contenu pour détecter les doublons.

### GMAIL-FR-ATT-007

Une erreur de téléchargement d’une pièce jointe DOIT être identifiable sans nécessairement invalider tout le snapshot, selon la politique de qualité configurée.

---

# 6.9 Threads

### GMAIL-FR-THREAD-001

Le connecteur DOIT conserver `threadId` sur chaque message.

### GMAIL-FR-THREAD-002

Le connecteur DOIT pouvoir produire un dataset de threads.

### GMAIL-FR-THREAD-003

Les messages d’un thread DOIVENT pouvoir être ordonnés chronologiquement.

### GMAIL-FR-THREAD-004

Le dataset de threads NE DOIT PAS remplacer le dataset de messages.

Les deux niveaux doivent rester disponibles.

---

# 6.10 Full Snapshot

### GMAIL-FR-SYNC-001

Le connecteur DOIT supporter une synchronisation complète.

### GMAIL-FR-SYNC-002

Une Full Sync DOIT pouvoir être utilisée :

- lors de la première ingestion ;
- après perte de checkpoint ;
- après invalidation du `historyId` ;
- lors d’une réconciliation majeure ;
- sur demande explicite.

### GMAIL-FR-SYNC-003

La Full Sync DOIT produire une version indépendante et identifiable.

### GMAIL-FR-SYNC-004

La Full Sync DOIT pouvoir être limitée à un périmètre configuré.

---

# 6.11 Incremental Sync

### GMAIL-FR-SYNC-010

Le connecteur DOIT supporter une synchronisation partielle via `history.list`.

### GMAIL-FR-SYNC-011

Il DOIT conserver un `startHistoryId` confirmé.

### GMAIL-FR-SYNC-012

Le checkpoint NE DOIT être avancé qu’après persistance réussie de l’ensemble des changements associés.

### GMAIL-FR-SYNC-013

Les événements Gmail suivants DOIVENT être pris en charge lorsque retournés :

```text
messageAdded
messageDeleted
labelAdded
labelRemoved
```

### GMAIL-FR-SYNC-014

Les `historyId` NE DOIVENT PAS être supposés contigus.

### GMAIL-FR-SYNC-015

Les résultats `history.list` DOIVENT être traités dans leur ordre logique.

### GMAIL-FR-SYNC-016

Un `HTTP 404` lié à un `startHistoryId` trop ancien ou invalide DOIT déclencher la stratégie de resynchronisation configurée.

Par défaut :

```text
historyId invalid
      ↓
mark checkpoint invalid
      ↓
full synchronization
      ↓
new checkpoint
```

---

# 6.12 Search / Range Ingestion

### GMAIL-FR-FILTER-001

Le connecteur DOIT pouvoir ingérer uniquement une plage temporelle.

### GMAIL-FR-FILTER-002

Il DOIT supporter les requêtes Gmail `q` lorsque le scope utilisé l’autorise.

### GMAIL-FR-FILTER-003

Les filtres appliqués DOIVENT être inscrits dans le manifest.

### GMAIL-FR-FILTER-004

Deux datasets générés avec des filtres différents DOIVENT être distinguables.

---

# 6.13 Push / CDC

### GMAIL-FR-CDC-001

Le connecteur DEVRA pouvoir intégrer `users.watch`.

### GMAIL-FR-CDC-002

Le canal de notifications DEVRA utiliser Google Cloud Pub/Sub conformément au modèle Gmail.

### GMAIL-FR-CDC-003

Une notification push NE DOIT PAS être considérée comme le changement métier complet.

Elle doit servir de signal pour déclencher :

```text
notification
    ↓
historyId
    ↓
history.list
    ↓
actual changes
```

### GMAIL-FR-CDC-004

Le système DOIT tolérer :

- notifications dupliquées ;
- notifications tardives ;
- notifications perdues ;
- notifications réordonnées.

### GMAIL-FR-CDC-005

Le `watch` Gmail DOIT être renouvelé avant expiration.

### GMAIL-FR-CDC-006

Le renouvellement automatique DEVRA être planifié avec une marge suffisante.

### GMAIL-FR-CDC-007

Le connecteur DOIT prévoir un fallback de polling / reconciliation périodique.

---

# 6.14 Suppressions

### GMAIL-FR-DEL-001

Les suppressions détectées DOIVENT être historisées.

### GMAIL-FR-DEL-002

Une suppression source NE DOIT PAS provoquer automatiquement la suppression physique du RAW historique.

### GMAIL-FR-DEL-003

Le modèle normalisé DOIT pouvoir représenter :

```text
is_deleted
deleted_at
deletion_history_id
```

ou un mécanisme équivalent.

### GMAIL-FR-DEL-004

Une politique de purge physique distincte DOIT pouvoir être appliquée.

---

# 7. Exigences de données

# 7.1 Identifiants

### GMAIL-DR-ID-001

`message_id` DOIT être traité comme identifiant source stable.

### GMAIL-DR-ID-002

`thread_id` DOIT être conservé indépendamment de `message_id`.

### GMAIL-DR-ID-003

`history_id` DOIT être stocké comme chaîne ou entier de précision suffisante, sans risque de troncature.

### GMAIL-DR-ID-004

La clé logique du message normalisé DOIT inclure le compte source.

Exemple :

```text
(account_id, message_id)
```

---

# 7.2 Timestamps

### GMAIL-DR-TIME-001

`internalDate` DOIT être converti vers une représentation temporelle normalisée.

### GMAIL-DR-TIME-002

La valeur source brute DOIT rester disponible.

### GMAIL-DR-TIME-003

Les dates issues des headers NE DOIVENT PAS remplacer `internalDate`.

### GMAIL-DR-TIME-004

Les timestamps normalisés DOIVENT être stockés en UTC.

---

# 7.3 Adresses e-mail

### GMAIL-DR-EMAIL-001

Les adresses présentes dans :

```text
From
To
Cc
Bcc
Reply-To
```

DOIVENT être normalisées.

### GMAIL-DR-EMAIL-002

Les noms d’affichage et adresses DOIVENT être distingués.

### GMAIL-DR-EMAIL-003

Une adresse e-mail DOIT pouvoir apparaître plusieurs fois dans différents rôles.

### GMAIL-DR-EMAIL-004

La résolution ultérieure vers une personne ou une organisation DOIT rester une étape d’enrichissement séparée.

---

# 7.4 Modèle normalisé minimal

Le dataset `messages` DEVRA au minimum pouvoir représenter :

```text
account_id
message_id
thread_id
history_id
internal_date
from_address
subject
snippet
mime_type
size_estimate
text_body
html_body
has_attachments
attachment_count
is_deleted
ingested_at
source_version
```

Les relations multi-valuées doivent être modélisées séparément.

Exemple :

```text
message_recipients
message_labels
message_headers
message_attachments
```

---

# 7.5 RAW Preservation

### GMAIL-DR-RAW-001

Le JSON source récupéré DOIT pouvoir être conservé.

### GMAIL-DR-RAW-002

Le format `raw` Gmail DOIT pouvoir être stocké lorsqu’il est demandé.

### GMAIL-DR-RAW-003

La couche RAW DOIT être immuable après publication, hors opérations explicites de gouvernance.

### GMAIL-DR-RAW-004

Les transformations normalisées DOIVENT pouvoir être rejouées depuis le RAW sans nouvel appel Gmail.

---

# 8. Exigences de stockage

### GMAIL-SR-001

Le connecteur DOIT utiliser l’abstraction de stockage de PyIngestKit.

### GMAIL-SR-002

Le connecteur DOIT fonctionner au minimum avec un filesystem local.

### GMAIL-SR-003

Le connecteur DEVRA supporter un stockage objet compatible S3.

### GMAIL-SR-004

Les pièces jointes DOIVENT être stockées séparément des fichiers Parquet.

### GMAIL-SR-005

Les URI de stockage DOIVENT être indépendantes du chemin physique lorsque l’abstraction PyIngestKit le permet.

### GMAIL-SR-006

Le système DOIT pouvoir publier une version de dataset via `PublishedDataset`.

---

# 9. Versioning

### GMAIL-VR-001

Chaque ingestion qualifiée DOIT être identifiable par une version de dataset.

### GMAIL-VR-002

Une version DOIT référencer :

```text
run_id
source_account
connector_version
ingestion_mode
input_checkpoint
output_checkpoint
manifest
```

### GMAIL-VR-003

Le dataset publié NE DOIT être remplacé qu’après validation réussie de la nouvelle version.

### GMAIL-VR-004

Une version échouée NE DOIT PAS devenir `PublishedDataset`.

### GMAIL-VR-005

Le système DOIT permettre de retrouver la version publiée précédemment.

---

# 10. Checkpointing

### GMAIL-CR-001

Le checkpoint DOIT être persistant.

### GMAIL-CR-002

Il DOIT être associé au compte Gmail.

### GMAIL-CR-003

Le système DOIT distinguer :

```text
input_checkpoint
candidate_checkpoint
committed_checkpoint
```

### GMAIL-CR-004

Le `candidate_checkpoint` NE DOIT devenir `committed_checkpoint` qu’après succès.

### GMAIL-CR-005

En cas de crash, le run suivant DOIT repartir depuis le dernier checkpoint confirmé.

---

# 11. Idempotence

### GMAIL-IR-001

Le retraitement du même message NE DOIT PAS créer de doublon logique.

### GMAIL-IR-002

Le retraitement d’un même événement d’historique DOIT être sûr.

### GMAIL-IR-003

Les pièces jointes identiques NE DOIVENT PAS être dupliquées inutilement lorsque le stockage dédupliqué est activé.

### GMAIL-IR-004

Le pipeline DOIT être compatible avec une stratégie `at-least-once`.

L’idempotence doit absorber les répétitions.

---

# 12. Réconciliation

### GMAIL-RR-001

Une commande de réconciliation DOIT être disponible.

### GMAIL-RR-002

Elle DOIT pouvoir comparer au minimum :

```text
source message count
local message count
source thread count
local thread count
missing message ids
unexpected message ids
attachment consistency
label consistency
```

### GMAIL-RR-003

La réconciliation DOIT produire un rapport.

### GMAIL-RR-004

Elle DOIT classifier les écarts.

Exemple :

```text
INFO
WARNING
REPAIRABLE
RESYNC_REQUIRED
```

### GMAIL-RR-005

La réparation automatique NE DOIT être effectuée que pour les écarts explicitement autorisés.

---

# 13. Gestion des erreurs

### GMAIL-ER-001

Les erreurs DOIVENT être classées au minimum en :

```text
AUTHENTICATION
AUTHORIZATION
RATE_LIMIT
TRANSIENT_NETWORK
SOURCE_NOT_FOUND
INVALID_CHECKPOINT
PARSING
STORAGE
DATA_QUALITY
INTERNAL
```

### GMAIL-ER-002

Les erreurs temporaires DOIVENT être retryables.

### GMAIL-ER-003

Les erreurs permanentes NE DOIVENT PAS être rejouées indéfiniment.

### GMAIL-ER-004

Les retries DOIVENT utiliser un backoff exponentiel avec jitter.

### GMAIL-ER-005

Un nombre maximum de retries DOIT être configurable.

### GMAIL-ER-006

Les messages définitivement en erreur DOIVENT pouvoir être placés dans une Dead Letter / quarantine logique.

---

# 14. Quotas et efficacité API

Depuis mai 2026, Google documente des quotas Gmail basés sur des unités par minute par projet et par utilisateur/projet, ainsi qu’une consommation variable selon la méthode.

La conception NE DOIT PAS dépendre de valeurs hardcodées.

### GMAIL-QR-001

Les limites de quota DOIVENT être externalisables.

### GMAIL-QR-002

Le connecteur DOIT limiter sa concurrence.

### GMAIL-QR-003

Le connecteur DOIT mesurer :

```text
api_calls
quota_units_estimated
retries
rate_limit_errors
```

### GMAIL-QR-004

Les méthodes coûteuses telles que :

```text
messages.get
messages.attachments.get
threads.get
watch
```

DOIVENT être utilisées uniquement lorsque nécessaires.

### GMAIL-QR-005

Le cache local DOIT éviter les appels inutiles.

### GMAIL-QR-006

La stratégie de batch DOIT être configurable lorsque supportée.

---

# 15. Performance

### GMAIL-PR-001

Le pipeline DOIT permettre une concurrence bornée.

### GMAIL-PR-002

Le nombre de workers DOIT être configurable.

### GMAIL-PR-003

La concurrence NE DOIT PAS dépasser les capacités de quota définies.

### GMAIL-PR-004

Les grosses pièces jointes NE DOIVENT PAS bloquer le traitement de tous les messages.

### GMAIL-PR-005

L’ingestion DEVRA supporter le streaming vers le stockage lorsqu’il est approprié.

### GMAIL-PR-006

La phase de découverte et la phase de fetch DEVRAIENT pouvoir être découplées.

---

# 16. Observabilité

### GMAIL-OR-001

Chaque exécution DOIT avoir un `run_id`.

### GMAIL-OR-002

Les logs DOIVENT être structurés.

### GMAIL-OR-003

Les logs DOIVENT inclure lorsque pertinent :

```text
run_id
account_id
message_id
thread_id
history_id
dataset_version
```

### GMAIL-OR-004

Les métriques minimales sont :

```text
messages_discovered
messages_fetched
messages_failed
messages_deleted
threads_processed
attachments_discovered
attachments_fetched
attachments_failed
bytes_downloaded
api_calls
retries
rate_limit_events
history_events_processed
run_duration_seconds
checkpoint_lag
```

### GMAIL-OR-005

Les secrets et contenus d’e-mails NE DOIVENT PAS être écrits dans les logs par défaut.

### GMAIL-OR-006

Un résumé de run DOIT être disponible.

---

# 17. Manifest

### GMAIL-MR-001

Chaque run DOIT produire un manifest.

### GMAIL-MR-002

Le manifest DOIT contenir au minimum :

```text
schema_version
connector_name
connector_version
run_id
account_id
started_at
completed_at
status
mode
filters
message_format
include_spam_trash
download_attachments
input_checkpoint
candidate_checkpoint
committed_checkpoint
messages_discovered
messages_fetched
messages_failed
attachments_fetched
bytes_downloaded
dataset_version
errors
warnings
```

### GMAIL-MR-003

Le manifest DOIT être sérialisable en JSON.

### GMAIL-MR-004

Le manifest DOIT pouvoir être versionné indépendamment du schéma métier.

---

# 18. Sécurité

# 18.1 OAuth Scopes

Google classe actuellement `gmail.readonly` et `gmail.metadata` parmi les scopes restreints.

Cette contrainte doit être prise en compte dès la conception.

### GMAIL-SEC-001

Le connecteur DOIT appliquer le principe du moindre privilège.

### GMAIL-SEC-002

Le scope `https://mail.google.com/` NE DOIT PAS être demandé pour une ingestion read-only.

### GMAIL-SEC-003

La configuration des scopes DOIT être explicite.

### GMAIL-SEC-004

Le système DOIT documenter les implications de vérification OAuth et de sécurité lorsqu’un connecteur est distribué à des tiers.

---

# 18.2 Secrets

### GMAIL-SEC-010

Les fichiers suivants NE DOIVENT PAS être versionnés :

```text
credentials.json
token.json
.env
```

### GMAIL-SEC-011

Les secrets DOIVENT être injectables via variables d’environnement ou Secret Manager.

### GMAIL-SEC-012

Les tokens persistés DOIVENT être protégés au repos.

### GMAIL-SEC-013

Le système DOIT permettre la révocation d’un compte.

---

# 18.3 Données

### GMAIL-SEC-020

Le contenu des messages DOIT être considéré comme sensible par défaut.

### GMAIL-SEC-021

Les pièces jointes DOIVENT hériter du même niveau de protection.

### GMAIL-SEC-022

Le stockage objet DOIT être privé par défaut.

### GMAIL-SEC-023

Les URLs publiques permanentes vers des pièces jointes sont interdites par défaut.

### GMAIL-SEC-024

Les données DOIVENT être chiffrées en transit.

### GMAIL-SEC-025

Le chiffrement au repos DEVRA être activable.

---

# 19. Confidentialité et gouvernance

### GMAIL-GOV-001

Le système DOIT permettre une politique de rétention.

### GMAIL-GOV-002

Le système DOIT permettre la suppression complète des données d’un compte source.

### GMAIL-GOV-003

La suppression de gouvernance DOIT être distincte d’une suppression Gmail métier.

### GMAIL-GOV-004

Le lineage source → RAW → normalized → published DOIT être traçable.

### GMAIL-GOV-005

Le système DOIT permettre d’exclure certaines catégories de données.

Exemples :

```text
Spam
Trash
attachments
specific labels
date ranges
specific queries
```

---

# 20. Exigences PyIngestKit

Le connecteur Gmail ne doit pas réimplémenter les primitives génériques disponibles dans PyIngestKit.

### GMAIL-PYK-001

Le connecteur DOIT utiliser les abstractions de source/connector fournies par PyIngestKit.

### GMAIL-PYK-002

Le stockage DOIT utiliser les adapters de PyIngestKit.

### GMAIL-PYK-003

Le versioning DOIT s’appuyer sur :

```text
DatasetVersionStore
PublishedDataset
```

ou leurs équivalents actuels.

### GMAIL-PYK-004

Le système de manifest DOIT être compatible avec le contrat PyIngestKit.

### GMAIL-PYK-005

La gestion des retries DEVRA réutiliser les primitives partagées du framework.

### GMAIL-PYK-006

Le checkpointing DOIT être conçu comme une abstraction réutilisable par d’autres connecteurs CDC.

### GMAIL-PYK-007

Les éléments spécifiques Gmail doivent rester dans :

```text
gmail connector
```

et non dans le cœur PyIngestKit, sauf lorsqu’une primitive est clairement généralisable.

---

# 21. Architecture logique des responsabilités

```text
┌─────────────────────────────────────────────┐
│                  Gmail API                  │
└─────────────────────┬───────────────────────┘
                      │
┌─────────────────────▼───────────────────────┐
│             Gmail API Adapter               │
│                                             │
│ auth / profile / list / get / history       │
│ attachments / watch                         │
└─────────────────────┬───────────────────────┘
                      │
┌─────────────────────▼───────────────────────┐
│              Gmail Connector                │
│                                             │
│ discovery                                   │
│ sync strategy                               │
│ MIME traversal                              │
│ normalization                               │
│ reconciliation                              │
└─────────────────────┬───────────────────────┘
                      │
┌─────────────────────▼───────────────────────┐
│                PyIngestKit                  │
│                                             │
│ lifecycle                                   │
│ retry                                       │
│ storage                                     │
│ manifests                                   │
│ versioning                                  │
│ publication                                 │
└─────────────────────┬───────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
   Filesystem                    Object Store
```

---

# 22. Configuration minimale

Une configuration fonctionnelle devrait permettre :

```yaml
gmail:
  account: me
  mode: full
  message_format: full
  include_spam_trash: false

  filters:
    query: null
    label_ids: []

  attachments:
    enabled: false
    max_size_bytes: null

  incremental:
    enabled: true

  retry:
    max_attempts: 5

  concurrency:
    max_workers: 4

  storage:
    backend: filesystem

  security:
    oauth_scopes:
      - https://www.googleapis.com/auth/gmail.readonly
```

La syntaxe exacte sera définie dans l’architecture et l’implémentation.

---

# 23. CLI cible

Le produit final DEVRA pouvoir exposer des commandes similaires à :

```bash
gmail-ingest auth
gmail-ingest profile
gmail-ingest labels
gmail-ingest snapshot
gmail-ingest incremental
gmail-ingest range
gmail-ingest reconcile
gmail-ingest status
gmail-ingest watch
```

ou l’équivalent via la CLI commune PyIngestKit.

---

# 24. Data Quality

### GMAIL-DQ-001

Chaque message doit posséder un `message_id`.

### GMAIL-DQ-002

Chaque message doit être rattachable à un compte.

### GMAIL-DQ-003

Les doublons logiques doivent être détectés.

### GMAIL-DQ-004

Les références de pièces jointes doivent pointer vers un message existant.

### GMAIL-DQ-005

Les associations message ↔ label doivent pointer vers des objets connus ou être marquées comme références non résolues.

### GMAIL-DQ-006

Les erreurs de parsing MIME doivent être mesurées.

### GMAIL-DQ-007

Le taux d’échec par run doit être exposé.

---

# 25. Tests

# 25.1 Unit Tests

Doivent couvrir au minimum :

```text
OAuth configuration
pagination
header parsing
email address parsing
MIME traversal
base64url decoding
attachment metadata
history event mapping
checkpoint logic
manifest generation
```

---

# 25.2 Integration Tests

Doivent couvrir :

```text
messages.list
messages.get
labels.list
threads.get
history.list
attachments.get
```

avec doubles de tests ou compte dédié selon le contexte.

---

# 25.3 Reliability Tests

Scénarios :

```text
network interruption
rate limiting
token expiration
partial attachment failure
process crash
checkpoint rollback
duplicate history event
out-of-order notification
expired historyId
```

---

# 25.4 End-to-End Tests

Au minimum :

```text
empty mailbox
small mailbox
mailbox with HTML
mailbox with multipart messages
mailbox with attachments
full sync → incremental sync
incremental failure → replay
invalid historyId → full resync
```

---

# 26. Critères d’acceptation MVP

Le MVP est considéré comme accepté lorsque :

1. OAuth fonctionne sur un compte de test.
2. Le profil Gmail est récupéré.
3. Les labels sont récupérés.
4. `messages.list` est paginé correctement.
5. Les messages peuvent être récupérés en `full`.
6. Les headers principaux sont normalisés.
7. Les bodies texte et HTML sont extraits.
8. Les structures MIME imbriquées sont prises en charge.
9. Les métadonnées de pièces jointes sont détectées.
10. Une couche RAW est produite.
11. Une couche normalisée est produite.
12. Un manifest est généré.
13. Le pipeline est idempotent.
14. Un checkpoint est persisté.
15. Une ingestion incrémentale via `history.list` fonctionne.
16. Un `historyId` invalide déclenche une resynchronisation maîtrisée.
17. Une version de dataset peut être publiée.
18. Les tests automatisés passent.
19. Aucun secret n’est présent dans Git.
20. Les logs ne contiennent pas le contenu des e-mails par défaut.

---

# 27. Exigences post-MVP

Les exigences suivantes peuvent être reportées après le MVP :

```text
Pub/Sub push notifications
multi-account
Google Workspace domain-wide delegation
advanced reconciliation
object-storage production hardening
attachment deduplication
Gold analytics
semantic enrichment
LLM enrichment
search indexing
```

---

# 28. Hors périmètre

Ne font pas partie de cette spécification :

```text
send email
reply
forward
draft management
message modification
label modification
trash / delete actions
automated answering
LLM inside ingestion core
```

Toute fonctionnalité d’écriture Gmail devra faire l’objet :

- d’un besoin distinct ;
- de scopes distincts ;
- d’une analyse de sécurité dédiée.

---

# 29. Matrice de criticité

| Domaine | Criticité |
|---|---|
| Authentification | Critique |
| Intégrité du checkpoint | Critique |
| Idempotence | Critique |
| Protection des données | Critique |
| RAW preservation | Haute |
| Full Sync | Haute |
| Incremental Sync | Haute |
| Réconciliation | Haute |
| Observabilité | Haute |
| Attachments | Moyenne à haute |
| Push CDC | Moyenne avant production temps réel |
| Gold Analytics | Faible pour le socle ingestion |
| LLM Enrichment | Hors cœur |

---

# 30. Matrice Requirements → Capability

| Requirement Area | Capability |
|---|---|
| Authentication | Gmail OAuth Adapter |
| Discovery | Message Enumerator |
| Message Fetch | Gmail Message Reader |
| MIME | MIME Parser |
| Attachments | Attachment Reader |
| Full Sync | Snapshot Strategy |
| Incremental | History Sync Strategy |
| CDC | Pub/Sub Watch Adapter |
| RAW | Raw Dataset Writer |
| Normalize | Gmail Normalizer |
| Checkpoint | Gmail Checkpoint Store |
| Versioning | DatasetVersionStore |
| Publish | PublishedDataset |
| Reconciliation | Reconciliation Engine |
| Metrics | Observability Adapter |

---

# 31. Risques principaux

## R1 — Expiration du historyId

**Impact :** perte de capacité d’incrémental.

**Réponse :**

```text
detect 404
→ invalidate checkpoint
→ full sync
→ commit new checkpoint
```

---

## R2 — Quotas API

**Impact :** ralentissement ou arrêt temporaire.

**Réponse :**

```text
bounded concurrency
+ quota-aware scheduling
+ exponential backoff
+ caching
```

---

## R3 — Volume de pièces jointes

**Impact :**

- stockage ;
- bande passante ;
- temps d’exécution.

**Réponse :**

```text
attachments disabled by default
or
metadata-only
```

puis activation explicite.

---

## R4 — Données personnelles

**Impact :** sécurité et gouvernance.

**Réponse :**

```text
least privilege
private storage
secret management
retention policy
auditability
```

---

## R5 — Notifications Push perdues

**Impact :** dataset non à jour.

**Réponse :**

```text
push = trigger only
history = source of truth
periodic reconciliation
```

---

## R6 — Parsing MIME complexe

**Impact :** perte partielle de contenu.

**Réponse :**

```text
recursive MIME parser
RAW preservation
parsing diagnostics
```

---

# 32. Décisions structurantes

Les décisions suivantes sont recommandées dès maintenant.

### D1

**Gmail API officielle** comme source primaire.

### D2

**OAuth 2.0 read-only** pour le premier périmètre.

### D3

**messages** comme grain principal.

### D4

**threads** conservés comme agrégat séparé.

### D5

**RAW immuable** avant normalisation.

### D6

**historyId** comme mécanisme d’incrémental natif.

### D7

**Full Sync obligatoire** en fallback d’un checkpoint invalide.

### D8

**Pub/Sub non considéré comme source de vérité**.

### D9

**Attachments optionnelles** au démarrage.

### D10

**LLM hors cœur ingestion**.

---

# 33. Exigences de production

Avant qualification `1.0.0`, le connecteur devra également disposer de :

```text
real Gmail integration tests
quota-aware load tests
credential rotation procedure
recovery runbook
full-resync runbook
monitoring dashboard
alerting rules
data retention policy
security review
dependency audit
release reproducibility
```

---

# 34. Definition of Done globale

Le connecteur Gmail est considéré comme production-ready lorsque le flux suivant est démontré :

```text
OAuth
  ↓
Profile
  ↓
Labels
  ↓
Full Snapshot
  ↓
RAW
  ↓
Normalize
  ↓
Validate
  ↓
Version
  ↓
Publish
  ↓
Checkpoint
  ↓
Incremental
  ↓
Failure
  ↓
Recovery
  ↓
Reconciliation
  ↓
Republish
```

sans :

- doublon logique ;
- perte silencieuse de données ;
- fuite de secrets ;
- corruption du checkpoint ;
- dépendance à un LLM ;
- intervention manuelle obligatoire pour un incident transitoire.

---

# 35. Conclusion

Le connecteur Gmail doit être conçu comme un **connecteur de synchronisation de données événementielles et documentaires**.

La capacité fondamentale recherchée n’est pas :

```text
download emails
```

mais :

```text
maintain a trustworthy local representation
of a Gmail mailbox over time
```

Cela implique quatre responsabilités essentielles :

```text
INGEST
+
TRACK CHANGE
+
RECOVER
+
PROVE WHAT HAPPENED
```

PyIngestKit doit fournir les primitives génériques.

Le connecteur Gmail doit fournir les connaissances spécifiques à Gmail.

Cette séparation permettra ensuite de réutiliser une grande partie du socle pour d’autres sources :

```text
GitHub
Google Drive
Google Calendar
Slack
Notion
Microsoft Graph
IMAP
```

et plus généralement toute source disposant d’un modèle :

```text
snapshot
+
incremental cursor
+
event history
+
reconciliation
```

---

# 36. Prochains documents recommandés

L’ordre recommandé est :

```text
00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN.md
GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md
01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE.md
02_GMAIL_INGESTION_RELEASE_AND_VERSIONING_STRATEGY.md
03_GMAIL_INGESTION_DATA_MODEL_AND_SOURCE_CATALOG.md
```

Puis :

```text
04_GMAIL_INGESTION_SYNC_AND_CHECKPOINT_STRATEGY.md
05_GMAIL_INGESTION_SECURITY_AND_OAUTH_MODEL.md
06_GMAIL_INGESTION_RECONCILIATION_AND_RELIABILITY.md
```

avant l’implémentation du premier jalon.

---

# 37. Références techniques officielles

Les exigences techniques de ce document ont été alignées avec la documentation officielle Gmail API, notamment :

- Gmail API overview  
  `https://developers.google.com/workspace/gmail/api/guides`

- Synchronize clients with Gmail  
  `https://developers.google.com/workspace/gmail/api/guides/sync`

- Configure push notifications  
  `https://developers.google.com/workspace/gmail/api/guides/push`

- Gmail API scopes  
  `https://developers.google.com/workspace/gmail/api/auth/scopes`

- Gmail API usage limits  
  `https://developers.google.com/workspace/gmail/api/reference/quota`

- Gmail REST API  
  `https://developers.google.com/workspace/gmail/api/reference/rest`

Les valeurs de quota étant susceptibles d’évoluer, elles ne doivent pas être codées en dur dans le connecteur.