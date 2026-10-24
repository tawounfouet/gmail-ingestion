# 00_GMAIL_INGESTION_EXPRESSION_DU_BESOIN

## 1. Contexte

Dans le cadre de la construction d’une plateforme d’ingestion de données fondée sur **PyIngestKit**, plusieurs connecteurs spécialisés sont progressivement mis en place afin d’ingérer des données provenant de sources hétérogènes selon un même modèle d’industrialisation.

Le connecteur **Strava** a permis de poser un premier pattern de référence autour de :

- l’authentification à une API externe ;
- l’extraction initiale complète ;
- l’ingestion incrémentale ;
- la gestion des curseurs et checkpoints ;
- la conservation d’une couche RAW ;
- la normalisation des données ;
- le versioning des datasets ;
- la génération de manifests ;
- la publication de datasets ;
- la réconciliation ;
- la fiabilité et la reprise après incident ;
- la production éventuelle de couches analytiques.

Le besoin est désormais d’appliquer cette même philosophie à **Gmail**, afin de disposer d’un connecteur robuste, reproductible et industrialisable permettant d’ingérer les données d’une boîte Gmail ou Google Workspace.

Gmail ne doit pas être considéré comme une simple liste d’e-mails à télécharger.

Une boîte Gmail constitue un système de données riche comprenant notamment :

- un compte ;
- des labels ;
- des messages ;
- des conversations (`threads`) ;
- des en-têtes ;
- des corps MIME ;
- des pièces jointes ;
- des relations entre messages ;
- un historique de modifications ;
- des événements d’ajout, de suppression ou de changement de labels.

L’objectif est donc de construire un véritable **pipeline de données Gmail**, et non un simple script d’export.

---

## 2. Problématique

Les données présentes dans Gmail sont généralement enfermées dans l’interface utilisateur et difficiles à exploiter de manière systématique pour des usages Data Engineering, analytiques ou applicatifs.

Les besoins potentiels sont pourtant nombreux :

- historiser une boîte mail ;
- centraliser les communications dans un Data Lake ;
- rechercher les échanges de manière structurée ;
- analyser les volumes de communication ;
- reconstituer des fils de conversation ;
- identifier les correspondants et organisations ;
- mesurer des délais de réponse ;
- analyser les pièces jointes ;
- produire des statistiques sur les communications ;
- alimenter des moteurs de recherche ;
- enrichir ultérieurement les messages avec du NLP ou des LLM ;
- intégrer Gmail dans une plateforme personnelle ou d’entreprise.

Une solution naïve consistant à relire l’intégralité de la boîte à chaque exécution serait :

- coûteuse ;
- lente ;
- difficilement scalable ;
- peu robuste ;
- peu compatible avec une exécution quotidienne ou quasi temps réel ;
- difficile à auditer ;
- incapable de garantir une ingestion reproductible.

Le connecteur doit donc offrir une approche industrielle basée sur :

```text
Full Snapshot
    ↓
Checkpoint
    ↓
Incremental Sync
    ↓
Change Detection
    ↓
Reconciliation
    ↓
Dataset Versioning
```

---

## 3. Expression synthétique du besoin

Le besoin est de disposer d’un **connecteur Gmail basé sur PyIngestKit** capable d’extraire, historiser, normaliser, versionner et publier les données Gmail de manière fiable, incrémentale et reproductible.

Le connecteur doit pouvoir fonctionner selon plusieurs modes :

```text
Gmail
 ├── Full Snapshot
 ├── Incremental
 ├── Date Range
 ├── Search / Filtered
 ├── Reconciliation
 └── Push / CDC
```

La solution doit séparer clairement :

```text
COLLECTE
   ↓
RAW
   ↓
NORMALISATION
   ↓
DATASET VERSION
   ↓
PUBLISHED DATASET
   ↓
ANALYTICS / ENRICHMENT
```

Les traitements d’intelligence artificielle ne doivent pas faire partie du cœur de l’ingestion.

---

## 4. Objectifs

### 4.1 Objectif principal

Construire un connecteur Gmail industrialisable permettant d’ingérer les données d’une boîte Gmail ou Google Workspace avec le même niveau de robustesse que les autres connecteurs basés sur PyIngestKit.

---

### 4.2 Objectifs fonctionnels

Le connecteur devra permettre de récupérer au minimum :

- les informations du compte Gmail ;
- les labels ;
- les messages ;
- les threads ;
- les headers ;
- les corps texte ;
- les corps HTML ;
- les structures MIME ;
- les pièces jointes ;
- les identifiants Gmail ;
- les dates internes ;
- les informations d’expéditeur et de destinataires ;
- les changements enregistrés par Gmail.

Il devra également permettre :

- une ingestion complète initiale ;
- une ingestion incrémentale ;
- une ingestion sur plage de dates ;
- une ingestion basée sur une requête Gmail ;
- la reprise après interruption ;
- la détection des changements ;
- la réconciliation entre Gmail et le dataset local.

---

### 4.3 Objectifs Data Engineering

Le connecteur doit permettre de transformer Gmail en source de données exploitable selon le modèle :

```text
External Source
      ↓
Connector
      ↓
Extraction
      ↓
Raw Dataset
      ↓
Normalization
      ↓
Validated Dataset
      ↓
Versioned Dataset
      ↓
Published Dataset
```

Les données produites doivent être :

- auditables ;
- reproductibles ;
- versionnées ;
- partitionnables ;
- transportables ;
- exploitables en local ou sur Object Storage ;
- indépendantes du moteur analytique final.

---

## 5. Utilisateurs et parties prenantes

### 5.1 Data Engineer

Le Data Engineer doit pouvoir :

- configurer le connecteur ;
- authentifier un compte Gmail ;
- lancer une ingestion ;
- relancer une ingestion interrompue ;
- connaître le checkpoint courant ;
- consulter les manifests ;
- inspecter les erreurs ;
- rejouer une ingestion ;
- réconcilier la source et le dataset.

---

### 5.2 Data Analyst

Le Data Analyst doit pouvoir exploiter des tables structurées telles que :

```text
gmail_messages
gmail_threads
gmail_labels
gmail_message_labels
gmail_addresses
gmail_attachments
gmail_history_events
```

sans avoir à interpréter directement la structure complexe de l’API Gmail.

---

### 5.3 Application / Data Product

Une application doit pouvoir consommer les datasets publiés sans connaître les détails du connecteur Gmail.

Exemples :

- moteur de recherche ;
- CRM personnel ;
- communication analytics ;
- knowledge base ;
- contact intelligence ;
- assistant personnel ;
- système d’archivage ;
- moteur de classification documentaire.

---

## 6. Source de données

La source principale est :

```text
Google Gmail API
```

Les principales ressources fonctionnelles sont :

```text
users
├── profile
├── labels
├── messages
├── threads
└── history
```

Les pièces jointes sont rattachées aux messages via les différentes parties MIME.

---

## 7. Authentification

Le connecteur doit utiliser le mécanisme d’authentification officiel de Google.

La solution devra supporter en priorité :

```text
OAuth 2.0
```

Le principe de moindre privilège doit être appliqué.

La première version du connecteur doit privilégier un scope en lecture seule.

Le connecteur ne doit pas nécessiter de droits permettant :

- l’envoi d’e-mails ;
- la suppression de messages ;
- la modification des messages ;
- la modification des labels ;

tant que ces fonctionnalités ne sont pas explicitement ajoutées à une version ultérieure.

---

## 8. Périmètre fonctionnel

### 8.1 Profil

Le dataset doit pouvoir conserver notamment :

- adresse e-mail ;
- nombre total de messages ;
- nombre total de threads ;
- identifiant d’historique courant.

---

### 8.2 Labels

Le connecteur doit récupérer :

- labels système ;
- labels utilisateur ;
- identifiants ;
- noms ;
- types ;
- visibilité éventuelle.

Les relations entre labels et messages doivent être conservées.

---

### 8.3 Messages

Chaque message doit être identifié de manière stable.

Le modèle cible doit notamment pouvoir représenter :

```text
message_id
thread_id
history_id
internal_date
from
to
cc
bcc
reply_to
subject
snippet
labels
headers
text_body
html_body
mime_type
size_estimate
```

---

### 8.4 Threads

Les threads doivent être représentés explicitement.

Le connecteur doit permettre de reconstruire :

```text
Thread
├── Message 1
├── Message 2
├── Message 3
└── ...
```

L’ordre chronologique doit pouvoir être déterminé.

---

### 8.5 Headers

Les principaux headers doivent être extraits sous une forme exploitable.

Exemples :

```text
From
To
Cc
Bcc
Subject
Date
Message-ID
In-Reply-To
References
Reply-To
Content-Type
```

La version RAW complète des headers doit également pouvoir être conservée.

---

### 8.6 Contenu MIME

La structure MIME ne doit pas être aplatie trop tôt.

Le connecteur doit être capable de représenter :

```text
multipart/*
text/plain
text/html
application/*
image/*
message/*
```

La couche RAW doit conserver suffisamment d’informations pour permettre un retraitement ultérieur.

---

### 8.7 Pièces jointes

Les pièces jointes doivent pouvoir être :

- détectées ;
- référencées ;
- téléchargées ;
- stockées séparément ;
- reliées au message d’origine.

Les métadonnées doivent contenir au minimum :

```text
attachment_id
message_id
filename
mime_type
size
content_hash
storage_uri
```

Le téléchargement des pièces jointes doit pouvoir être configurable afin d’éviter un coût inutile.

---

## 9. Modes d’ingestion

### 9.1 Full Snapshot

Le mode Full Snapshot permet de construire une représentation complète de la boîte.

Il est utilisé notamment pour :

- l’initialisation ;
- la reconstruction ;
- la réconciliation ;
- la récupération après perte du checkpoint.

---

### 9.2 Incremental Sync

Après le snapshot initial, le connecteur doit pouvoir récupérer uniquement les changements intervenus depuis la dernière ingestion.

Le pipeline doit maintenir un checkpoint basé sur l’historique Gmail.

Conceptuellement :

```text
Snapshot
   ↓
history_id = H100
   ↓
nouveaux changements
   ↓
history.list(startHistoryId=H100)
   ↓
H101
H102
H103
   ↓
checkpoint = H103
```

Le checkpoint ne doit être validé qu’après succès de l’ingestion correspondante.

---

### 9.3 Date Range

Le connecteur doit permettre l’ingestion d’une plage temporelle.

Exemples :

```text
2026-01-01 → 2026-01-31
last 7 days
last 30 days
```

---

### 9.4 Search / Filtered Ingestion

Le connecteur doit permettre d’exploiter les capacités de recherche Gmail.

Exemples :

```text
from:example.com
has:attachment
label:important
after:2026/01/01
before:2026/02/01
```

Ces filtres doivent pouvoir être enregistrés dans le manifest afin de connaître exactement le périmètre du dataset produit.

---

### 9.5 Push / CDC

Une version plus avancée doit permettre une ingestion pilotée par événements.

Architecture cible :

```text
Gmail
  ↓
users.watch
  ↓
Google Pub/Sub
  ↓
Notification
  ↓
historyId
  ↓
Incremental ingestion
```

Les notifications ne doivent pas être considérées comme la source de vérité.

Elles servent uniquement à déclencher l’interrogation de l’historique Gmail.

---

## 10. Gestion de l’incrémental

Le système doit conserver un état d’ingestion contenant notamment :

```text
account
last_successful_run
last_history_id
last_full_snapshot
dataset_version
published_version
```

Le système doit distinguer :

```text
candidate checkpoint
```

et :

```text
committed checkpoint
```

afin d’éviter de perdre des événements en cas d’échec.

---

## 11. Réconciliation

La réconciliation doit être un mécanisme natif.

Elle doit permettre de vérifier que la représentation locale reste cohérente avec Gmail.

Des contrôles pourront porter sur :

- nombre de messages ;
- nombre de threads ;
- messages manquants ;
- messages supprimés ;
- labels divergents ;
- pièces jointes manquantes ;
- incohérences de relations ;
- checkpoints invalides.

Le système doit pouvoir décider :

```text
Incremental Repair
```

ou :

```text
Full Resync
```

selon la gravité du problème.

---

## 12. Gestion des suppressions

Le dataset ne doit pas supprimer silencieusement une donnée historique.

Une suppression Gmail doit pouvoir être représentée comme un événement.

Exemple :

```text
message_id
deleted_at
source_event
history_id
```

Une stratégie de type tombstone doit pouvoir être utilisée.

---

## 13. Couche RAW

La couche RAW doit préserver la représentation source autant que possible.

Exemple :

```text
raw/
└── gmail/
    ├── profile/
    ├── labels/
    ├── messages/
    ├── threads/
    ├── history/
    └── attachments/
```

Un message pourra notamment être stocké sous la forme :

```text
messages/{message_id}.json
```

Une représentation MIME brute pourra être conservée lorsqu’elle est nécessaire.

---

## 14. Couche normalisée

Une couche normalisée doit permettre l’exploitation analytique.

Exemple :

```text
normalized/
├── messages.parquet
├── threads.parquet
├── labels.parquet
├── message_labels.parquet
├── addresses.parquet
├── recipients.parquet
├── attachments.parquet
└── history_events.parquet
```

---

## 15. Versioning

Chaque ingestion publiable doit produire une version identifiable.

Exemple :

```text
gmail/
└── account=<account>/
    └── versions/
        ├── 2026-10-24T010000Z/
        ├── 2026-10-24T020000Z/
        └── 2026-10-24T030000Z/
```

Le modèle PyIngestKit doit pouvoir s’appuyer sur :

```text
DatasetVersionStore
PublishedDataset
```

Le pointeur publié doit permettre d’identifier la dernière version qualifiée.

---

## 16. Manifest

Chaque ingestion doit générer un manifest.

Le manifest doit contenir notamment :

```text
connector
connector_version
account
run_id
started_at
completed_at
mode
query
date_range
input_checkpoint
output_checkpoint
message_count
thread_count
attachment_count
bytes_downloaded
errors
warnings
dataset_version
status
```

---

## 17. Stockage

Le connecteur doit être agnostique du stockage.

Il doit pouvoir fonctionner avec :

```text
Local Filesystem
S3 compatible Object Storage
AWS S3
MinIO
Hetzner Object Storage
Azure Blob Storage
```

selon les adapters disponibles dans PyIngestKit.

---

## 18. Organisation cible des données

Une structure logique possible est :

```text
gmail/
├── raw/
│   ├── profile/
│   ├── labels/
│   ├── messages/
│   ├── threads/
│   ├── history/
│   └── attachments/
│
├── normalized/
│   ├── messages/
│   ├── threads/
│   ├── labels/
│   ├── recipients/
│   └── attachments/
│
├── manifests/
├── checkpoints/
├── versions/
└── published/
```

---

## 19. Idempotence

Une ingestion rejouée avec le même périmètre ne doit pas créer arbitrairement des doublons.

L’idempotence doit être garantie à partir d’identifiants stables tels que :

```text
account_id
message_id
thread_id
attachment_id
history_id
```

---

## 20. Reprise après incident

Le pipeline doit pouvoir redémarrer après :

- coupure réseau ;
- expiration d’un token ;
- erreur API ;
- limitation de quota ;
- crash du processus ;
- erreur de stockage ;
- arrêt machine ;
- indisponibilité temporaire du stockage.

Il ne doit pas être nécessaire de recommencer systématiquement toute l’ingestion.

---

## 21. Rate Limits et quotas

Le connecteur doit respecter les quotas Gmail.

Il doit disposer d’une stratégie de :

```text
Retry
+
Backoff
+
Jitter
+
Rate Limiting
```

Les erreurs temporaires doivent être distinguées des erreurs définitives.

---

## 22. Observabilité

Chaque exécution doit produire des informations exploitables.

Exemples de métriques :

```text
messages_discovered
messages_fetched
messages_skipped
threads_fetched
attachments_fetched
bytes_downloaded
api_calls
api_errors
retries
duration
checkpoint_lag
```

Les logs doivent permettre de retrouver :

```text
run_id
account
message_id
thread_id
history_id
```

lorsque cela est pertinent.

---

## 23. Sécurité

Les données Gmail sont particulièrement sensibles.

Le connecteur doit appliquer les principes suivants :

- moindre privilège ;
- secrets hors du code ;
- tokens protégés ;
- aucune donnée sensible dans les logs ;
- chiffrement en transit ;
- possibilité de chiffrement au repos ;
- séparation des comptes ;
- contrôle des accès au stockage ;
- rotation ou révocation des credentials.

Les fichiers de secrets ne doivent jamais être versionnés.

Exemple :

```text
credentials.json
token.json
.env
```

doivent être exclus de Git.

---

## 24. Confidentialité et protection des données

Le connecteur doit permettre de mettre en œuvre des politiques de gouvernance.

Il doit notamment être possible de :

- limiter les comptes ingérés ;
- limiter les périodes ;
- limiter les labels ;
- exclure certaines catégories de messages ;
- désactiver le téléchargement des pièces jointes ;
- définir une durée de conservation ;
- supprimer un dataset local ;
- retracer l’origine des données.

---

## 25. Séparation ingestion / intelligence artificielle

Le cœur du connecteur ne doit pas dépendre d’un LLM.

Le pipeline de base doit rester :

```text
Gmail API
   ↓
Extraction
   ↓
Validation
   ↓
Normalization
   ↓
Persistence
   ↓
Versioning
```

Les traitements suivants doivent rester optionnels et placés en aval :

```text
Classification
Entity Extraction
Topic Detection
Summarization
Semantic Search
Action Item Detection
LLM Enrichment
```

Cette séparation garantit :

- reproductibilité ;
- maîtrise des coûts ;
- déterminisme ;
- auditabilité ;
- possibilité de changer de modèle IA.

---

## 26. Data Products futurs

Une fois le socle d’ingestion stabilisé, plusieurs Data Products pourront être construits.

### Communication Activity

```text
email_activity_daily
```

Mesures possibles :

- messages reçus ;
- messages envoyés ;
- threads actifs ;
- correspondants uniques ;
- pièces jointes.

---

### Correspondent Analytics

```text
correspondent_activity
```

Exemples :

- fréquence d’échange ;
- première interaction ;
- dernière interaction ;
- nombre de threads ;
- volume de messages.

---

### Response Time Analytics

```text
response_time
```

Exemples :

- temps moyen de réponse ;
- délai médian ;
- taux de réponse ;
- conversations sans réponse.

---

### Communication Network

Les relations :

```text
Person
  ↓
Email Address
  ↓
Domain
  ↓
Organization
```

pourront être utilisées pour construire un graphe de communication.

---

### Attachment Analytics

Possibilité d’analyser :

- volumes ;
- formats ;
- fréquences ;
- auteurs ;
- périodes ;
- doublons.

---

## 27. Hors périmètre initial

Les éléments suivants ne font pas partie du MVP :

- envoyer des e-mails ;
- répondre à des e-mails ;
- déplacer les messages ;
- modifier les labels ;
- supprimer des messages ;
- créer des brouillons ;
- automatiser des réponses ;
- classifier les e-mails avec un LLM ;
- produire des résumés IA ;
- exposer une interface utilisateur Gmail alternative.

Ces fonctionnalités pourront faire l’objet de lots ultérieurs distincts.

---

## 28. Contraintes techniques

Le projet doit respecter les principes suivants :

```text
Python
PyIngestKit
API officielle Gmail
OAuth 2.0
Configuration externalisée
Storage abstraction
Dataset versioning
Manifest
Checkpoint
Idempotence
Observability
Tests automatisés
```

Le connecteur doit être exploitable :

- en CLI ;
- en bibliothèque Python ;
- dans un scheduler ;
- dans un orchestrateur ;
- dans un conteneur.

---

## 29. Critères de succès

Le besoin sera considéré comme satisfait lorsqu’il sera possible de :

1. authentifier un compte Gmail en lecture seule ;
2. récupérer son profil ;
3. récupérer ses labels ;
4. réaliser un snapshot complet des messages ;
5. récupérer les threads ;
6. extraire les métadonnées et corps des messages ;
7. télécharger facultativement les pièces jointes ;
8. produire un dataset RAW ;
9. produire un dataset normalisé ;
10. générer un manifest ;
11. conserver un checkpoint ;
12. exécuter une ingestion incrémentale ;
13. reprendre après interruption ;
14. détecter un checkpoint invalide ;
15. déclencher une resynchronisation ;
16. publier une version qualifiée du dataset ;
17. exécuter le pipeline de manière répétable et idempotente.

---

## 30. Vision cible

La vision cible peut être résumée ainsi :

```text
                    GOOGLE GMAIL
                         │
                     OAuth 2.0
                         │
                    Gmail API
                         │
                ┌────────▼────────┐
                │ Gmail Connector │
                │   PyIngestKit   │
                └────────┬────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
 Full Snapshot      Incremental       Push / CDC
                         │
                         ▼
                      RAW DATA
                         │
                         ▼
                    VALIDATION
                         │
                         ▼
                  NORMALIZATION
                         │
                         ▼
                 DATASET VERSION
                         │
                         ▼
                 PUBLISHED DATASET
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Analytics    Search      AI/LLM
```

Le connecteur Gmail devient ainsi une nouvelle brique de la plateforme PyIngestKit, au même titre que les connecteurs Strava, GitHub ou les autres sources de données.

---

## 31. Principe directeur

Le principe directeur du projet est :

> **Gmail n’est pas seulement une boîte mail à exporter : c’est une source de données événementielle, relationnelle et documentaire qui doit pouvoir être ingérée comme un dataset versionné, auditable et reproductible.**

Le connecteur doit donc privilégier :

```text
Correctness
    >
Reliability
    >
Reproducibility
    >
Incrementality
    >
Performance
    >
Advanced Analytics
```

Les fonctions analytiques et IA viendront après la maîtrise complète de l’ingestion.

---

## 32. Prochain document recommandé

Le prochain document logique est :

```text
01_GMAIL_INGESTION_PROJECT_VISION_AND_ARCHITECTURE.md
```

Il détaillera notamment :

- l’architecture cible ;
- les composants ;
- les responsabilités ;
- les adapters ;
- les flux ;
- les couches RAW / Normalized / Published ;
- le modèle d’exécution ;
- le mécanisme de checkpoint ;
- le cycle de synchronisation ;
- l’intégration avec PyIngestKit.