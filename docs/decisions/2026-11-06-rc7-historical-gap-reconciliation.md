# ADR 2026-11-06 - Historique incomplet et stratégie de réconciliation du Current State

## Statut

**DECISION ADOPTEE POUR L'INGENIERIE - NO-GO POUR LA PROMOTION STABLE.**

- Domaine : Gmail Ingestion 0.5.0rc7, projection et synchronisation Gmail History.
- Date des preuves : 2026-11-06 (UTC).
- Décision : conserver les artifacts historiques immuables et distinguer la vérité passée de la vérité courante.
- Ce document ne vaut ni validation de la projection historique ni approbation d'une reconstruction approximative.
- La version `0.5.0` et le pointeur Current autoritatif restent bloqués.

## 1. Contexte et faits vérifiés

Une mailbox réelle a fourni un Full Snapshot de **177 546 messages**, converti par RC6 en **711 segments Parquet**, manifest `COMPLETED`, **2,6 Gio** environ de données normalisées. Les 3 deltas History committés couvrent la frontière `15654186` vers `15694719`.

Un premier essai RC7 de projection a échoué car certains événements `MESSAGE_ADDED` marqués `PROCESSED` ne disposaient pas du RAW `users.messages.get(format=full)` correspondant dans leur run historique. Des messages pouvaient déjà être introuvables au moment de leur hydratation dans le connecteur 0.4. L'intégrité des manifests History ne garantit donc pas la complétude des RAW nécessaires à la matérialisation.

| Preuve | Résultat |
|---|---|
| Événements History historiques examinés | 2 624 |
| `MESSAGE_ADDED` | 510 |
| Ajouts sans RAW dans leur run | 59 |
| Ajouts suivis d'un `MESSAGE_DELETED` dans les deltas | 7 |
| Ajouts sans résolution RAW à la frontière historique | 52 |
| `users.messages.get(format=minimal)` effectués le 2026-11-06 | 52 |
| Messages alors accessibles | 49 |
| Messages alors en 404 | 3 |
| Réponses `format=full` archivées dans deux runs de preuves distincts | 49 par run |
| Deux captures strictement identiques en JSON | 48 |
| Capture différente uniquement dans le corps MIME source | 1 |
| Messages équivalents après normalisation entre les deux captures | 48 |
| Message dont les tables normalisées diffèrent | 1 (`attachments`, `mime_parts`) |
| Messages récupérés dont `historyId <= 15694719` | 48 |
| Messages récupérés dont `historyId > 15694719` | 1 |
| Pages History ultérieures parcourues intégralement | 37 |
| Enregistrements History ultérieurs parcourus | 18 050 |
| Frontière renvoyée par `history.list` | `15903015` |
| Ajouts, suppressions ou mutations de labels post-cible pour la cohorte des 52 | 0 |
| Espace disque libre constaté lors du précédent contrôle | 11 Gio |
| Mémoire physique installée | 96 Gio |

Les vérifications étaient bornées et documentées; elles ne constituent pas une preuve exhaustive de l'état de chaque message à une date passée. Aucun appel de consultation n'a changé les runs historiques, les checkpoints ou le pointeur Current. Deux jeux de preuves distincts sont conservés dans `gmail.recovery_evidence`.

## 2. Signification de l'absence de correspondances History

L'appel `users.history.list(startHistoryId=15694719)` a retourné 37 pages jusqu'à l'absence de `nextPageToken`. Le balayage n'a identifié aucun événement typé pour les 52 identifiants.

Cela permet de déclarer : **aucun événement typé correspondant n'a été retourné par la requête paginée dans cet intervalle**.

Cela ne permet PAS de déclarer :

- que les 3 messages en 404 étaient absents de la mailbox dès `15694719` ;
- qu'une réponse actuelle `format=full` décrit parfaitement le RAW d'une date antérieure ;
- que les labels actuels sont nécessairement ceux d'un checkpoint antérieur ;
- que les 52 ajouts peuvent être ignorés sans dégradation explicitée ;
- que le message ayant `historyId > 15694719` peut être rétrodaté au checkpoint ;
- que l'égalité de deux calculs incrémental/rebuild, effectués avec les mêmes substitutions, démontre à elle seule la fidélité des données Gmail historiques.

La documentation Gmail précise que `historyId` d'un message référence la dernière modification portée à ce message, que les History IDs ont des sauts et que la disponibilité de l'historique est limitée dans le temps :

- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages
- https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- https://developers.google.com/workspace/gmail/api/guides/sync

La présence d'un `historyId` supérieur à la frontière cible sans correspondance dans l'audit mérite investigation, pas une déduction automatique de corruption ou de suppression.

## 3. Invariants de sécurité et provenance

1. Ne jamais écraser `gmail.messages`, `gmail.history`, le Full Snapshot, les 711 segments, les deux `gmail.recovery_evidence` ou les manifests existants.
2. Ne jamais forger un `MESSAGE_DELETED` afin de neutraliser un `MESSAGE_ADDED` sans RAW.
3. Ne jamais rebaptiser les réponses `format=full` actuelles « RAW historique original ».
4. Toute représentation récupérée doit porter son `recovery_run_id`, sa capture temporelle, le `historyId` observé, la frontière cible et un statut `NON_AUTHORITATIVE`.
5. Exiger une manifestation explicite du manque de preuve pour les 3 messages 404 et du cas supérieur à la frontière cible.
6. Un candidat incomplet reste isolé et ne peut promouvoir le Current pointer.
7. Aucun `0.5.0` stable avant une qualification d'état courant vérifiable et une décision explicite sur la complétude.
8. Ne pas confondre RAM physique installée et RAM disponible, ni lancer de gros travaux sur un volume presque plein.

## 4. Deux produits de données distincts

### A. Reconstruction stricte à la frontière historique

`NORMALIZED BASELINE(15654186) + IMMUTABLE DELTAS -> HISTORICAL(15694719)`

**Statut : BLOCKED / NO-GO.**

La frontière historique n'est pas certifiable tant que des `MESSAGE_ADDED` obligatoires n'ont pas de RAW historique ou d'une preuve de substitution suffisamment forte. Les 49 captures datées sont des indices, et les 3 messages en 404 n'ont aucune représentation locale exploitable. Conserver l'échec existant comme preuve.

### B. Current State observé et réconcilié à une nouvelle frontière

**Orientation retenue : une nouvelle génération clairement versionnée, distincte du checkpoint historique**, après conservation d'une chaîne History postérieure et réconciliation indépendante avec Gmail.

Étapes prévues :

1. Préserver au plus tôt le History post-cible dans un nouveau run incrémental avec la récupération Full Snapshot automatique **désactivée**.
2. Améliorer le projecteur pour consommer des preuves de récupération `NON_AUTHORITATIVE` uniquement via une politique déclarée, jamais via réécriture des runs initiaux. Toute exception doit être comptabilisée dans un manifest de qualité et le digest doit couvrir le choix de politique.
3. Construire un candidat `CURRENT_RECONCILIATION` isolé, avec des écarts explicites (49 RAW de capture, 3 absents observés, un cas post-cible, sept adds supprimés ultérieurement). Ne pas assimiler ce candidat à une projection historique stricte.
4. Comparer la présence des message IDs du candidat contre une énumération Gmail récente, avec capture de la frontière du nouveau listing et rattrapage History pour fermer la fenêtre de course.
5. Réconcilier les labels et l'état des threads actuels, avec indicateurs de couverture et anomalies.
6. Exécuter rebuild, digest de contenu, contrôles référentiels et checks de ressources, puis seulement statuer sur un candidat à promouvoir. L'égalité des digests doit être complétée d'une comparaison avec la source indépendante.
7. Si le niveau de complétude requis est irréalisable, choisir explicitement un nouveau snapshot (de préférence incrémentalement réutilisé ou exécuté sur stockage disposant de marge), pas une promotion silencieuse d'état dégradé.

**Important :** ces étapes sont un plan d'implémentation, pas des fonctionnalités déjà livrées.

## 5. Action opérateur prioritaire : préserver le History de la nouvelle période

Le curseur `15694719` a répondu avec succès le 2026-11-06. La documentation officielle prévient qu'il peut expirer. L'audit antérieur était en lecture seule et **n'a pas persisté** les 18 050 enregistrements comme une quatrième ingestion History.

Après avoir libéré suffisamment d'espace disque sur une zone **hors des artifacts de ce projet**, vérifier le checkpoint actif, puis exécuter le connecteur existant avec `--no-auto-full-resync` afin d'interdire le démarrage involontaire d'un nouveau Full Snapshot :

```bash
df -h .
gmail-ingest incremental \
  --account-id acct_84757009c00c40cf859cb796 \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --checkpoint-root .gmail-ingestion-control-live-0.5.0 \
  --max-results 500 \
  --no-auto-full-resync
```

Le contrôle de l'espace libre reste une **condition préalable opérationnelle** : 11 Gio mesurés lors du dernier contrôle ne constituent pas une réserve prudente pour une opération imprévisible. Ne pas supprimer les artifacts pour libérer de la place. Le mode `--no-auto-full-resync` bloque la récupération automatisée en cas de curseur expiré, mais ne rend pas les écritures de l'incrémental facultatives.

Un run réussi doit fournir : `input_checkpoint`, `committed_checkpoint`, `run_id`, `manifest`, `full_snapshot_recovery: false`. Une erreur ne justifie pas le changement manuel du checkpoint.

## 6. Critères de sortie / DoD

- [x] Baseline 711/711 complet, 177 546 messages.
- [x] Trois runs historiques avec manifests `COMPLETED` / `RECOVERED_COMMIT`.
- [x] 59 ajouts sans RAW recensés, dont 7 avec suppression ultérieure.
- [x] 49 sources actuelles capturées et comparées, 3 404 conservés comme lacunes.
- [x] Un écart de normalisation localisé à `attachments` / `mime_parts`.
- [x] History post-cible entièrement parcouru sans correspondance pour les 52 IDs.
- [ ] History post-cible conservé dans un run incrémental committé.
- [ ] Politique de réconciliation implémentée et testée, sans écriture des sources historiques.
- [ ] Inventaire des messages comparé à la source Gmail actuelle et rattrapage de la fenêtre d'inventaire.
- [ ] Intégrité des labels / threads et invariants référentiels vérifiés.
- [ ] Candidate Current State reconstruite sans anomalie non déclarée.
- [ ] Statut de qualité et décision de promotion explicitement approuvés.
- [ ] Gate de sortie réelle Gmail pour la version 0.5.0 stable.

## 7. Décision explicite

```text
HISTORICAL(15694719): NOT QUALIFIED
CURRENT_STATE: NOT YET MATERIALIZED OR PROMOTED
CAPTURED_RECOVERY: AVAILABLE AS DATED, NON-AUTHORITATIVE EVIDENCE
POST_TARGET_HISTORY: OBSERVED, NOT YET COMMITTED
NEXT: PRESERVE HISTORY SAFELY; DESIGN SOURCE-VALIDATED CURRENT RECONCILIATION
STABLE_0.5.0: NO-GO
```

Cette ADR vaut comme registre de décision. Aucun mécanisme de réparation, de fusion ou de promotion n'est activé par sa création.


## Addendum 2026-11-06 - History post-target committé selon la sortie opérateur

Après la décision initiale, l'opérateur a partagé la sortie d'une exécution
`gmail-ingest incremental --no-auto-full-resync` terminée. La sortie locale
indique un run `8b133cc6-7ebe-4b3a-b26c-d309a058ca4c` en statut
`COMPLETED`, de `15694719` à `15903183`, avec 37 pages, 8 202 événements,
3 044 hydratations, 60 tombstones, 5 090 mutations de labels et 8 messages
indisponibles. Aucun Full Snapshot automatique n'a été déclenché.

Cette information met à jour la priorité opérationnelle de la section 5 :
**la capture post-cible est signalée comme effectuée par l'opérateur**. Le run
n'a pas été importé dans la CI et ses artifacts locaux n'ont pas encore été
revérifiés indépendamment. Le nouvel outil
`scripts/audit_current_release_readiness.py` fournit cette vérification de
chaîne et un bilan de provenance sans modification ni accès Gmail.

La décision historique reste inchangée : `HISTORICAL(15694719): NOT QUALIFIED`.
La qualification du Current State nécessite toujours une réconciliation
indépendante avec Gmail. La stable `0.5.0` reste `NO-GO`. Voir
`docs/runbooks/CURRENT_RECONCILIATION_RC8.md`.
