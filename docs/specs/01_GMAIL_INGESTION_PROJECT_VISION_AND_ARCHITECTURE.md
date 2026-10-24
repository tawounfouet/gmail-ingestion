# 01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE

## 0. Document Control

| Champ | Valeur |
|---|---|
| Document | `01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE.md` |
| Domaine | Data Engineering / Data Ingestion |
| Source | Gmail / Google Workspace |
| Framework cible | PyIngestKit |
| Statut | Project Vision & Target Architecture |
| Version documentaire | 1.0 |
| Date de référence | 2026-10-24 |
| Document précédent | `GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md` |

---

# 1. Objet du document

Ce document décrit la vision produit et l’architecture cible du connecteur **Gmail Ingestion** construit sur **PyIngestKit**.

Il transforme les besoins et exigences définis dans :

```text
00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN.md
GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md
```

en une architecture cohérente permettant de passer ensuite à :

```text
Architecture
   ↓
Release Strategy
   ↓
Data Model
   ↓
Sync & Checkpoint Strategy
   ↓
Security Model
   ↓
Implementation
```

Ce document répond principalement aux questions suivantes :

```text
WHY ?
Pourquoi ce connecteur existe-t-il ?

WHAT ?
Quelles responsabilités couvre-t-il ?

WHERE ?
Où se situent les frontières entre Gmail, le connecteur et PyIngestKit ?

HOW ?
Comment les flux Full / Incremental / CDC sont-ils orchestrés ?

WITH WHAT CONTRACTS ?
Quelles abstractions et interfaces structurent l’implémentation ?
```

---

# 2. Vision produit

## 2.1 Vision synthétique

La vision du projet est de transformer Gmail de :

```text
Mailbox UI
```

vers :

```text
Reliable Versioned Dataset
```

selon une chaîne maîtrisée :

```text
Gmail
  ↓
Discover
  ↓
Extract
  ↓
Preserve RAW
  ↓
Normalize
  ↓
Validate
  ↓
Version
  ↓
Publish
  ↓
Reconcile
```

Le connecteur ne doit pas être un simple exporteur d’e-mails.

Il doit maintenir dans le temps une représentation locale fiable d’une boîte Gmail.

---

## 2.2 Formulation cible

> **Gmail Ingestion est un connecteur PyIngestKit permettant de synchroniser une boîte Gmail vers un dataset local ou objet, versionné, audit-able, incrémental et reconstructible.**

---

# 3. Principes d’architecture

Les choix d’architecture doivent respecter les principes suivants.

## 3.1 Source of truth

Gmail reste la source de vérité opérationnelle.

Le dataset local représente :

```text
a controlled materialization
```

de l’état et de l’historique accessibles via Gmail.

---

## 3.2 RAW before interpretation

Toujours préserver la donnée source avant transformation.

```text
API Response
   ↓
RAW
   ↓
Normalized
```

et non :

```text
API Response
   ↓
Normalize
   ↓
Discard Source
```

---

## 3.3 Incremental by default after bootstrap

La première exécution est un snapshot.

Les suivantes utilisent l’historique Gmail dès que possible.

```text
Bootstrap
   ↓
Full Snapshot
   ↓
Checkpoint
   ↓
Incremental Sync
```

---

## 3.4 Full Sync remains a first-class capability

Le Full Sync n’est pas un mode temporaire supprimé après le bootstrap.

Il reste nécessaire pour :

- initialisation ;
- réparation ;
- audit ;
- réconciliation ;
- invalidation du `historyId` ;
- reconstruction complète.

---

## 3.5 Notifications are triggers, not data truth

Le mécanisme Push / CDC doit respecter :

```text
Pub/Sub notification
        ↓
trigger
        ↓
history.list
        ↓
actual changes
```

La notification seule n’est jamais suffisante pour matérialiser l’état métier.

---

## 3.6 PyIngestKit owns generic ingestion capabilities

Le connecteur Gmail ne doit pas implémenter de nouveau :

- mécanisme générique de retry ;
- abstraction de stockage ;
- lifecycle générique de run ;
- versioning générique ;
- publication générique ;
- logging générique ;
- primitives de manifest déjà disponibles.

---

## 3.7 Gmail connector owns Gmail semantics

Le connecteur Gmail est responsable de :

- l’API Gmail ;
- la pagination Gmail ;
- les `historyId` ;
- les labels ;
- les messages ;
- les threads ;
- les structures MIME ;
- les pièces jointes ;
- les événements d’historique ;
- le mécanisme `users.watch`.

---

# 4. Architecture générale

```text
┌───────────────────────────────────────────────────────────────┐
│                         GOOGLE CLOUD                          │
│                                                               │
│  ┌───────────────────────┐       ┌─────────────────────────┐  │
│  │      Gmail API        │       │   Google Cloud Pub/Sub  │  │
│  │                       │       │                         │  │
│  │ profile               │       │ Push notifications      │  │
│  │ labels                │       └─────────────┬───────────┘  │
│  │ messages              │                     │              │
│  │ threads               │                     │              │
│  │ history               │                     │              │
│  │ watch                 │                     │              │
│  └─────────────┬─────────┘                     │              │
└────────────────┼───────────────────────────────┼──────────────┘
                 │                               │
                 ▼                               ▼
┌───────────────────────────────────────────────────────────────┐
│                     GMAIL INGESTION                           │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                 Gmail API Adapter                       │  │
│  │                                                         │  │
│  │ OAuth / Profile / Labels / Messages / Threads          │  │
│  │ History / Attachments / Watch                          │  │
│  └──────────────────────────┬──────────────────────────────┘  │
│                             │                                 │
│  ┌──────────────────────────▼──────────────────────────────┐  │
│  │                  Gmail Connector                       │  │
│  │                                                        │  │
│  │ Sync strategies                                        │  │
│  │ MIME traversal                                         │  │
│  │ Gmail normalization                                    │  │
│  │ Gmail checkpoint semantics                             │  │
│  │ Reconciliation                                         │  │
│  └──────────────────────────┬──────────────────────────────┘  │
└─────────────────────────────┼─────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│                         PyIngestKit                           │
│                                                               │
│  Run lifecycle                                                │
│  Retry / backoff                                              │
│  Storage ports                                                │
│  Manifest                                                     │
│  Dataset versioning                                           │
│  PublishedDataset                                             │
│  Metrics / logging contracts                                  │
└─────────────────────────────┬─────────────────────────────────┘
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
        Filesystem        S3-compatible      Other
                            Object Store     adapters
```

---

# 5. Architecture en couches

Le projet est découpé en six couches principales.

```text
1. Source Integration
2. Domain / Gmail Semantics
3. Application / Use Cases
4. PyIngestKit Integration
5. Persistence / Storage
6. Observability / Operations
```

---

# 6. Layer 1 — Source Integration

Cette couche encapsule exclusivement l’accès à Gmail.

## 6.1 Responsabilités

```text
OAuth
HTTP/API client
pagination
rate limiting awareness
Google error mapping
API DTO retrieval
```

---

## 6.2 Composants

```text
GmailAuthProvider
GmailApiClient
GmailProfileApi
GmailLabelsApi
GmailMessagesApi
GmailThreadsApi
GmailHistoryApi
GmailAttachmentsApi
GmailWatchApi
```

---

## 6.3 GmailApiClient

Interface logique :

```python
class GmailApiClient:
    def get_profile(...)
    def list_labels(...)
    def list_messages(...)
    def get_message(...)
    def get_thread(...)
    def get_attachment(...)
    def list_history(...)
    def watch(...)
    def stop_watch(...)
```

Cette classe ne doit pas :

- écrire de Parquet ;
- gérer les versions ;
- interpréter le modèle analytique ;
- décider d’un Full Resync ;
- publier un dataset.

---

# 7. Layer 2 — Domain / Gmail Semantics

Cette couche représente les concepts Gmail indépendamment du client Google.

## 7.1 Objets principaux

```text
GmailAccount
GmailLabel
GmailMessage
GmailThread
GmailHeader
GmailMimePart
GmailAttachment
GmailHistoryEvent
GmailCheckpoint
GmailWatchSubscription
```

---

## 7.2 GmailAccount

Exemple conceptuel :

```python
@dataclass(frozen=True)
class GmailAccount:
    account_id: str
    email_address: str
```

`account_id` doit être stable dans le contexte de stockage.

Il ne doit pas nécessairement être égal à l’adresse e-mail en clair.

Une stratégie de pseudonymisation peut être ajoutée.

---

# 8. Layer 3 — Application / Use Cases

Cette couche porte l’orchestration métier.

## 8.1 Use cases principaux

```text
AuthenticateMailbox
GetMailboxProfile
CatalogLabels

RunFullSnapshot
RunIncrementalSync
RunRangeSync
RunFilteredSync

DownloadAttachment
ReconcileMailbox

RegisterWatch
RenewWatch
StopWatch
HandlePushNotification
```

---

## 8.2 Principe

Un use case décide :

```text
what should happen
```

mais ne doit pas contenir :

```text
Google SDK implementation details
filesystem implementation details
S3 SDK calls
```

---

# 9. Layer 4 — PyIngestKit Integration

PyIngestKit fournit le socle transversal.

## 9.1 Responsabilités

```text
run lifecycle
retry policy
storage abstraction
dataset version lifecycle
manifest lifecycle
publish lifecycle
generic observability hooks
```

---

## 9.2 Primitive de versioning

La cible conceptuelle est :

```text
DatasetVersionStore
      │
      ├── FilesystemDatasetVersionStore
      └── S3DatasetVersionStore

PublishedDataset
```

Le connecteur Gmail manipule ces contrats sans connaître les détails de stockage.

---

## 9.3 Frontière recommandée

```text
Gmail Connector
      │
      │ produces
      ▼
Dataset Artifacts
      │
      │ persisted by
      ▼
PyIngestKit Storage / Versioning
```

---

# 10. Layer 5 — Persistence

Cette couche persiste :

- RAW ;
- normalized ;
- manifests ;
- checkpoints ;
- versions ;
- published pointer ;
- attachments.

---

# 11. Layer 6 — Observability & Operations

Cette couche fournit :

```text
logs
metrics
run summaries
health
readiness
alerts
reconciliation reports
```

Elle ne doit pas dépendre du contenu métier des e-mails.

---

# 12. Control Plane vs Data Plane

Une séparation importante est introduite.

## 12.1 Data Plane

Manipule les données Gmail.

```text
messages
threads
labels
attachments
history events
```

---

## 12.2 Control Plane

Manipule l’état du pipeline.

```text
run
checkpoint
manifest
dataset version
watch expiration
errors
metrics
publication pointer
```

---

## 12.3 Vue

```text
                 ┌─────────────────────┐
                 │     CONTROL PLANE   │
                 │                     │
                 │ Run                 │
                 │ Checkpoint          │
                 │ Manifest            │
                 │ Version             │
                 │ Watch state         │
                 └──────────┬──────────┘
                            │ controls
                            ▼
┌────────────────────────────────────────────────┐
│                  DATA PLANE                    │
│                                                │
│ Messages / Threads / Labels / Attachments      │
│ History Events / RAW / Normalized              │
└────────────────────────────────────────────────┘
```

---

# 13. Flux Full Snapshot

## 13.1 Objectif

Construire une représentation complète du périmètre Gmail configuré.

---

## 13.2 Séquence

```text
User / Scheduler
      │
      ▼
RunFullSnapshot
      │
      ▼
GetProfile
      │
      ├──────────────► capture initial historyId
      │
      ▼
ListLabels
      │
      ▼
ListMessages (paged)
      │
      ▼
Persist discovered IDs
      │
      ▼
GetMessage × N
      │
      ├── headers
      ├── MIME
      └── attachment metadata
      │
      ▼
Optional GetAttachment
      │
      ▼
Write RAW
      │
      ▼
Normalize
      │
      ▼
Validate
      │
      ▼
Create Dataset Version
      │
      ▼
Write Manifest
      │
      ▼
Commit Checkpoint
      │
      ▼
Publish
```

---

# 14. Snapshot consistency strategy

Une boîte Gmail peut changer pendant un Full Snapshot.

Il faut donc éviter de supposer une isolation transactionnelle globale.

## 14.1 Stratégie recommandée

Au début du snapshot :

```text
profile.historyId = H_START
```

À la fin :

```text
profile.historyId = H_END
```

Après le snapshot principal :

```text
history.list(startHistoryId=H_START)
```

permet de rattraper les changements intervenus pendant la durée du snapshot.

---

## 14.2 Pattern

```text
capture H_START
     ↓
snapshot
     ↓
catch-up history from H_START
     ↓
apply changes
     ↓
commit H_END_EFFECTIVE
```

Cela réduit le risque de fenêtre d’incohérence.

---

# 15. Flux Incremental

## 15.1 Entrée

```text
committed_checkpoint.history_id
```

---

## 15.2 Séquence

```text
Scheduler
   │
   ▼
RunIncrementalSync
   │
   ▼
Load committed checkpoint H100
   │
   ▼
history.list(startHistoryId=H100)
   │
   ▼
Process pages
   │
   ├── messageAdded
   ├── messageDeleted
   ├── labelAdded
   └── labelRemoved
   │
   ▼
Hydrate changed messages when needed
   │
   ▼
Write RAW events
   │
   ▼
Update normalized datasets
   │
   ▼
Create candidate checkpoint H125
   │
   ▼
Validate
   │
   ▼
Create dataset version
   │
   ▼
Publish
   │
   ▼
Commit H125
```

---

# 16. Invalid historyId handling

Google peut refuser un `startHistoryId` devenu invalide ou trop ancien.

L’architecture doit traiter ce scénario explicitement.

```text
history.list
    │
    └── HTTP 404
          │
          ▼
InvalidHistoryCheckpoint
          │
          ▼
mark checkpoint invalid
          │
          ▼
RunFullSnapshot
          │
          ▼
establish new checkpoint
```

Aucune tentative artificielle de calcul d’un `historyId` intermédiaire ne doit être réalisée.

---

# 17. History event processing

Les événements Gmail doivent être convertis vers un modèle interne.

## 17.1 Types

```text
MESSAGE_ADDED
MESSAGE_DELETED
LABEL_ADDED
LABEL_REMOVED
```

---

## 17.2 Mapping

```text
Gmail History Response
       ↓
GmailHistoryMapper
       ↓
GmailHistoryEvent
       ↓
Event Reducer
       ↓
Dataset Mutations
```

---

# 18. History Event Reducer

Le reducer traduit un événement Gmail en mutation dataset.

Exemple :

```text
MESSAGE_ADDED
    ↓
fetch message
    ↓
upsert normalized message
```

```text
MESSAGE_DELETED
    ↓
tombstone
```

```text
LABEL_ADDED
    ↓
upsert message_label relation
```

```text
LABEL_REMOVED
    ↓
close/delete active relation
```

---

# 19. Idempotence architecture

Le système est conçu pour accepter des exécutions `at-least-once`.

## 19.1 Message key

```text
(account_id, message_id)
```

---

## 19.2 Thread key

```text
(account_id, thread_id)
```

---

## 19.3 Attachment key

```text
(account_id, message_id, attachment_id)
```

---

## 19.4 History event key

Une clé dérivée peut être utilisée :

```text
account_id
+
history_id
+
event_type
+
message_id
+
label_id?
```

---

# 20. Checkpoint architecture

## 20.1 États

```text
COMMITTED
CANDIDATE
INVALID
```

---

## 20.2 Modèle

```text
GmailCheckpoint
├── account_id
├── history_id
├── run_id
├── dataset_version
├── state
├── created_at
└── committed_at
```

---

## 20.3 Transition

```text
H100 COMMITTED
     │
     ▼
Run
     │
     ▼
H125 CANDIDATE
     │
     ├── failure ──► discard candidate
     │
     └── success
            │
            ▼
      H125 COMMITTED
```

---

# 21. Checkpoint Store

Interface conceptuelle :

```python
class GmailCheckpointStore(Protocol):
    def load_committed(self, account_id: str) -> GmailCheckpoint | None: ...
    def save_candidate(self, checkpoint: GmailCheckpoint) -> None: ...
    def commit(self, checkpoint: GmailCheckpoint) -> None: ...
    def invalidate(self, account_id: str, reason: str) -> None: ...
```

Le stockage physique peut être :

```text
filesystem
database
object storage
```

sans changer la logique Gmail.

---

# 22. Push / CDC architecture

## 22.1 Vue

```text
Gmail
  │
  │ users.watch
  ▼
Google Cloud Pub/Sub
  │
  ▼
Push Consumer
  │
  ▼
Notification Decoder
  │
  ▼
MailboxChanged
  │
  ▼
Incremental Sync
  │
  ▼
history.list
```

---

# 23. Watch lifecycle

Le watch Gmail est temporaire.

Il doit donc avoir un cycle de vie explicite.

```text
REGISTERED
    ↓
ACTIVE
    ↓
RENEWING
    ↓
ACTIVE
```

ou :

```text
ACTIVE
   ↓
EXPIRED
   ↓
REGISTER
```

---

## 23.1 État de watch

```text
GmailWatchState
├── account_id
├── topic
├── history_id
├── expiration
├── registered_at
├── last_renewed_at
└── status
```

---

# 24. Watch renewal

Le renouvellement doit être piloté par un scheduler.

Recommandation :

```text
expiration returned by Gmail
          ↓
renew sufficiently before expiration
```

La marge doit être configurable.

Une planification quotidienne peut être utilisée comme valeur opérationnelle prudente si elle reste compatible avec la configuration du compte et les quotas.

---

# 25. Push reliability

Les notifications Push doivent être traitées comme :

```text
possibly duplicated
possibly delayed
possibly missing
```

Le système doit donc conserver :

```text
periodic incremental polling
+
reconciliation
```

même lorsqu’un watch est actif.

---

# 26. Architecture MIME

Les messages Gmail sont des arbres MIME.

## 26.1 Modèle

```text
Message
└── Payload
    ├── Part
    │   ├── text/plain
    │   └── text/html
    └── multipart/*
        ├── Part
        └── Part
            └── Attachment
```

---

# 27. MIME traversal

Le parser doit fonctionner récursivement.

```python
def walk(part):
    yield part

    for child in part.parts:
        yield from walk(child)
```

Le modèle ne doit pas supposer :

```text
body = payload.body
```

---

# 28. MIME extraction pipeline

```text
Gmail Payload
     ↓
MimeTreeWalker
     ↓
MimePartClassifier
     ├── body text
     ├── body html
     ├── inline resource
     └── attachment
     ↓
MessageContentAssembler
```

---

# 29. RAW architecture

La couche RAW conserve l’état source.

## 29.1 Arborescence

```text
gmail/
└── accounts/
    └── <account_id>/
        └── raw/
            ├── profile/
            ├── labels/
            ├── messages/
            ├── threads/
            ├── history/
            └── attachments/
```

---

## 29.2 Messages

```text
raw/messages/
└── <message_id>/
    ├── full.json
    ├── raw.eml          # optionnel
    └── metadata.json
```

---

## 29.3 Attachments

```text
raw/attachments/
└── <message_id>/
    └── <attachment_id>/
        ├── metadata.json
        └── payload.bin
```

---

# 30. Normalized architecture

La couche normalisée est tabulaire.

```text
normalized/
├── accounts.parquet
├── labels.parquet
├── messages.parquet
├── threads.parquet
├── message_headers.parquet
├── message_recipients.parquet
├── message_labels.parquet
├── mime_parts.parquet
├── attachments.parquet
└── history_events.parquet
```

---

# 31. Grain des datasets

| Dataset | Grain |
|---|---|
| `accounts` | 1 ligne par compte |
| `labels` | 1 ligne par label |
| `messages` | 1 ligne par message |
| `threads` | 1 ligne par thread |
| `message_headers` | 1 ligne par header |
| `message_recipients` | 1 ligne par destinataire et rôle |
| `message_labels` | 1 relation message-label |
| `mime_parts` | 1 ligne par partie MIME |
| `attachments` | 1 ligne par pièce jointe |
| `history_events` | 1 ligne par changement Gmail |

---

# 32. Published architecture

Une version publiée représente un dataset cohérent.

```text
published/
├── current.json
└── versions/
    └── <dataset_version>/
```

ou via le contrat abstrait `PublishedDataset`.

---

# 33. Dataset Version layout

Exemple logique :

```text
versions/
└── 2026-10-24T070000Z/
    ├── manifest.json
    ├── raw/
    ├── normalized/
    ├── quality/
    │   └── report.json
    └── metadata/
        └── checkpoint.json
```

---

# 34. Atomic publication

La publication suit le pattern :

```text
Build Candidate Version
        ↓
Validate
        ↓
Finalize Manifest
        ↓
Publish Version Pointer
        ↓
Commit Checkpoint
```

ou, selon les garanties transactionnelles disponibles :

```text
Build
→ Validate
→ Commit Version
→ Commit Checkpoint
→ Publish pointer
```

Le point important est que :

```text
checkpoint
```

et :

```text
published version
```

ne doivent jamais diverger silencieusement.

---

# 35. Publication state machine

```text
CREATED
   ↓
EXTRACTING
   ↓
NORMALIZING
   ↓
VALIDATING
   ↓
READY
   ↓
PUBLISHED
```

Échec :

```text
ANY STATE
   ↓
FAILED
```

Une version `FAILED` ne devient jamais la version courante.

---

# 36. Manifest architecture

Le manifest décrit l’exécution et ses artefacts.

## 36.1 Sections

```text
identity
source
configuration
checkpoint
counts
storage
quality
errors
timing
versioning
publication
```

---

## 36.2 Exemple conceptuel

```json
{
  "schema_version": "1",
  "connector": "gmail",
  "connector_version": "0.x",
  "run_id": "...",
  "account_id": "...",
  "mode": "incremental",
  "input_checkpoint": {
    "history_id": "100"
  },
  "output_checkpoint": {
    "history_id": "125"
  },
  "counts": {
    "messages": 42,
    "attachments": 3
  },
  "dataset_version": "...",
  "status": "success"
}
```

---

# 37. Discovery / Fetch separation

Pour les boîtes volumineuses, la découverte et la récupération doivent être découplées.

```text
messages.list
     ↓
DiscoveredMessage
     ↓
Persistent Discovery Queue
     ↓
workers
     ↓
messages.get
```

---

# 38. Pourquoi découpler ?

Cela permet :

- reprise partielle ;
- parallélisme borné ;
- meilleure gestion des quotas ;
- visibilité sur le backlog ;
- réexécution ciblée ;
- séparation des erreurs `list` / `get`.

---

# 39. Fetch execution model

```text
Discovery Producer
       │
       ▼
Bounded Work Queue
       │
       ├── Worker 1
       ├── Worker 2
       ├── Worker 3
       └── Worker N
              │
              ▼
        Gmail messages.get
```

Le nombre de workers doit être configurable.

---

# 40. Backpressure

Le système doit éviter :

```text
discover millions
→ hold millions in RAM
```

Le pipeline doit supporter :

```text
paged discovery
+
bounded queue
+
persistent progress
```

---

# 41. Retry architecture

Les erreurs Google doivent être mappées vers des catégories génériques.

```text
Google Error
    ↓
GmailErrorMapper
    ↓
PyIngestKit Error Category
```

---

# 42. Retry categories

```text
RETRYABLE
├── network
├── transient API
├── quota / rate
└── temporary backend

NON_RETRYABLE
├── invalid credentials
├── permission denied
├── invalid configuration
└── invalid request

RECOVERY_REQUIRED
└── invalid historyId
```

---

# 43. Rate limiting architecture

Le connecteur ne doit pas hardcoder les quotas Gmail.

La politique est :

```text
config
  ↓
RateLimitPolicy
  ↓
Concurrency Controller
  ↓
Gmail API
```

---

# 44. Concurrency

Trois niveaux doivent être distingués.

```text
account concurrency
message concurrency
attachment concurrency
```

Exemple :

```yaml
concurrency:
  accounts: 1
  messages: 4
  attachments: 2
```

---

# 45. Attachment architecture

Les pièces jointes sont un sous-flux séparé.

```text
Message
  ↓
Attachment Metadata
  ↓
Attachment Policy
  ├── skip
  ├── metadata_only
  └── download
         ↓
      Content Store
```

---

# 46. Attachment Policy

Une policy peut utiliser :

```text
mime type
size
filename
label
sender
date
```

Exemple :

```yaml
attachments:
  mode: metadata_only
  max_size_bytes: 25_000_000
```

---

# 47. Content-addressed attachment option

Une évolution peut stocker les blobs selon un hash.

```text
SHA-256(content)
       ↓
objects/<hash>
```

Puis :

```text
attachments.parquet
storage_uri = objects/<hash>
```

Bénéfices :

- déduplication ;
- contrôle d’intégrité ;
- stockage partagé.

---

# 48. Security architecture

## 48.1 Trust boundaries

```text
┌─────────────────┐
│ Google Identity │
└────────┬────────┘
         │ OAuth
         ▼
┌─────────────────┐
│ Gmail Connector │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Private Storage │
└─────────────────┘
```

---

# 49. OAuth design

Le mode MVP utilise :

```text
gmail.readonly
```

car le connecteur doit lire le contenu des messages.

Aucun scope d’écriture n’est requis pour le cœur d’ingestion.

---

# 50. OAuth responsibilities

```text
GmailAuthProvider
├── load client config
├── initiate consent
├── store token securely
├── refresh token
└── revoke / invalidate
```

---

# 51. Restricted scope consideration

Le scope Gmail donnant accès au contenu des messages est fortement sensible.

L’architecture doit donc supporter deux contextes.

## 51.1 Personal / development usage

```text
local OAuth client
test user
private connector
```

---

## 51.2 Distributed / multi-user product

Peut nécessiter :

```text
OAuth verification
privacy disclosures
security assessment depending on deployment/use
```

Le connecteur ne doit pas supposer que les contraintes du mode local suffisent à un produit SaaS.

---

# 52. Secret storage

Abstraction recommandée :

```python
class CredentialStore(Protocol):
    def load(account_id): ...
    def save(account_id, credentials): ...
    def delete(account_id): ...
```

Adapters possibles :

```text
Encrypted Local File
Environment
Cloud Secret Manager
Vault
```

---

# 53. Logging security

Par défaut, les logs ne doivent jamais exposer :

```text
message body
subject
email addresses
OAuth token
attachment content
raw MIME
```

---

# 54. Safe logging identifiers

Utiliser principalement :

```text
run_id
account_id
message_id
thread_id
history_id
attachment_id
```

---

# 55. Observability architecture

```text
Connector
   │
   ├── Logs
   ├── Metrics
   ├── Traces
   └── Run Summary
```

---

# 56. Metrics

## 56.1 Source metrics

```text
gmail_api_calls_total
gmail_api_errors_total
gmail_rate_limit_events_total
gmail_retry_total
```

---

## 56.2 Pipeline metrics

```text
messages_discovered_total
messages_processed_total
messages_failed_total
attachments_processed_total
history_events_total
```

---

## 56.3 State metrics

```text
checkpoint_history_id
last_success_timestamp
watch_expiration_timestamp
reconciliation_drift
```

---

# 57. Run context

Toutes les opérations d’un run doivent partager :

```text
RunContext
├── run_id
├── account_id
├── mode
├── started_at
├── dataset_version
└── trace_id
```

---

# 58. Reconciliation architecture

Le moteur de réconciliation compare source et cible.

```text
Gmail Source State
       │
       ▼
Source Inventory
       │
       │ compare
       ▼
Local Inventory
       │
       ▼
Reconciliation Report
```

---

# 59. Reconciliation levels

## Level 1 — Counts

```text
messages total
threads total
```

---

## Level 2 — IDs

```text
source ids
vs
local ids
```

---

## Level 3 — Metadata

```text
labels
thread associations
attachment references
```

---

## Level 4 — Content

Optionnel et coûteux :

```text
hash / content verification
```

---

# 60. Reconciliation decisions

```text
NO_ACTION
REPAIR
REFETCH_MESSAGE
REFETCH_ATTACHMENT
REBUILD_RELATION
FULL_RESYNC
```

---

# 61. Data quality architecture

La validation se fait en plusieurs niveaux.

```text
API contract
    ↓
RAW validation
    ↓
Normalization validation
    ↓
Referential validation
    ↓
Publication validation
```

---

# 62. Validation rules

Exemples :

```text
message_id non-null
account_id non-null
thread_id present where expected
recipient relation references message
attachment references message
history event type recognized
dataset version manifest exists
```

---

# 63. Quality report

Chaque version peut contenir :

```text
quality/report.json
```

avec :

```text
errors
warnings
counts
duplicate checks
referential checks
parsing failures
```

---

# 64. Package structure cible

```text
src/
└── gmail_ingestion/
    ├── __init__.py
    │
    ├── api/
    │   ├── client.py
    │   ├── auth.py
    │   ├── profile.py
    │   ├── labels.py
    │   ├── messages.py
    │   ├── threads.py
    │   ├── history.py
    │   ├── attachments.py
    │   └── watch.py
    │
    ├── domain/
    │   ├── account.py
    │   ├── message.py
    │   ├── thread.py
    │   ├── label.py
    │   ├── mime.py
    │   ├── attachment.py
    │   ├── history.py
    │   ├── checkpoint.py
    │   └── watch.py
    │
    ├── application/
    │   ├── full_sync.py
    │   ├── incremental_sync.py
    │   ├── range_sync.py
    │   ├── filtered_sync.py
    │   ├── reconciliation.py
    │   └── watch_lifecycle.py
    │
    ├── normalization/
    │   ├── message_normalizer.py
    │   ├── address_parser.py
    │   ├── header_parser.py
    │   ├── mime_walker.py
    │   └── history_mapper.py
    │
    ├── persistence/
    │   ├── raw_writer.py
    │   ├── normalized_writer.py
    │   ├── checkpoint_store.py
    │   └── attachment_store.py
    │
    ├── pyingestkit/
    │   ├── connector.py
    │   ├── manifest.py
    │   ├── versioning.py
    │   └── storage.py
    │
    ├── observability/
    │   ├── logging.py
    │   ├── metrics.py
    │   └── run_summary.py
    │
    ├── config/
    │   ├── models.py
    │   └── loader.py
    │
    ├── cli/
    │   └── main.py
    │
    └── exceptions.py
```

---

# 65. Alternative monorepo layout

Si les connecteurs sont hébergés directement dans PyIngestKit :

```text
src/
└── pyingestkit/
    └── connectors/
        └── gmail/
            ├── api/
            ├── domain/
            ├── sync/
            ├── normalization/
            └── connector.py
```

Le choix exact dépendra du modèle de distribution retenu pour les autres connecteurs.

---

# 66. Dependency rule

Les dépendances doivent pointer vers l’intérieur.

```text
CLI
 ↓
Application
 ↓
Domain
```

Les adapters dépendent des ports :

```text
Google Adapter ─────► Domain/Application Ports
Storage Adapter ────► Persistence Ports
```

Le domaine ne dépend jamais du SDK Google.

---

# 67. Architecture hexagonale simplifiée

```text
                     ┌──────────────┐
                     │     CLI      │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │ Application  │
                     └──────┬───────┘
                            │
                    ┌───────▼───────┐
                    │    Domain     │
                    └───────┬───────┘
                            │ Ports
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
         Gmail API      Storage        Observability
          Adapter        Adapter          Adapter
```

---

# 68. Configuration architecture

La configuration doit être :

```text
typed
validated
externalized
serializable
```

---

# 69. Configuration example

```yaml
connector:
  name: gmail
  account: me

auth:
  scopes:
    - https://www.googleapis.com/auth/gmail.readonly

ingestion:
  mode: incremental
  include_spam_trash: false
  message_format: full

filters:
  query: null
  label_ids: []

attachments:
  mode: metadata_only
  max_size_bytes: null

concurrency:
  message_workers: 4
  attachment_workers: 2

retry:
  max_attempts: 5
  backoff: exponential
  jitter: true

checkpoint:
  backend: filesystem

storage:
  backend: filesystem
  root: ./data/gmail

versioning:
  enabled: true

observability:
  log_level: INFO
```

---

# 70. Configuration validation

Exemples :

```text
mode=incremental
requires checkpoint or bootstrap fallback
```

```text
attachments.mode=download
requires attachment storage
```

```text
cdc.enabled=true
requires pubsub configuration
```

---

# 71. CLI architecture

La CLI est un adapter applicatif.

```text
gmail-ingest
├── auth
├── profile
├── labels
├── snapshot
├── incremental
├── range
├── reconcile
├── status
└── watch
```

---

# 72. CLI examples

```bash
gmail-ingest snapshot
```

```bash
gmail-ingest incremental
```

```bash
gmail-ingest range \
  --after 2026-10-01 \
  --before 2026-10-24
```

```bash
gmail-ingest reconcile
```

---

# 73. Library API

Le connecteur doit aussi être utilisable sans CLI.

Exemple conceptuel :

```python
connector = GmailConnector(config)

result = connector.run(
    mode="incremental"
)
```

---

# 74. Scheduler integration

Le connecteur doit être orchestrable via :

```text
cron
Airflow
Dagster
Prefect
PyScheduleKit
Kubernetes CronJob
CI scheduler
custom orchestrator
```

sans dépendre directement d’un orchestrateur.

---

# 75. Deployment modes

## 75.1 Local

```text
Python CLI
+
local OAuth
+
filesystem
```

---

## 75.2 Server scheduled

```text
Container
+
persistent credentials
+
S3
+
scheduler
```

---

## 75.3 Event-driven

```text
Pub/Sub
+
consumer
+
incremental worker
+
object storage
```

---

## 75.4 Multi-account future

```text
Control Plane
   ↓
Account Queue
   ↓
Worker Pool
   ↓
isolated mailbox runs
```

---

# 76. Multi-account isolation

Même si le MVP est mono-compte, l’architecture doit éviter de bloquer une future extension.

Chaque run est scindé par :

```text
account_id
```

Chaque checkpoint appartient à :

```text
one account
```

Chaque version appartient à :

```text
one account dataset namespace
```

---

# 77. Data partitioning

Partitionnement recommandé pour les datasets volumineux :

```text
account_id
year
month
```

ou :

```text
internal_date
```

selon le dataset.

Les partitions ne doivent pas devenir la clé métier.

---

# 78. Message mutation model

Un message Gmail peut changer sans changer d’identifiant.

Exemples :

```text
labels
historyId
state
```

Le modèle normalized peut utiliser :

```text
current state
```

tout en conservant :

```text
history_events
```

pour les mutations.

---

# 79. SCD considerations

Les messages eux-mêmes ne nécessitent pas forcément un SCD Type 2 complet.

Les changements sont déjà traçables via :

```text
history_events
```

Un futur besoin analytique peut ajouter un historisé spécialisé.

---

# 80. Deletion architecture

```text
messageDeleted
      ↓
History Event
      ↓
Message Tombstone
      ↓
Normalized current state updated
```

Le RAW historique reste conservé selon la politique de rétention.

---

# 81. Tombstone model

```text
message_id
account_id
is_deleted
deleted_at
deletion_history_id
```

---

# 82. Data lifecycle

```text
DISCOVERED
    ↓
FETCHED
    ↓
RAW_WRITTEN
    ↓
NORMALIZED
    ↓
VALIDATED
    ↓
VERSIONED
    ↓
PUBLISHED
```

---

# 83. Failure lifecycle

```text
DISCOVERED
    ↓
FETCH_ERROR
    ↓
RETRYING
    │
    ├── success → FETCHED
    │
    └── exhausted → QUARANTINED
```

---

# 84. Quarantine architecture

Les objets problématiques doivent pouvoir être isolés.

```text
quarantine/
├── messages/
├── attachments/
└── parsing/
```

avec métadonnées :

```text
run_id
object_id
error_type
error_message
attempts
last_attempt_at
```

---

# 85. Reprocessing

Un objet quarantiné peut être rejoué sans refaire tout le snapshot.

```text
gmail-ingest replay-message <id>
```

ou via un use case interne.

---

# 86. Version compatibility

Les artifacts doivent contenir :

```text
connector_version
manifest_schema_version
normalized_schema_version
```

afin de distinguer :

```text
code version
metadata version
data model version
```

---

# 87. Schema evolution

Les changements du modèle normalized doivent être :

```text
explicit
versioned
backward-aware
```

Exemple :

```text
normalized_schema_version = 1
```

---

# 88. Backward compatibility

Lorsqu’une nouvelle colonne est ajoutée :

```text
prefer additive evolution
```

Les ruptures de schéma doivent être réservées aux versions majeures du contrat dataset.

---

# 89. Testing architecture

```text
Unit
  ↓
Contract
  ↓
Integration
  ↓
Reliability
  ↓
End-to-End
```

---

# 90. Unit test targets

```text
MIME traversal
header parsing
address parsing
history mapping
checkpoint transitions
event reducer
configuration
manifest
```

---

# 91. Contract tests

Vérifier les hypothèses sur :

```text
Gmail API DTO shapes
PyIngestKit ports
DatasetVersionStore
PublishedDataset
```

---

# 92. Integration tests

Utiliser :

```text
dedicated Gmail test mailbox
```

pour éviter de dépendre d’une boîte réelle de production.

Scénarios :

```text
new message
reply/thread
label change
delete
attachment
HTML
multipart
```

---

# 93. Reliability tests

```text
crash after RAW
crash before checkpoint commit
HTTP 429
HTTP 5xx
expired token
invalid historyId
attachment failure
duplicate notification
```

---

# 94. End-to-end proof

Le scénario cible est :

```text
Bootstrap
  ↓
Full Sync
  ↓
Publish V1
  ↓
Receive new mail
  ↓
Incremental
  ↓
Publish V2
  ↓
Label mutation
  ↓
Incremental
  ↓
Publish V3
  ↓
Invalidate history checkpoint
  ↓
Full Resync
  ↓
Publish V4
```

---

# 95. Architecture decisions

## ADR-001 — Official Gmail API

Utiliser l’API Gmail officielle comme interface source.

**Raison :**

- modèle Gmail natif ;
- history ;
- labels ;
- threads ;
- watch ;
- OAuth officiel.

---

## ADR-002 — Read-only core

Le cœur d’ingestion reste read-only.

**Raison :**

- sécurité ;
- moindre privilège ;
- séparation ingestion/action.

---

## ADR-003 — RAW immutable

Le RAW publié est immuable.

**Raison :**

- reproductibilité ;
- audit ;
- reprocessing.

---

## ADR-004 — historyId as incremental cursor

Utiliser `historyId` comme curseur incrémental Gmail.

**Raison :**

- mécanisme natif ;
- évite full scan permanent.

---

## ADR-005 — Full Sync fallback

Un checkpoint invalide déclenche un Full Sync.

**Raison :**

- comportement attendu par le modèle Gmail ;
- sécurité des données.

---

## ADR-006 — Push as trigger only

Pub/Sub ne devient pas la source de vérité.

**Raison :**

- pertes et duplications possibles ;
- `history.list` permet la reconstruction.

---

## ADR-007 — Attachments separate

Les pièces jointes sont stockées séparément du dataset tabulaire.

**Raison :**

- volume ;
- type binaire ;
- déduplication future.

---

## ADR-008 — Generic capabilities remain in PyIngestKit

Retry, storage, versioning et publication restent hors du domaine Gmail.

---

# 96. Anti-patterns à éviter

## 96.1 Full scan every run

```text
❌ list all messages daily
```

---

## 96.2 Checkpoint before persistence

```text
❌ advance historyId
before data is safely stored
```

---

## 96.3 Flatten MIME immediately

```text
❌ discard MIME tree
```

---

## 96.4 Log message content

```text
❌ logger.info(message_body)
```

---

## 96.5 Store binaries in Parquet

```text
❌ huge attachment bytes inside analytical table
```

---

## 96.6 Pub/Sub-only consistency

```text
❌ assume every notification is delivered
```

---

## 96.7 Gmail-specific logic in PyIngestKit core

```text
❌ if connector == "gmail":
```

dans le cœur générique.

---

# 97. Operational runbooks anticipated

Les runbooks suivants devront être produits avant production :

```text
AUTH_TOKEN_EXPIRED
GMAIL_RATE_LIMIT
INVALID_HISTORY_ID
FULL_RESYNC
STORAGE_FAILURE
ATTACHMENT_FAILURE
PUBSUB_WATCH_EXPIRED
RECONCILIATION_DRIFT
```

---

# 98. SLO concepts

Les SLO exacts seront définis plus tard.

Exemples :

```text
successful scheduled runs
incremental freshness
maximum checkpoint lag
reconciliation drift
failed object rate
```

---

# 99. Cost architecture

Les principaux coûts proviennent de :

```text
API usage
network transfer
attachment storage
object storage operations
compute
```

Le cœur du pipeline n’introduit aucun coût LLM.

---

# 100. Cost controls

```text
incremental sync
metadata-only attachments
bounded concurrency
filtering
retention policies
content deduplication
```

---

# 101. AI boundary

L’IA se situe après publication.

```text
Gmail
  ↓
Ingestion
  ↓
Published Dataset
  ↓
Optional Enrichment
     ├── classification
     ├── entities
     ├── summarization
     └── semantic indexing
```

---

# 102. Future Data Products

```text
Communication Activity
Correspondent Analytics
Response Time Analytics
Communication Graph
Attachment Analytics
Topic Analytics
Knowledge Retrieval
```

---

# 103. Architecture roadmap view

La progression naturelle de l’architecture est :

```text
Foundation
   ↓
Full Snapshot
   ↓
Normalization
   ↓
Incremental
   ↓
Reliability
   ↓
Versioning
   ↓
CDC
   ↓
Production Qualification
```

---

# 104. Component maturity levels

| Composant | MVP | Production |
|---|---|---|
| OAuth | requis | hardened |
| Profile | requis | stable |
| Labels | requis | stable |
| Messages | requis | stable |
| MIME | requis | hardened |
| Attachments | metadata | configurable download |
| Full Sync | requis | optimized |
| Incremental | requis | hardened |
| CDC | optionnel | supported |
| Reconciliation | basic | advanced |
| Filesystem | requis | supported |
| S3 | optionnel | supported |
| Observability | basic | production-grade |

---

# 105. Minimal viable architecture

Le MVP peut être réduit à :

```text
Gmail API
   ↓
GmailApiClient
   ↓
FullSync / IncrementalSync
   ↓
RAW Writer
   ↓
Normalizer
   ↓
Filesystem
   ↓
Manifest
   ↓
Checkpoint
```

mais il doit respecter dès le départ les frontières permettant l’architecture cible.

---

# 106. Production target architecture

```text
                             ┌───────────────────────┐
                             │      Scheduler        │
                             └──────────┬────────────┘
                                        │
                                        ▼
┌────────────────┐      ┌────────────────────────────────────┐
│ Google Pub/Sub │─────►│ Gmail Ingestion Application        │
└────────────────┘      │                                    │
                        │ Full / Incremental / Reconcile      │
                        └───────────────┬────────────────────┘
                                        │
                              ┌─────────▼───────────┐
                              │ Gmail Domain Layer │
                              └─────────┬───────────┘
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
              Gmail API          PyIngestKit           Observability
                    │                   │
                    │            ┌──────┴───────┐
                    │            ▼              ▼
                    │         Versioning       Storage
                    │                           │
                    │                    ┌──────┴───────┐
                    │                    ▼              ▼
                    │                 Local             S3
                    │
                    ▼
               Google APIs
```

---

# 107. Sequence — Full Sync

```text
Scheduler        Connector       Gmail API       Storage       VersionStore
   │                │               │               │               │
   │ run full       │               │               │               │
   ├───────────────►│               │               │               │
   │                │ getProfile    │               │               │
   │                ├──────────────►│               │               │
   │                │◄──────────────┤               │               │
   │                │ listMessages  │               │               │
   │                ├──────────────►│               │               │
   │                │◄──────────────┤               │               │
   │                │ getMessage*   │               │               │
   │                ├──────────────►│               │               │
   │                │◄──────────────┤               │               │
   │                │ write RAW     │               │               │
   │                ├───────────────────────────────►│               │
   │                │ normalize     │               │               │
   │                │ write normalized             │               │
   │                ├───────────────────────────────►│               │
   │                │ create version                               │
   │                ├──────────────────────────────────────────────►│
   │                │ publish                                      │
   │                ├──────────────────────────────────────────────►│
   │                │ commit checkpoint                            │
   │                │               │               │               │
   │◄───────────────┤ success       │               │               │
```

---

# 108. Sequence — Incremental

```text
Scheduler      Connector      Checkpoint      Gmail API       Storage
   │              │               │               │               │
   │ incremental  │               │               │               │
   ├─────────────►│               │               │               │
   │              │ load          │               │               │
   │              ├──────────────►│               │               │
   │              │◄──────────────┤ H100          │               │
   │              │ history(H100) │               │               │
   │              ├───────────────────────────────►│               │
   │              │◄───────────────────────────────┤ events/H125   │
   │              │ hydrate msgs  │               │               │
   │              ├───────────────────────────────►│               │
   │              │◄───────────────────────────────┤               │
   │              │ write changes │               │               │
   │              ├───────────────────────────────────────────────►│
   │              │ candidate H125│               │               │
   │              ├──────────────►│               │               │
   │              │ validate      │               │               │
   │              │ commit H125   │               │               │
   │              ├──────────────►│               │               │
   │◄─────────────┤ success       │               │               │
```

---

# 109. Sequence — Invalid Checkpoint

```text
Connector
   │
   │ history.list(H100)
   ▼
Gmail API
   │
   └── 404
        │
        ▼
Checkpoint invalid
        │
        ▼
Full Sync
        │
        ▼
New dataset version
        │
        ▼
New committed historyId
```

---

# 110. Sequence — Push notification

```text
Gmail
  │
  ▼
Pub/Sub
  │
  ▼
Push Consumer
  │
  ▼
Decode account + historyId
  │
  ▼
Schedule Incremental Sync
  │
  ▼
Read committed checkpoint
  │
  ▼
history.list
  │
  ▼
Apply changes
```

---

# 111. Repository documentation target

À terme, le projet devrait disposer au minimum de :

```text
docs/
├── specs/
│   ├── 00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN.md
│   ├── GMAIL_INGESTION_REQUIREMENTS_ANALYSIS.md
│   ├── 01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE.md
│   ├── 02_GMAIL_INGESTION_RELEASE_AND_VERSIONING_STRATEGY.md
│   ├── 03_GMAIL_INGESTION_DATA_MODEL_AND_SOURCE_CATALOG.md
│   ├── 04_GMAIL_INGESTION_SYNC_AND_CHECKPOINT_STRATEGY.md
│   ├── 05_GMAIL_INGESTION_SECURITY_AND_OAUTH_MODEL.md
│   └── 06_GMAIL_INGESTION_RECONCILIATION_AND_RELIABILITY.md
│
└── runbooks/
    ├── INVALID_HISTORY_ID.md
    ├── FULL_RESYNC.md
    ├── RATE_LIMITING.md
    └── WATCH_RENEWAL.md
```

---

# 112. Architecture acceptance criteria

L’architecture est validée si elle permet de répondre positivement aux questions suivantes.

### Source

- Gmail est-il encapsulé derrière un adapter ?
- Le SDK Google fuit-il dans le domaine ?

### Sync

- Full Sync et Incremental sont-ils distincts ?
- Un checkpoint peut-il être invalidé ?
- Un Full Sync peut-il reconstruire la cible ?

### Data

- Le RAW est-il préservé ?
- Le normalized est-il rejouable depuis le RAW ?
- Les attachments sont-elles séparées ?

### Reliability

- Les retries sont-ils génériques ?
- Les exécutions sont-elles idempotentes ?
- Un crash ne peut-il pas avancer le checkpoint ?

### Versioning

- Chaque version est-elle identifiable ?
- Une version failed peut-elle être exclue de la publication ?
- Le PublishedDataset peut-il être restauré ?

### Security

- Aucun droit d’écriture Gmail n’est-il requis ?
- Les tokens sont-ils isolés ?
- Les logs restent-ils sans contenu sensible ?

### Extensibility

- Le stockage peut-il passer de local à S3 ?
- Le CDC peut-il être ajouté sans réécrire le sync engine ?
- Le multi-account est-il possible sans casser le modèle ?

---

# 113. Architecture Quality Attributes

| Attribut | Réponse architecturale |
|---|---|
| Reliability | checkpoints transactionnels + reconciliation |
| Recoverability | Full Sync + replay ciblé |
| Scalability | pagination + bounded concurrency |
| Security | OAuth read-only + secret isolation |
| Auditability | RAW + manifest + versioning |
| Portability | storage abstraction |
| Maintainability | layers + ports/adapters |
| Extensibility | strategy pattern pour modes de sync |
| Cost control | incremental + attachment policies |
| Testability | source adapters isolés |

---

# 114. Non-goals

Cette architecture ne cherche pas à devenir :

```text
Email Client
CRM
LLM Agent
Mail Automation Tool
Marketing Platform
```

Elle cherche uniquement à devenir :

```text
Reliable Gmail Data Connector
```

---

# 115. Vision à long terme

Le connecteur Gmail doit devenir un exemple de connecteur **stateful incremental** dans PyIngestKit.

Cette catégorie pourra ensuite servir de pattern à :

```text
Google Drive Changes API
Microsoft Graph Delta
Slack Events
GitHub Events
Notion incremental sync
Calendar sync tokens
```

Le pattern générique pourrait être :

```text
Snapshot
   ↓
Cursor
   ↓
Incremental Events
   ↓
Checkpoint
   ↓
Recovery
   ↓
Reconciliation
```

---

# 116. Reusable PyIngestKit opportunities

L’implémentation Gmail peut faire émerger des primitives génériques.

Exemples :

```text
CursorCheckpointStore
IncrementalSyncStrategy
ReconciliationReport
BinaryArtifactStore
EventReducer
SnapshotCatchUpStrategy
```

Ces primitives ne doivent remonter dans PyIngestKit que si elles sont réellement indépendantes de Gmail.

---

# 117. Recommended implementation boundary

Le principe est :

```text
build generic capability once
but only after genericity is proven
```

Éviter :

```text
premature abstraction
```

Le connecteur Gmail peut d’abord porter une implémentation locale d’un concept avant extraction vers PyIngestKit lors du second use case comparable.

---

# 118. Architecture Summary

La cible peut être résumée en trois systèmes.

```text
┌─────────────────────────────┐
│          GMAIL              │
│                             │
│ Source + Change History     │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│      GMAIL CONNECTOR        │
│                             │
│ Gmail semantics             │
│ Sync strategies             │
│ MIME / History / Watch      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│        PYINGESTKIT          │
│                             │
│ Storage                     │
│ Versioning                  │
│ Publication                 │
│ Lifecycle                   │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│     VERSIONED DATASET       │
│                             │
│ RAW                         │
│ Normalized                  │
│ Manifests                   │
│ Checkpoints                 │
└─────────────────────────────┘
```

---

# 119. Architecture Decision Summary

```text
Gmail API              → official source interface
OAuth                  → read-only first
RAW                    → immutable
Normalized             → Parquet-oriented
Incremental            → historyId
Checkpoint             → commit after persistence
Invalid historyId      → Full Sync
Pub/Sub                → trigger only
Attachments            → separate binary flow
Storage                → PyIngestKit abstraction
Versioning             → DatasetVersionStore
Publication            → PublishedDataset
Reconciliation         → first-class capability
LLM                    → downstream only
```

---

# 120. Prochaine étape

Le prochain document recommandé est :

```text
02_GMAIL_INGESTION_RELEASE_AND_VERSIONING_STRATEGY.md
```

Il devra transformer cette architecture en lots implémentables.

Il définira notamment :

```text
0.0.x
Repository Bootstrap

0.1.x
Authentication + Core Snapshot

0.2.x
Normalization

0.3.x
Filtered / Range ingestion

0.4.x
Incremental History Sync

0.5.x
CDC / Pub/Sub

0.6.x
Reconciliation & Reliability

0.7.x
Dataset Versioning / Object Storage

0.8.x
Analytics

0.9.x
Advanced / Multi-account

1.0.0
Production Qualified
```

---

# 121. Références techniques officielles

Les décisions Gmail de cette architecture s’appuient sur les contrats publics de la Gmail API :

- Gmail API  
  `https://developers.google.com/workspace/gmail/api`

- Synchronization guide  
  `https://developers.google.com/workspace/gmail/api/guides/sync`

- Push notifications  
  `https://developers.google.com/workspace/gmail/api/guides/push`

- OAuth scopes  
  `https://developers.google.com/workspace/gmail/api/auth/scopes`

- Usage limits  
  `https://developers.google.com/workspace/gmail/api/reference/quota`

- REST resources  
  `https://developers.google.com/workspace/gmail/api/reference/rest`

Les quotas et politiques Google pouvant évoluer, ils doivent rester externalisés et ne pas être codés en dur.

---

# 122. Conclusion

L’architecture retenue fait de Gmail un connecteur de synchronisation stateful plutôt qu’un simple extracteur.

La chaîne fondamentale est :

```text
Gmail
  ↓
Snapshot
  ↓
RAW
  ↓
Normalize
  ↓
Version
  ↓
Checkpoint
  ↓
Incremental
  ↓
Reconcile
```

La séparation essentielle reste :

```text
Gmail-specific knowledge
        │
        ▼
Gmail Connector

Generic ingestion capabilities
        │
        ▼
PyIngestKit
```

Cette frontière permet de construire un connecteur Gmail robuste aujourd’hui tout en enrichissant progressivement le socle PyIngestKit pour les futures sources incrémentales.

> **Le résultat recherché n’est pas de télécharger une boîte mail.  
> Le résultat recherché est de maintenir dans le temps un dataset Gmail fiable, vérifiable, versionné et reconstructible.**