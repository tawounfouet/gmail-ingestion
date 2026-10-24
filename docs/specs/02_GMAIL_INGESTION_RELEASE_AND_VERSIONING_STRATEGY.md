# 02_GMAIL_INGESTION_RELEASE_AND_VERSIONING_STRATEGY

## 0. Document Control

| Champ | Valeur |
|---|---|
| Document | `02_GMAIL_INGESTION_RELEASE_AND_VERSIONING_STRATEGY.md` |
| Domaine | Data Engineering / Data Ingestion |
| Source | Gmail / Google Workspace |
| Framework cible | PyIngestKit |
| Statut | Release & Versioning Strategy |
| Version documentaire | 1.0 |
| Date de référence | 2026-10-24 |
| Documents précédents | `00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN.md`, `GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md`, `01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE.md` |

---

# 1. Objet du document

Ce document définit la stratégie de livraison, de versioning et de qualification du connecteur **Gmail Ingestion**.

Il transforme l’architecture cible en une séquence de versions incrémentales, cohérentes et testables.

L’objectif est d’éviter une implémentation monolithique du type :

```text
tout construire
→ tout tester à la fin
→ découvrir trop tard les incompatibilités
```

La stratégie retenue est au contraire :

```text
Foundation
   ↓
Authentication
   ↓
Full Snapshot
   ↓
Normalization
   ↓
Filtering
   ↓
Incremental Sync
   ↓
CDC
   ↓
Reliability
   ↓
Versioning / Object Storage
   ↓
Analytics
   ↓
Multi-account
   ↓
Production Qualification
```

Chaque version doit produire une capacité utilisable, testable et documentée.

---

# 2. Principes de release

La stratégie de release suit les principes suivants.

## 2.1 Une version = une capacité cohérente

Chaque version doit :

- ajouter une capacité identifiable ;
- conserver un périmètre contrôlé ;
- posséder des critères d’acceptation explicites ;
- être testable indépendamment ;
- éviter de mélanger plusieurs changements structurants non liés.

---

## 2.2 Les fondations avant les optimisations

Ordre de priorité :

```text
Correctness
    >
Reliability
    >
Recoverability
    >
Security
    >
Incrementality
    >
Performance
    >
Advanced Features
```

---

## 2.3 Le Full Snapshot précède l’incrémental

L’incrémental ne peut être considéré fiable tant que :

- le snapshot complet n’est pas stable ;
- le modèle RAW n’est pas défini ;
- la normalisation est déterministe ;
- les checkpoints sont persistés.

---

## 2.4 Le CDC vient après l’incrémental

Le push Pub/Sub ne doit pas être implémenté avant :

```text
history.list
+
checkpointing
+
idempotence
```

Le CDC ne fait que déclencher un moteur incrémental déjà fiable.

---

## 2.5 Production ≠ feature complete

La version `1.0.0` ne signifie pas :

```text
toutes les fonctionnalités possibles
```

Elle signifie :

```text
scope défini
+
qualifié
+
sécurisé
+
observable
+
récupérable
+
documenté
```

---

# 3. Modèles de versions à distinguer

Le projet manipule plusieurs types de versions.

Il est essentiel de ne pas les confondre.

```text
Connector Package Version
Dataset Version
Manifest Schema Version
Normalized Schema Version
Checkpoint Schema Version
Published Dataset Pointer
```

---

# 4. Version du package

Le package suit **Semantic Versioning** :

```text
MAJOR.MINOR.PATCH
```

Exemples :

```text
0.1.0
0.4.0
0.7.1
1.0.0
```

Des préversions peuvent être utilisées :

```text
0.1.0a1
0.1.0a2
0.1.0b1
0.1.0rc1
```

---

# 5. Interprétation SemVer

## 5.1 Major

```text
1.x → 2.x
```

signifie une rupture significative du contrat public.

Exemples :

- configuration incompatible ;
- changement du modèle d’exécution ;
- schéma normalisé cassant ;
- suppression d’une API publique.

---

## 5.2 Minor

```text
0.4 → 0.5
1.2 → 1.3
```

ajoute une capacité compatible.

Exemples :

- nouveau mode d’ingestion ;
- nouveau backend de stockage ;
- nouveau dataset analytique.

---

## 5.3 Patch

```text
0.4.0 → 0.4.1
```

corrige un défaut sans changement majeur du contrat.

Exemples :

- bug de parsing ;
- retry incorrect ;
- fuite de fichier ;
- erreur dans un manifest.

---

# 6. Préversions

Les préversions sont utilisées pour structurer les jalons avant stabilité.

```text
a = alpha
b = beta
rc = release candidate
```

---

## 6.1 Alpha

Une alpha correspond à :

```text
capability foundation
```

Elle peut :

- évoluer rapidement ;
- changer d’API ;
- manquer de fonctionnalités secondaires.

---

## 6.2 Beta

Une beta correspond à :

```text
feature complete for the milestone
```

mais nécessite encore :

- qualification ;
- tests renforcés ;
- stabilisation.

---

## 6.3 Release Candidate

Une RC est :

```text
production candidate
```

Aucun changement fonctionnel important ne doit être ajouté entre `rc1` et la version stable.

---

# 7. Version du dataset

La version du dataset est distincte de celle du package.

Exemple :

```text
connector version:
0.4.0

dataset version:
2026-10-24T070000Z
```

ou :

```text
dataset version:
run-01JAX...
```

---

# 8. Dataset Version ID

Le format recommandé est :

```text
YYYY-MM-DDTHHMMSSZ
```

avec suffixe éventuel :

```text
2026-10-24T071500Z__01JAXYZ...
```

Le dataset version ID doit être :

- unique ;
- ordonnable ;
- indépendant de la version du code ;
- lié à un `run_id`.

---

# 9. Version du schéma normalisé

Le schéma normalisé doit disposer de sa propre version.

Exemple :

```text
normalized_schema_version = 1
```

Une évolution additive peut rester :

```text
1
```

Une rupture peut passer à :

```text
2
```

---

# 10. Version du manifest

Le manifest possède également :

```text
manifest_schema_version
```

Exemple :

```json
{
  "manifest_schema_version": "1"
}
```

---

# 11. Version du checkpoint

Le format du checkpoint doit être versionné.

Exemple :

```json
{
  "checkpoint_schema_version": "1",
  "history_id": "123456"
}
```

---

# 12. PublishedDataset

`PublishedDataset` n’est pas une version.

C’est un pointeur vers :

```text
current qualified dataset version
```

Exemple :

```text
published/current
       ↓
2026-10-24T071500Z
```

---

# 13. Stratégie globale des releases

La roadmap proposée est :

```text
0.0.1  Repository Bootstrap

0.1.0a1 OAuth & Profile
0.1.0a2 Labels
0.1.0b1 Full Message Snapshot
0.1.0b2 MIME + Attachments + Manifest
0.1.0rc1 Snapshot Qualification
0.1.0   Snapshot Foundation Stable

0.2.0a1 Normalized Data Model
0.2.0b1 Threads + Relations
0.2.0rc1 Normalization Qualification
0.2.0   Normalized Dataset Stable

0.3.0a1 Search Filters
0.3.0b1 Date Range / Filtered Sync
0.3.0   Filtered Ingestion Stable

0.4.0a1 Checkpoint Foundation
0.4.0b1 Gmail History Incremental
0.4.0b2 Deletions + Label Mutations
0.4.0rc1 Incremental Reliability
0.4.0   Incremental Sync Stable

0.5.0a1 users.watch
0.5.0b1 Pub/Sub Consumer
0.5.0b2 Watch Renewal + CDC Recovery
0.5.0   CDC Stable

0.6.0a1 Reconciliation Engine
0.6.0b1 Recovery / Quarantine
0.6.0b2 Reliability Hardening
0.6.0   Reconciliation & Reliability Stable

0.7.0a1 DatasetVersionStore Integration
0.7.0b1 S3 / Object Storage
0.7.0b2 PublishedDataset + Atomic Promotion
0.7.0   Versioned Object Storage Stable

0.8.0a1 Communication Activity Gold
0.8.0b1 Correspondent / Response Analytics
0.8.0   Analytics Gold Stable

0.9.0a1 Multi-account Foundation
0.9.0b1 Google Workspace Extensions
0.9.0rc1 Public Contracts Freeze
0.9.0   Pre-1.0 Feature Complete

1.0.0rc1 Production Qualification
1.0.0   Production Qualified
```

---

# 14. Vue consolidée

| Jalon | Version cible | Capacité principale |
|---|---:|---|
| GMAIL_00 | `0.0.1` | Repository Bootstrap |
| GMAIL_01 | `0.1.0` | Snapshot Foundation |
| GMAIL_02 | `0.2.0` | Normalized Dataset |
| GMAIL_03 | `0.3.0` | Filtered / Range |
| GMAIL_04 | `0.4.0` | Incremental History Sync |
| GMAIL_05 | `0.5.0` | Push / CDC |
| GMAIL_06 | `0.6.0` | Reconciliation & Reliability |
| GMAIL_07 | `0.7.0` | Dataset Versioning & Object Storage |
| GMAIL_08 | `0.8.0` | Communication Analytics Gold |
| GMAIL_09 | `0.9.0` | Multi-account & Public Contracts |
| GMAIL_10 | `1.0.0` | Production Qualified |

---

# 15. GMAIL_00 — 0.0.1 Repository Bootstrap

## 15.1 Objectif

Créer le repository et le socle de développement.

Aucune ingestion Gmail réelle n’est encore requise.

---

## 15.2 Scope

```text
repository structure
pyproject.toml
src layout
tests layout
docs layout
CI foundation
lint
format
type checking
test runner
license
README
configuration skeleton
```

---

## 15.3 Structure cible

```text
gmail-ingestion/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── src/
│   └── gmail_ingestion/
├── tests/
├── docs/
│   ├── specs/
│   └── runbooks/
└── .github/
    └── workflows/
```

---

## 15.4 Dépendances

```text
Python
PyIngestKit
pytest
ruff
mypy / pyright
```

Les dépendances Google peuvent être ajoutées dès ce jalon ou au suivant.

---

## 15.5 Critères d’acceptation

- package installable ;
- tests exécutables ;
- CI verte ;
- import principal fonctionnel ;
- aucun secret versionné ;
- documentation de bootstrap présente.

---

## 15.6 Tag

```text
v0.0.1
```

---

# 16. GMAIL_01 — 0.1.0 Snapshot Foundation

Ce jalon transforme le bootstrap en connecteur Gmail capable de produire un snapshot exploitable.

---

# 17. 0.1.0a1 — OAuth & Profile

## Scope

```text
OAuth 2.0
gmail.readonly
token refresh
profile retrieval
account context
configuration
CLI auth/profile
```

---

## Livrables

```text
GmailAuthProvider
GmailApiClient
GmailAccount
GetMailboxProfile
```

---

## CLI

```bash
gmail-ingest auth
gmail-ingest profile
```

---

## Acceptance

- authentification réussie ;
- profil récupéré ;
- token réutilisable ;
- expiration gérée ;
- aucun token dans les logs.

---

# 18. 0.1.0a2 — Labels Catalog

## Scope

```text
users.labels.list
label domain model
RAW labels
normalized labels
```

---

## Acceptance

- labels système récupérés ;
- labels utilisateur récupérés ;
- type conservé ;
- dataset labels produit.

---

# 19. 0.1.0b1 — Full Message Snapshot

## Scope

```text
messages.list
pagination
messages.get
raw message JSON
snapshot orchestration
```

---

## Flux

```text
profile
  ↓
messages.list
  ↓
paged IDs
  ↓
messages.get
  ↓
RAW
```

---

## Acceptance

- toutes les pages parcourues ;
- messages complets récupérés ;
- IDs persistés ;
- reprise partielle possible ;
- pas de duplication logique.

---

# 20. 0.1.0b2 — MIME + Attachments + Manifest

## Scope

```text
recursive MIME traversal
text/plain
text/html
attachment metadata
optional attachment download
run manifest
basic metrics
```

---

## Acceptance

- multipart imbriqué supporté ;
- body texte extrait ;
- body HTML extrait ;
- pièces jointes détectées ;
- manifest JSON produit ;
- contenu binaire séparé.

---

# 21. 0.1.0rc1 — Snapshot Qualification

## Objectif

Qualifier le pipeline snapshot complet.

---

## Tests

```text
empty mailbox
simple text mail
HTML mail
multipart
thread
attachment
large mailbox sample
retry
token refresh
```

---

## Exit Criteria

```text
full snapshot stable
RAW complete
manifest stable
no known critical bug
```

---

# 22. 0.1.0 — Snapshot Foundation Stable

La version stable `0.1.0` doit permettre :

```text
OAuth
→ profile
→ labels
→ full messages
→ MIME
→ attachments metadata
→ RAW
→ manifest
```

---

# 23. GMAIL_02 — 0.2.0 Normalized Dataset

Ce jalon transforme les réponses Gmail en modèle analytique stable.

---

# 24. 0.2.0a1 — Normalized Data Model

## Datasets

```text
accounts
labels
messages
message_headers
message_recipients
mime_parts
attachments
```

---

## Acceptance

- transformations déterministes ;
- tests unitaires de mapping ;
- schéma versionné ;
- export Parquet.

---

# 25. 0.2.0b1 — Threads + Relations

## Scope

```text
threads
message_labels
message_recipients
message_headers
thread aggregation
```

---

## Acceptance

- relation message ↔ thread valide ;
- relations multi-valuées normalisées ;
- FK logiques vérifiables.

---

# 26. 0.2.0rc1 — Normalization Qualification

Qualification :

```text
RAW
→ normalize
→ parquet
→ re-run
→ identical logical output
```

---

# 27. 0.2.0 — Normalized Dataset Stable

Cette version fige le premier contrat analytique.

```text
normalized_schema_version = 1
```

---

# 28. GMAIL_03 — 0.3.0 Filtered / Range Ingestion

---

# 29. 0.3.0a1 — Search Filters

## Scope

Support de :

```text
q
labelIds
includeSpamTrash
```

---

## Exemples

```text
from:example.com
has:attachment
label:important
```

---

# 30. 0.3.0b1 — Date Range

## Scope

```text
after
before
last N days
explicit range
```

---

## Acceptance

- manifest contient les filtres ;
- deux périmètres distincts sont distinguables ;
- pas de confusion avec Full Sync global.

---

# 31. 0.3.0 — Filtered Ingestion Stable

Permet :

```text
snapshot full
snapshot filtered
date range
query-based
```

---

# 32. GMAIL_04 — 0.4.0 Incremental History Sync

Ce jalon constitue le cœur du connecteur stateful.

---

# 33. 0.4.0a1 — Checkpoint Foundation

## Scope

```text
GmailCheckpoint
CheckpointStore
COMMITTED
CANDIDATE
INVALID
```

---

## Acceptance

- checkpoint persistant ;
- isolation par compte ;
- crash avant commit ne perd pas d’événement ;
- candidate distinct du committed.

---

# 34. 0.4.0b1 — Gmail History Incremental

## Scope

```text
users.history.list
startHistoryId
pagination
history mapping
messageAdded
```

---

## Flow

```text
checkpoint H100
   ↓
history.list(H100)
   ↓
events
   ↓
hydrate
   ↓
persist
   ↓
candidate H125
   ↓
commit
```

---

# 35. 0.4.0b2 — Deletions + Label Mutations

## Scope

```text
messageDeleted
labelAdded
labelRemoved
tombstones
message_labels mutation
```

---

# 36. 0.4.0rc1 — Incremental Reliability

Scénarios obligatoires :

```text
duplicate events
page boundaries
crash before commit
crash after write
invalid checkpoint
historyId 404
```

---

# 37. 0.4.0 — Incremental Sync Stable

À cette version :

```text
Full Snapshot
+
Incremental History
+
Checkpoint
+
Recovery
```

doivent être fiables.

---

# 38. GMAIL_05 — 0.5.0 Push / CDC

---

# 39. 0.5.0a1 — users.watch

## Scope

```text
watch registration
watch state
expiration
historyId
topic configuration
```

---

# 40. 0.5.0b1 — Pub/Sub Consumer

## Scope

```text
Pub/Sub notification
decode mailbox
decode historyId
trigger incremental
```

---

# 41. 0.5.0b2 — Watch Renewal + CDC Recovery

## Scope

```text
renewal scheduling
expired watch
duplicate notifications
delayed notifications
poll fallback
```

---

# 42. 0.5.0 — CDC Stable

Le CDC est considéré stable lorsque :

```text
Push
→ Incremental Engine
```

fonctionne sans introduire une nouvelle source de vérité.

---

# 43. GMAIL_06 — 0.6.0 Reconciliation & Reliability

---

# 44. 0.6.0a1 — Reconciliation Engine

## Levels

```text
counts
IDs
metadata
relations
attachments
```

---

## Output

```text
ReconciliationReport
```

---

# 45. 0.6.0b1 — Recovery / Quarantine

## Scope

```text
quarantine
targeted replay
failed messages
failed attachments
retry exhaustion
```

---

# 46. 0.6.0b2 — Reliability Hardening

## Scope

```text
rate limit handling
backpressure
bounded queues
partial failure
resume
crash testing
```

---

# 47. 0.6.0 — Reliability Stable

Critères :

```text
no silent loss
no checkpoint corruption
recoverable partial failures
reconciliation available
```

---

# 48. GMAIL_07 — 0.7.0 Dataset Versioning & Object Storage

Ce jalon aligne Gmail avec le modèle avancé de PyIngestKit.

---

# 49. 0.7.0a1 — DatasetVersionStore Integration

## Scope

```text
FilesystemDatasetVersionStore
dataset versions
version manifests
version lifecycle
```

---

# 50. 0.7.0b1 — S3 / Object Storage

## Scope

```text
S3DatasetVersionStore
S3-compatible
MinIO
Hetzner Object Storage
AWS S3
```

selon les adapters disponibles.

---

# 51. 0.7.0b2 — PublishedDataset + Atomic Promotion

## Scope

```text
PublishedDataset
candidate version
quality gate
promotion
rollback
```

---

## Promotion

```text
build
→ validate
→ finalize
→ publish
```

---

# 52. 0.7.0 — Versioned Object Storage Stable

À cette version :

```text
filesystem
+
object storage
+
versioning
+
published pointer
```

doivent être disponibles.

---

# 53. GMAIL_08 — 0.8.0 Communication Analytics Gold

Ce jalon est optionnel pour le cœur ingestion mais constitue un premier Data Product.

---

# 54. 0.8.0a1 — Communication Activity Gold

Datasets :

```text
email_activity_daily
thread_activity
attachment_activity
```

---

# 55. 0.8.0b1 — Correspondent / Response Analytics

Datasets :

```text
correspondent_activity
response_time
domain_activity
```

---

# 56. 0.8.0 — Analytics Gold Stable

Les Gold datasets doivent être :

- dérivables du normalized ;
- recalculables ;
- indépendants du RAW ;
- indépendants d’un LLM.

---

# 57. GMAIL_09 — 0.9.0 Multi-account & Public Contracts

---

# 58. 0.9.0a1 — Multi-account Foundation

## Scope

```text
account registry
isolated checkpoints
isolated storage namespaces
per-account runs
```

---

# 59. 0.9.0b1 — Google Workspace Extensions

Possibilités :

```text
Workspace account management
domain-aware configuration
service account scenarios
domain-wide delegation if explicitly needed
```

Toute extension de ce type doit être sécurisée séparément.

---

# 60. 0.9.0rc1 — Public Contracts Freeze

À partir de cette version :

```text
CLI
configuration
Python API
manifest schema
checkpoint schema
normalized schema
```

doivent être gelés autant que possible avant `1.0.0`.

---

# 61. 0.9.0 — Pre-1.0 Feature Complete

Cette version marque :

```text
feature complete for 1.0 scope
```

Aucune nouvelle capacité majeure n’est ajoutée ensuite.

---

# 62. GMAIL_10 — 1.0.0 Production Qualified

---

# 63. 1.0.0rc1 — Production Qualification

Qualification obligatoire sur :

```text
real Gmail API
real OAuth
real object storage
long-running incremental sync
watch renewal
checkpoint recovery
quota / rate limiting
failure injection
reconciliation
```

---

# 64. Scénarios de qualification

```text
worker crash
network outage
HTTP 429
HTTP 5xx
invalid token
expired historyId
partial attachment failure
duplicate Pub/Sub event
missing Pub/Sub event
storage unavailable
restart after crash
```

---

# 65. Production readiness checklist

```text
[ ] security review
[ ] dependency audit
[ ] secret handling validated
[ ] runbooks available
[ ] observability dashboard
[ ] alerts defined
[ ] rollback tested
[ ] dataset restore tested
[ ] version promotion tested
[ ] checkpoints tested
[ ] long-run test passed
[ ] docs complete
```

---

# 66. 1.0.0 — Definition

La version `1.0.0` signifie :

```text
production qualified
```

et non :

```text
all possible features implemented
```

---

# 67. Dépendances entre jalons

```text
0.0.1
  ↓
0.1.0 Snapshot
  ↓
0.2.0 Normalized
  ↓
0.3.0 Filters
  ↓
0.4.0 Incremental
  ↓
0.5.0 CDC
  ↓
0.6.0 Reliability
  ↓
0.7.0 Versioning/Object Store
  ↓
0.8.0 Analytics
  ↓
0.9.0 Multi-account
  ↓
1.0.0 Production
```

---

# 68. Dépendances non strictes

Certains jalons peuvent avancer en parallèle.

Exemple :

```text
0.7 Object Storage
```

peut être initié avant :

```text
0.5 CDC
```

si PyIngestKit expose déjà les primitives nécessaires.

Cependant la **release publique** doit conserver une séquence compréhensible.

---

# 69. Relation avec PyIngestKit

Le connecteur doit consommer PyIngestKit, pas le forker.

---

# 70. Compatibilité minimale

Chaque release Gmail doit déclarer :

```text
requires-pyingestkit
```

Exemple :

```toml
dependencies = [
  "pyingestkit>=0.6,<0.7"
]
```

La borne exacte doit être définie au moment de la livraison.

---

# 71. Règle de montée de version PyIngestKit

Une version Gmail peut relever son minimum PyIngestKit lorsqu’elle dépend d’une nouvelle primitive.

Exemple :

```text
Gmail 0.7.0
requires PyIngestKit version exposing:
- DatasetVersionStore
- PublishedDataset
- object storage adapters
```

---

# 72. Compatibilité documentée

Chaque release note doit contenir :

```text
Python version
PyIngestKit min/max
Google API dependency versions
normalized schema version
manifest schema version
checkpoint schema version
```

---

# 73. Matrice de compatibilité cible

| Gmail Connector | PyIngestKit | Normalized Schema | Manifest | Checkpoint |
|---|---|---|---|---|
| `0.1.x` | à définir | `0/1` | `1` | N/A |
| `0.2.x` | à définir | `1` | `1` | N/A |
| `0.4.x` | à définir | `1` | `1` | `1` |
| `0.7.x` | versioning capable | `1` | `1` | `1` |
| `1.0.0` | production baseline | `1` ou stable cible | stable | stable |

---

# 74. Stratégie de branches

Recommandation :

```text
main
```

comme branche stable.

Branches de travail :

```text
feat/*
fix/*
docs/*
chore/*
release/*
```

---

# 75. Exemple

```text
feat/oauth-profile
feat/full-snapshot
feat/history-sync
fix/mime-nested-parts
release/0.4.0
```

---

# 76. Release branches

Une branche :

```text
release/0.4.0
```

peut être utilisée uniquement pendant la stabilisation.

Elle doit rester courte.

---

# 77. Tags

Tags Git :

```text
v0.0.1
v0.1.0a1
v0.1.0b1
v0.1.0
v1.0.0
```

---

# 78. Conventional Commits

Recommandé :

```text
feat:
fix:
docs:
refactor:
test:
chore:
perf:
ci:
```

---

# 79. Exemple

```text
feat(history): add Gmail incremental history sync
```

```text
fix(mime): preserve nested multipart alternatives
```

---

# 80. Changelog

Chaque version stable doit avoir :

```text
CHANGELOG.md
```

ou des release notes générées.

Sections :

```text
Added
Changed
Fixed
Security
Deprecated
Removed
Migration
```

---

# 81. Release notes

Exemple :

```text
Gmail Ingestion 0.4.0

Added
- history.list incremental sync
- checkpoint store
- tombstones

Changed
- manifest now records history cursor

Fixed
- duplicate message replay
```

---

# 82. Politique de migration

Une version modifiant un format persistant doit fournir une migration.

Formats concernés :

```text
checkpoint
manifest
normalized dataset
storage layout
configuration
```

---

# 83. Migration de checkpoint

Exemple :

```text
checkpoint v1
   ↓
migration
   ↓
checkpoint v2
```

Une migration doit être :

- testée ;
- idempotente ;
- réversible si raisonnable.

---

# 84. Migration des datasets

Les datasets historiques ne doivent pas être modifiés silencieusement.

Deux options :

```text
read old schema
```

ou :

```text
explicit migration
```

---

# 85. Versioning des données RAW

Le RAW source ne doit pas être réécrit lors d’une évolution du normalizer.

Pattern :

```text
RAW V1
   ↓
Normalizer v2
   ↓
Normalized Schema v2
```

---

# 86. Reprocessing

Une nouvelle version de code doit pouvoir produire :

```text
new dataset version
```

à partir d’un RAW historique.

Le manifest doit enregistrer :

```text
source raw version
normalizer version
connector version
```

---

# 87. Promotion des versions

Une dataset version suit :

```text
CREATED
   ↓
BUILDING
   ↓
VALIDATING
   ↓
READY
   ↓
PUBLISHED
```

---

# 88. Promotion gate

Une version ne peut être publiée que si :

```text
run success
quality checks pass
manifest finalized
checkpoint consistent
required artifacts exist
```

---

# 89. Rollback

Rollback :

```text
PublishedDataset
       ↓
previous version
```

sans supprimer la version défaillante.

---

# 90. Roll-forward

Dans la majorité des cas :

```text
fix code
→ create new version
→ promote new version
```

est préférable à modifier une version publiée.

---

# 91. Immutable releases

Une release Git taggée ne doit jamais être réécrite.

```text
v0.4.0
```

doit toujours référencer le même commit.

---

# 92. Immutable dataset versions

Une dataset version publiée ne doit pas être modifiée.

Toute correction produit une nouvelle version.

---

# 93. Release gates par niveau

## Alpha Gate

```text
unit tests pass
feature works
docs basic
```

---

## Beta Gate

```text
integration tests
error handling
data quality
observability
```

---

## RC Gate

```text
end-to-end
migration tests
reliability tests
no critical defects
```

---

## Stable Gate

```text
all RC criteria
release notes
migration notes
tag
artifact
```

---

# 94. Quality Gates

Tous les jalons stables doivent satisfaire :

```text
lint
type check
unit tests
integration tests
security scan
dependency scan
```

selon disponibilité.

---

# 95. Coverage

La couverture ne doit pas être une fin en soi.

Priorité aux zones critiques :

```text
checkpoint logic
history reducer
MIME parser
manifest
version promotion
recovery
```

---

# 96. Security Gate

Avant toute stable :

```text
no secrets committed
no write scope accidentally enabled
logs sanitized
dependency vulnerabilities reviewed
```

---

# 97. Data Contract Gate

Avant une stable touchant le normalized :

```text
schema documented
migration documented
sample datasets updated
compatibility checked
```

---

# 98. Documentation Gate

Chaque version stable doit mettre à jour au minimum :

```text
README
configuration reference
CLI reference
architecture docs if impacted
CHANGELOG
```

---

# 99. Packaging

Le package doit être buildable comme :

```text
wheel
sdist
```

si distribution Python classique retenue.

---

# 100. Container

À partir d’un jalon suffisamment stable, une image container peut être publiée.

Exemple :

```text
gmail-ingestion:0.6.0
gmail-ingestion:1.0.0
```

---

# 101. Container tagging

```text
1.0.0
1.0
1
latest
```

`latest` ne doit jamais remplacer l’usage de versions explicites en production.

---

# 102. Artifact provenance

Une release doit idéalement conserver :

```text
git commit SHA
package hash
container digest
dependency lock
```

---

# 103. SHA-256

Les artifacts distribués peuvent être accompagnés d’un SHA-256.

Exemple :

```text
gmail_ingestion-0.4.0-py3-none-any.whl
gmail_ingestion-0.4.0-py3-none-any.whl.sha256
```

---

# 104. Reproducible build

Objectif :

```text
same source
+
same dependency lock
=
same logical artifact
```

---

# 105. Environnements

Recommandation :

```text
DEV
TEST
REC
PROD
```

ou :

```text
LOCAL
CI
STAGING
PROD
```

---

# 106. Promotion environnementale

```text
DEV
 ↓
TEST
 ↓
STAGING
 ↓
PROD
```

Le même artifact doit être promu.

Il ne doit pas être rebuildé différemment entre environnements.

---

# 107. Configuration par environnement

Seules les configurations changent :

```text
credentials
storage
logging
quota policy
account
Pub/Sub topic
```

---

# 108. Secrets

Les secrets ne doivent jamais être intégrés aux artifacts.

---

# 109. Rollout strategy

Pour les versions majeures du sync engine :

```text
single test mailbox
   ↓
small mailbox
   ↓
larger mailbox
   ↓
production mailbox
```

---

# 110. Canary

En multi-account :

```text
1 account
→ 5%
→ 25%
→ 100%
```

peut être utilisé.

---

# 111. Feature flags

Certaines capacités risquées peuvent être activées progressivement.

Exemples :

```text
attachments_download
cdc_enabled
new_normalizer
new_reconciliation
```

---

# 112. Dépréciation

Une API publique dépréciée doit :

1. être documentée ;
2. émettre un warning ;
3. rester disponible pendant une période raisonnable ;
4. être supprimée uniquement dans une version compatible avec la politique SemVer.

---

# 113. Backward compatibility target

Avant `1.0.0`, des ruptures sont possibles mais doivent rester documentées.

Après `1.0.0` :

```text
breaking changes
→ major version
```

---

# 114. Roadmap visuelle

```text
0.0.1
BOOTSTRAP
   │
   ▼
0.1.0
SNAPSHOT
   │
   ▼
0.2.0
NORMALIZED
   │
   ▼
0.3.0
FILTERS
   │
   ▼
0.4.0
INCREMENTAL
   │
   ▼
0.5.0
CDC
   │
   ▼
0.6.0
RELIABILITY
   │
   ▼
0.7.0
VERSIONING / S3
   │
   ▼
0.8.0
ANALYTICS
   │
   ▼
0.9.0
MULTI-ACCOUNT
   │
   ▼
1.0.0
PRODUCTION QUALIFIED
```

---

# 115. Capability Matrix

| Capability | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OAuth | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Full Snapshot | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Normalized |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Filters |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Incremental |  |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Push CDC |  |  |  |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reconciliation |  |  |  | basic | basic | ✓ | ✓ | ✓ | ✓ | ✓ |
| Object Storage | optional | optional | optional | optional | optional | optional | ✓ | ✓ | ✓ | ✓ |
| PublishedDataset |  |  |  |  |  |  | ✓ | ✓ | ✓ | ✓ |
| Gold Analytics |  |  |  |  |  |  |  | ✓ | ✓ | ✓ |
| Multi-account |  |  |  |  |  |  |  |  | ✓ | ✓ |

---

# 116. Release Ownership

Chaque release doit avoir :

```text
owner
scope
acceptance criteria
test evidence
release notes
```

même si un seul développeur porte initialement le projet.

---

# 117. Issue / Milestone Mapping

Exemple GitHub :

```text
Milestone: GMAIL_04 — 0.4.0 Incremental Sync
```

Issues :

```text
CheckpointStore
History API Adapter
History Mapper
Event Reducer
Tombstones
Invalid History Recovery
Reliability Tests
```

---

# 118. PR policy

Une PR doit idéalement rester :

```text
small enough to review
large enough to deliver coherent behavior
```

Éviter :

```text
one PR = entire 0.4.0
```

---

# 119. Merge criteria

```text
tests green
review complete
docs updated
no secrets
migration addressed
```

---

# 120. Release Candidate Freeze

Pendant une RC :

```text
allowed:
bug fixes
docs
tests
security fixes

not allowed:
new major feature
large refactor
new architecture
```

---

# 121. Patch strategy

Exemple :

```text
0.4.0
 ↓
bug found in history pagination
 ↓
0.4.1
```

---

# 122. Hotfix strategy

Pour une production `1.0.0` :

```text
1.0.0
 ↓
critical bug
 ↓
1.0.1
```

sans attendre `1.1.0`.

---

# 123. Security release

Une vulnérabilité peut déclencher :

```text
1.0.2
```

avec publication rapide et release notes dédiées.

---

# 124. Major version future

Un futur `2.0.0` pourrait être justifié par :

```text
new public API
new normalized model
distributed runtime
fundamental storage contract change
```

---

# 125. Future 1.x releases

Après `1.0.0`, des versions possibles :

```text
1.1.0 Advanced Workspace
1.2.0 Search Index Integration
1.3.0 Advanced Analytics
1.4.0 Distributed Workers
1.5.0 Enterprise Governance
```

Ces versions ne font pas partie du scope initial.

---

# 126. Dataset retention and version pruning

Le versioning ne signifie pas conserver toutes les versions éternellement.

Une politique peut conserver :

```text
latest N
daily for 30 days
monthly for 1 year
milestone snapshots forever
```

---

# 127. Published version retention

La version publiée actuelle et la précédente doivent idéalement rester disponibles pour rollback.

---

# 128. RAW retention

Le RAW peut suivre une politique différente du normalized.

Exemple :

```text
RAW: 90 days
Normalized: 1 year
Published Gold: long-term
```

selon contraintes de gouvernance.

---

# 129. Attachment retention

Les pièces jointes peuvent avoir :

```text
shorter retention
```

ou :

```text
metadata-only
```

pour contrôler les coûts.

---

# 130. Release Decision Record

Chaque version stable doit répondre à :

```text
What changed?
Why?
What data contract changed?
What migration is required?
How to roll back?
What PyIngestKit version is required?
```

---

# 131. Definition of Done — Stable Release

Une release stable est terminée lorsque :

```text
[ ] code merged
[ ] tests passed
[ ] integration passed
[ ] docs updated
[ ] changelog updated
[ ] compatibility recorded
[ ] migration recorded
[ ] tag created
[ ] artifact built
[ ] artifact hash recorded
[ ] release notes published
```

---

# 132. Definition of Done — Dataset Version

Une dataset version est publiable lorsque :

```text
[ ] extraction complete
[ ] RAW persisted
[ ] normalized persisted
[ ] manifest finalized
[ ] quality checks passed
[ ] checkpoint consistent
[ ] version registered
[ ] publication pointer update possible
```

---

# 133. Versioning invariant

Le principe central est :

> **Une version du code décrit comment le connecteur fonctionne ; une version du dataset décrit ce qu’une exécution a produit.**

Les deux ne doivent jamais être confondues.

---

# 134. Release invariant

> **Aucun jalon ne doit introduire une capacité plus avancée tant que la capacité précédente n’est pas suffisamment fiable pour servir de fondation.**

Exemple :

```text
CDC
```

ne doit pas masquer un moteur incrémental instable.

---

# 135. Roadmap finale recommandée

```text
GMAIL_00 — 0.0.1
Repository Bootstrap

GMAIL_01 — 0.1.0
Snapshot Foundation

GMAIL_02 — 0.2.0
Normalized Dataset

GMAIL_03 — 0.3.0
Filtered / Range Ingestion

GMAIL_04 — 0.4.0
Incremental History Sync

GMAIL_05 — 0.5.0
Push / CDC

GMAIL_06 — 0.6.0
Reconciliation & Reliability

GMAIL_07 — 0.7.0
Dataset Versioning & Object Storage

GMAIL_08 — 0.8.0
Communication Analytics Gold

GMAIL_09 — 0.9.0
Multi-account & Public Contracts

GMAIL_10 — 1.0.0
Production Qualified
```

---

# 136. Prochain document recommandé

Le prochain document est :

```text
03_GMAIL_INGESTION_DATA_MODEL_AND_SOURCE_CATALOG.md
```

Il devra détailler :

```text
Gmail resources
API endpoints
source fields
RAW schemas
normalized entities
relationships
dataset grains
keys
partitioning
data types
lineage
```

---

# 137. Documents suivants

Après `03` :

```text
04_GMAIL_INGESTION_SYNC_AND_CHECKPOINT_STRATEGY.md
05_GMAIL_INGESTION_SECURITY_AND_OAUTH_MODEL.md
06_GMAIL_INGESTION_RECONCILIATION_AND_RELIABILITY.md
```

Puis l’implémentation pourra démarrer par :

```text
GMAIL_00 — 0.0.1 Repository Bootstrap
```

---

# 138. Conclusion

La stratégie de release retenue construit Gmail Ingestion par couches successives :

```text
foundation
→ snapshot
→ model
→ filter
→ incremental
→ event-driven
→ reliability
→ versioning
→ analytics
→ multi-account
→ production
```

Cette progression évite deux erreurs fréquentes :

```text
1. vouloir implémenter le temps réel avant d’avoir un incremental fiable
2. confondre version logicielle et version de données
```

La cible finale reste :

> **Un connecteur Gmail versionné, testable, recoverable et industrialisable, dont chaque release ajoute une capacité claire sans fragiliser les garanties déjà acquises.**