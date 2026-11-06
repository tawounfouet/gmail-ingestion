# RC8 - Qualification de Current Reconciliation à partir de Gmail

Version logicielle cible : `0.5.0rc8` (candidate en qualification, **pas** la stable).
Issue de sortie : #29. PR d'implémentation : #33.

## 0. Invariants non négociables

- Le Full Snapshot initial `c394dcbb-f5da-4ae0-abb8-c9e6dcf81479`
  reste immuable (177 546 messages hors Spam et Corbeille, 711 segments).
- Les 4 runs History déjà committés, le checkpoint `15903183`, les 49
  captures de récupération datées et les 3 observations 404 sont conservés.
- Les 58 ajouts sans RAW d'origine sont **des exceptions à qualifier**, pas
  58 suppressions à inventer. Le code doit identifier les IDs distincts.
- Un nouveau `CURRENT_RECONCILIATION` est une **vue de source actuelle** et
  ne peut pas certifier rétroactivement le `HISTORICAL(15694719)`.
- Un candidat RC8 vit dans une racine de projection indépendante.
  Les scripts RC8 n'appellent jamais la promotion du pointeur Current.
- OAuth `gmail.readonly` exclusivement. Ne transmettre ni les IDs de messages,
  ni les RAW, ni les tokens OAuth ou fichiers privés au dépôt public.
- Le disque de l'opérateur n'avait que 8,6 Gio libres. Un output Current complet
  nécessite un volume de destination offrant au moins 20 Gio libres et 24 Gio
  de RAM actuellement disponible (minimum conservatoire, **pas** garantie).
  Un volume externe dédié peut être utilisé comme `--projection-root`.

## 1. Préparation et chemin des preuves privées

```bash
git switch main
git pull --ff-only
GMAIL_PY="/opt/homebrew/opt/python@3.14/bin/python3.14"
PRIV="$HOME/.local/share/gmail-ingestion-private"
mkdir -p "$PRIV"
chmod 700 "$PRIV"
df -h .
```

Repérer le fichier privé `source-*.json` créé par le premier inventaire
all-mail qui avait donné 181 200 IDs et `history_end=15904124`.
Le fichier existe déjà et **ne doit pas** être recapturé dans un Full Snapshot.
Définir manuellement son chemin absolu, par exemple :

```bash
ls -1 "$PRIV"/source-*.json
SOURCE_ALL="$PRIV/source-REMPLACER_HORODATAGE.json"
```

## 2. Inventaire source aligné hors Spam/Corbeille

Le nouveau script ne rescane pas 363 pages de messages. Il liste seulement
les IDs classés SPAM et TRASH, puis rejoue History depuis le premier inventaire.
Il vérifie les mutations pendant la lecture des deux labels.

```bash
SCOPED="$PRIV/rc8-scoped-$(date -u +%Y%m%dT%H%M%SZ).json"
env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/derive_scoped_inventory_from_all.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --previous-all-inventory "$SOURCE_ALL" \
  --evidence-out "$SCOPED" \
  --max-history-pages 200 \
  --max-label-pages 1000 \
  --page-size 500
```

**Si échec :** conserver le premier inventaire, ne pas synthétiser d'IDs et
ne pas supprimer l'historique. Les caps peuvent être augmentés avec justification
si les pages de Gmail sont valides. Si History a expiré, une décision explicite
de nouveau snapshot avec périmètre complet sera nécessaire : **pas d'auto-resync**.

## 3. Fermer la continuité History jusqu'à la source

Le checkpoint local initialement committé `15903183` est antérieur à
la source observée. Vérifier l'espace libre avant toute nouvelle écriture,
puis effectuer seulement un incremental strict, sans Full Snapshot :

```bash
df -h .
caffeinate -i gmail-ingest incremental \
  --account-id acct_84757009c00c40cf859cb796 \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --checkpoint-root .gmail-ingestion-control-live-0.5.0 \
  --max-results 500 \
  --no-auto-full-resync
```

Noter le NOUVEAU run UUID et sa sortie `committed_checkpoint`.
Le résultat doit couvrir `history_end` du `SCOPED`. S'il ne le couvre pas,
ne pas poursuivre. Si History expire, stop et décision NO-GO.

## 4. Plan de candidat isolé, sans données volumineuses

Remplacer `NOUVEAU_RUN_UUID` par l'UUID vérifié du rattrapage.

```bash
NEW_RUN="NOUVEAU_RUN_UUID"
PLAN="$PRIV/rc8-plan-$(date -u +%Y%m%dT%H%M%SZ).json"
env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/plan_rc8_current_candidate.py \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --account-id acct_84757009c00c40cf859cb796 \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --delta-run-id ab677d4e-be1e-40f0-80c7-5edcf5abc6fa \
  --delta-run-id 73153907-c8fa-46e6-884a-55bfe67ccb71 \
  --delta-run-id 807ad9f4-66a1-40e5-aadf-f18aefe850fc \
  --delta-run-id 8b133cc6-7ebe-4b3a-b26c-d309a058ca4c \
  --delta-run-id "$NEW_RUN" \
  --source-inventory "$SCOPED" \
  --candidate-evidence-out "$PLAN"
```

Résultat attendu : plan déterministe `rc8_...`, digest source, registre des
exceptions séparé, nombre distinct d'IDs, compteurs de rapprochement.
Le `PLAN` contient des IDs privés et ne doit jamais être publié.

## 5. Capturer uniquement les RAW source manquants

```bash
RECOVERY_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/capture_rc8_current_source_evidence.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --candidate-evidence "$PLAN" \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --run-id "$RECOVERY_ID" \
  --max-messages 200 \
  --max-payload-mib 256
```

Le script préserve les messages d'origine et écrit dans
`gmail.rc8_source_evidence`. En cas de message actuellement 404, contenu
récent postérieur au checkpoint source, ou mutation source concurrente, il
refuse le GO : refaire une observation cohérente plutôt que forger les données.

## 6. Matérialiser le candidat sur un volume suffisamment dimensionné

**Ne pas lancer avec les 8,6 Gio libres rapportés sur le Mac.**
Préférer un volume externe dédié, avec un emplacement de destination
déjà créé. L'allocation RAM réelle est mesurée, pas inférée des 96 Gio installés.

```bash
OUT_ROOT="/Volumes/DEDICATED_DATA/rc8-current-candidates"
mkdir -p "$OUT_ROOT"
df -h "$OUT_ROOT"

env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/materialize_rc8_current_candidate.py \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --projection-root "$OUT_ROOT" \
  --account-id acct_84757009c00c40cf859cb796 \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --delta-run-id ab677d4e-be1e-40f0-80c7-5edcf5abc6fa \
  --delta-run-id 73153907-c8fa-46e6-884a-55bfe67ccb71 \
  --delta-run-id 807ad9f4-66a1-40e5-aadf-f18aefe850fc \
  --delta-run-id 8b133cc6-7ebe-4b3a-b26c-d309a058ca4c \
  --delta-run-id "$NEW_RUN" \
  --source-inventory "$SCOPED" \
  --recovery-run-id "$RECOVERY_ID" \
  --min-free-gib 20 \
  --min-available-ram-gib 24
```

Il calcule un `proj_...` déterministe, écrit des Parquet et des SHA-256 dans
une racine indépendante, vérifie un replay déterministe, mais **ne promeut
pas** Current. Il faut conserver le `projection_id` retourné.

## 7. Vérification indépendante sur source Gmail réelle

Le contrôle lit les tables Parquet par batches, vérifie les empreintes
physiques et choisit un échantillon déterministe de messages.
Il appelle `messages.get(full)`, compare les six tables normalisées, les
références de threads et les structures MIME/attachments, et vérifie que
les messages contrôlés n'ont pas muté depuis l'inventaire.

```bash
PROJECTION_ID="proj_REMPLACER_IDENTIFIANT"
env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/verify_rc8_source_parity.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --projection-root "$OUT_ROOT" \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --projection-id "$PROJECTION_ID" \
  --source-inventory "$SCOPED" \
  --sample-size 32
```

**Un PASS de 32 messages ne qualifie pas 180 000 messages.** Il prouve une
parité de source sur un échantillon seulement. La conformité exhaustive
messages/labels/threads/MIME, l'intégrité de tous les artifacts et une
validation de clôture à frontière constante sont encore nécessaires avant
toute stable.

## 8. Matrice d'acceptation et décision

| Gate | Contrat | État avant l'exécution locale |
| --- | --- | --- |
| G1 | Scope Gmail source compatible + History durable | PENDING |
| G2 | Exceptions sans RAW classées et prouvées | PENDING |
| G3 | Parquet Current isolé, identité versionnée | PENDING |
| G4 | Parité des IDs, labels, threads, MIME | PENDING |
| G5 | Digest rebuild + checksums + RAM / disque | PENDING |
| G6 | Exact-main CI RC8 + boîte réelle + décision | PENDING |

Le résultat automatique de préparation d'un candidat ne peut pas retourner
`stable_release_qualified=true`. Il ne permet pas d'appeler le projecteur
historique `gmail-ingest project --promote`.

Le projet doit **conserver 0.5.0 stable en NO-GO** jusqu'à une décision explicite,
avec preuves réelles anonymisées, sur l'issue #29.


## 2026-11-07 - Capture des 50 exceptions bloquée par un 404 Gmail

Le plan local a été vérifié avec `candidate_id=rc8_7ddada510a3242aebb7d54b77c830a46`,
source `15906422`, History committé `15906426`, et 50 IDs à
récupérer. La capture a échoué par
`RC8 SOURCE CAPTURE FAILED: exception_type=GmailMessageNotFoundError`.

Cela prouve qu'**au moins un ID du plan gelé n'est plus hydratable au moment
du `messages.get(full)`**. Cela ne prouve pas que le message a été supprimé,
et ne suffit pas à établir que la source à la frontière `15906422` était
incorrecte. Le script original capture toute la cohorte en mémoire avant
de persister les RAW et le manifest : le run interrompu n'est **pas** une
preuve de capture réussie et ne doit pas être référencé comme récupération.

### Diagnostic ciblé, sans réingestion ni écriture

Après `git pull --ff-only`, réutiliser le même fichier privé de plan :

```bash
GMAIL_PY="/opt/homebrew/opt/python@3.14/bin/python3.14"
PRIV="$HOME/.local/share/gmail-ingestion-private"
PLAN="$(ls -1t "$PRIV"/rc8-plan-*.json | head -n 1)"

caffeinate -i env PYTHONPATH="$PWD/src" \
  "$GMAIL_PY" scripts/diagnose_rc8_capture_cohort.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --candidate-evidence "$PLAN" \
  --max-messages 50 \
  --max-history-pages 100
```

Ce diagnostic effectue au plus 50 appels `messages.get(full)` et une
lecture bornée de Gmail History à partir de `15906422`. Il conserve les
résultats **uniquement en mémoire**, retourne exclusivement des compteurs
expurgés et ne touche ni aux fichiers historiques ni au pointeur Current.

Interprétation des résultats :

- `message_not_found_count` compte les 404 sur la cohorte privée ;
- `not_found_with_post_frontier_delete_count` compte uniquement les
  404 corroborés par une entrée `messagesDeleted` Gmail après
  `15906422`, sans les requalifier en suppression historique à cette
  frontière ;
- `not_found_without_post_frontier_delete_count` reste **non prouvé** :
  ne jamais inventer une suppression pour en faire un GO ;
- `payload_newer_than_source_frontier_count` et
  `required_message_ids_changed_since_inventory_count` mesurent les
  mouvements de la source postérieurs à l'inventaire ;
- `currently_outside_source_scope_count` signale une sortie du
  périmètre hors Spam/Corbeille.

Si un seul de ces bloqueurs est présent, la preuve figée devient
inadéquate pour capturer ces messages comme s'ils étaient inchangés à
`15906422`. Il faut **préparer une nouvelle observation source à une
frontière actualisée**, classifier les changements History, puis régénérer
un nouveau plan et une nouvelle identité de candidat. Ne pas réutiliser
le plan gelé en supprimant arbitrairement les IDs introuvables et ne
pas lancer un nouveau Full Snapshot automatique.

Une fois la source correctement réalignée, la capture actuelle reste
soumise à ses propres vérifications fail-closed. Même un diagnostic PASS
n'est pas une qualification de projection ni une décision de release.
