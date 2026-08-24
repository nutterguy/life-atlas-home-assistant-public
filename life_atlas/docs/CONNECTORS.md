# Life Atlas connector architecture

## Status

This document defines the architectural contract for external Life Atlas data connectors. It is intentionally implementation-neutral. Runtime connector support, schemas, Home Assistant packaging, and individual connectors are introduced in later changes.

The core rule is:

> Life Atlas owns the curated life record. Connectors own complete source-system archives.

Life Atlas may keep durable copies of selected source evidence when that evidence has lasting value. It must not mirror entire source systems into the canonical database by default.

## Goals

The connector system must:

- keep Life Atlas usable when every connector is offline or removed;
- isolate source-specific authentication, protocols, caches, databases, and update cycles from the core application;
- support both MCP-backed and non-MCP-backed sources without coupling Life Atlas to MCP;
- allow independent installation, upgrade, restart, failure, backup, and recovery of connectors;
- preserve source provenance and enough promoted evidence for the curated Life Atlas record to remain independently meaningful;
- avoid bulk-copying complete WhatsApp, Google Photos, email, activity, or other archives into `life_atlas.sqlite3`;
- support future sources without source-specific conditionals spreading through the core application;
- remain compatible with the Windows edition's portable canonical database model.

## Architecture

Life Atlas communicates with connectors through a small, versioned Life Atlas Connector Protocol. MCP may be used by an upstream component or exposed separately for AI clients, but MCP is not the internal Life Atlas connector contract.

```text
External source
      |
      v
Connector / adapter
  - source authentication
  - source-native protocol
  - source-native archive/cache
  - source-specific sync
      |
      v
Life Atlas Connector Protocol
      |
      v
Life Atlas
  - curated timeline
  - people / places / trips / relationships
  - promoted evidence
  - selected promoted media
  - review and confidence
  - source-neutral extraction
```

The first intended proof connectors are Google Photos and WhatsApp. Their source characteristics are deliberately different so that an abstraction proven by both should generalise well to later connectors such as Diarium, Strava, Garmin, calendar, email, or other personal archives.

## Trust and ownership boundaries

### Life Atlas owns

- `life_atlas.sqlite3`;
- curated events, people, places, trips, chapters, relationships, tags, and review state;
- source-neutral provenance records;
- durable promoted text evidence;
- durable promoted media selected for the Life Atlas record;
- mappings that associate external identities with Life Atlas entities;
- connector registration, capabilities, status, cursors, and promotion state.

### A connector owns

- source authentication and refresh/session tokens;
- source-native databases and indexes;
- complete or near-complete raw source history;
- source media caches and temporary downloads;
- source-specific sync state and protocol details;
- source-specific upstream dependencies and their migrations.

Life Atlas must never open or mutate a connector-owned database directly. A connector must never open or mutate `life_atlas.sqlite3` directly. Communication crosses the connector protocol boundary only.

## Connector protocol principles

The first protocol version will be HTTP/JSON and versioned independently from both Life Atlas and connector releases. Exact request and response schemas are frozen in a later implementation step.

The expected capability families are:

- health and version information;
- advertised capabilities;
- search;
- item retrieval;
- incremental changes using opaque cursors/checkpoints;
- explicit source sync/history requests where supported;
- media retrieval where supported.

Connectors advertise capabilities rather than forcing Life Atlas to infer behaviour from connector type. Examples include search, incremental sync, history sync, media, identities, locations, realtime updates, and write operations.

Life Atlas integrations are read-only by default. A source connector may internally support writes, but write operations must not become part of the Life Atlas-facing contract unless a future requirement explicitly justifies them.

## Source items

Each connector translates source-native records into a source-neutral envelope called a SourceItem. The exact schema is defined later, but a SourceItem must be able to represent at least:

- stable connector-scoped identifier;
- source item type;
- source timestamp and modification state;
- text/title when applicable;
- participants/identities when applicable;
- location when applicable;
- media descriptors when applicable;
- source-native identifier and provenance metadata;
- content hash or equivalent change/deduplication signal where practical;
- lifecycle state such as created, updated, deleted, or unavailable.

A SourceItem is not automatically a canonical Life Atlas record.

## Reference, promotion, and derivation

Life Atlas treats source material in distinct stages.

### Available

The connector knows the item exists. Life Atlas may discover or search it without storing its content permanently.

### Referenced

Life Atlas stores a durable source reference and provenance metadata but does not necessarily copy the full source content or media.

### Promoted

Life Atlas deliberately stores its own durable snapshot because the source item has lasting value as evidence or media in the curated record. Promoted content must remain useful even if the connector later disappears.

Examples include:

- a WhatsApp message that directly supports a trip date or relationship event;
- a photo selected as representative media for an event or person;
- a diary excerpt used as evidence;
- a document or email excerpt supporting a confirmed life event.

Promotion must be selective. Searching or analysing an item does not by itself promote it.

### Derived

Life Atlas creates or updates structured knowledge inferred from source evidence, for example an event, person association, place, trip, or relationship fact. Derived knowledge retains provenance back to its supporting source item or promoted evidence.

One item may be both promoted and used to derive structured knowledge.

## Canonical versus source data

Life Atlas remains a curated, human-readable model of the user's life. Raw conversations, complete photo libraries, and other bulk archives stay connector-owned.

A typical workflow is:

```text
source archive
    -> discover/search
    -> inspect
    -> reference
    -> optionally promote durable evidence
    -> optionally derive curated knowledge
    -> review/accept where required
```

Life Atlas event descriptions and other narrative fields must not become dumping grounds for raw source transcripts. Evidence is stored and related separately from the clean canonical representation.

## Promotion policy

Promotion should support explicit user action and, where safe, rule-based automation.

Good candidates for automatic or low-friction promotion include:

- short evidence excerpts used by an accepted canonical event;
- media explicitly attached to an event, diary day, or person;
- selected portrait/representative photos;
- imported diary records where the diary itself is intended to be canonical source material.

Items requiring review before promotion include:

- long conversation spans;
- relationship or identity evidence inferred by AI;
- large or expensive media;
- ambiguous or weakly relevant source material.

Items should not normally be promoted merely because they were encountered during search. Bulk chatter, duplicate media, notifications, reactions, memes, and caches should remain source-owned unless explicitly selected.

## Evidence durability and provenance

Promoted evidence stores enough immutable provenance to understand its origin even if the source later changes or disappears. Where available this includes:

- connector identifier;
- source-native item identifier;
- source timestamp;
- sender/author or source identity reference;
- source conversation/collection context;
- capture/promoted timestamp;
- content hash;
- original lifecycle state.

For promoted text, Life Atlas stores the promoted snapshot rather than only a pointer. For promoted media, Life Atlas stores its own managed copy in `/data/media` or a future equivalent canonical media store.

If the upstream item is later edited, deleted, expires, or becomes unavailable, Life Atlas records that source status without silently rewriting previously promoted evidence. A new source version may be retained as a separate revision or provenance update when appropriate.

## Sync model

Connectors expose incremental changes using opaque cursors/checkpoints where the source permits it. Life Atlas records the last successfully committed cursor only after processing the corresponding batch successfully.

All connector ingestion must be idempotent. Replaying the same change batch must not create duplicate canonical records, evidence, mappings, or media.

The design must tolerate:

- Life Atlas being offline while connectors continue to collect data;
- connector restarts;
- interrupted sync batches;
- retries and replay;
- duplicates from upstream sources;
- edited/deleted/unavailable source items;
- partial historical coverage;
- very large source archives.

Push/webhook mechanisms may be added as wake-up hints, but the recoverable source of truth remains a pull/checkpoint process.

## Identity resolution

External identities are not automatically equivalent to Life Atlas people.

A connector identity may be represented by a phone number, WhatsApp identifier, email address, provider account, contact name, face/person cluster, username, or other source-specific key. Life Atlas stores explicit mappings between external identities and canonical people.

Automatic matching may propose candidates with confidence, but ambiguous mappings enter the review queue. A shared identity-resolution layer should be reusable across connectors rather than implemented independently for WhatsApp, Photos, email, and later sources.

## Media

Connector-owned media is not automatically copied into Life Atlas. Life Atlas may reference source media for browsing and search, then promote selected items into its own durable media store.

Promotion should distinguish an optimised Life Atlas copy from an optional archived original. Large originals should never be copied automatically without an explicit policy or user choice.

Reference counting, content hashes, and duplicate detection should prevent unnecessary duplicate canonical files.

## AI and extraction

Connectors provide source data. They do not define the user's life narrative.

Source interpretation, extraction, confidence, entity resolution, and review live in Life Atlas so that all sources share one consistent pipeline. AI-derived facts must remain distinguishable from source evidence and retain links to the evidence that supports them.

Direct AI access to source-specific MCP servers is optional. The preferred long-term ChatGPT-facing interface is Life Atlas itself, which can perform federated search and retrieve connector evidence behind a narrower trust boundary.

## Security

- Connector credentials remain connector-owned and are never copied into the Life Atlas database.
- Connector internal services should use Home Assistant app-to-app networking/private service discovery where possible rather than publishing unnecessary LAN ports.
- Life Atlas should receive the minimum connector capability needed for its purpose.
- Read-only operation is the default for personal communication and archive connectors.
- Sensitive source content must not be emitted into routine logs.
- Secrets, sessions, personal source databases, downloaded source archives, and machine-specific configuration must never be committed to Git.
- Connector failures must not grant broader Home Assistant permissions to Life Atlas core.

For WhatsApp specifically, the Life Atlas-facing adapter must omit message sending, reactions, typing, mark-read, and similar mutation operations even if the chosen upstream bridge supports them.

## Versioning and updates

Life Atlas, each connector package, the connector protocol, and third-party upstream dependencies have independent versions.

Third-party upstreams are pinned to reviewed releases or commits. Production connectors must not track an upstream `main` branch implicitly.

A connector reports at least:

- connector package version;
- connector protocol version;
- upstream component/version where applicable;
- authentication/connection state;
- last attempted and last successful sync;
- current error/health state.

An upstream update is tested against the connector contract and integration suite before its pin is advanced.

## Failure isolation

Life Atlas must continue to serve its canonical database when any or all connectors are absent, offline, misconfigured, unauthenticated, incompatible, or corrupt.

Connector failures are represented as source status, not as global Life Atlas failure. Existing promoted evidence remains available when its source connector is offline.

Protocol incompatibility must fail closed with a clear status rather than silently misinterpreting data.

## Backup and recovery

Life Atlas backups contain the canonical database and durable promoted evidence/media needed to make the curated life record usable without external connectors.

Connector archives and credentials have separate backup requirements because some source data may be rebuildable while other historical material may not be recoverable from the upstream service later.

Each connector will define which state is:

- required for recovery;
- recommended to back up;
- safely rebuildable;
- intentionally excluded from ordinary backup because it contains replaceable cache data.

Home Assistant backup/restore behaviour must be tested explicitly for each connector rather than assumed.

## Observability

Life Atlas should eventually expose a source status view containing, where supported:

- installed/available state;
- connector and upstream versions;
- protocol version;
- connection/authentication status;
- last attempted and successful sync;
- item/change counts where meaningful;
- current cursor/checkpoint state;
- recoverable error information;
- available capabilities.

Routine status must avoid exposing sensitive message text, credentials, or personal data in logs.

## Testing requirements

Before Connector Protocol v1 is considered stable, it must be proven by at least two materially different real connectors, initially Google Photos and WhatsApp.

A reference/mock connector will be used for deterministic contract tests. The test matrix must eventually cover:

- unavailable connector;
- timeouts and malformed responses;
- unsupported/old protocol version;
- unsupported capabilities;
- pagination and large result sets;
- duplicate/replayed batches;
- interrupted sync and resume;
- edits, deletes, and unavailable source items;
- authentication expiry;
- connector restart and upgrade;
- Life Atlas restart and upgrade;
- backup and restore;
- disk/storage failures where practical;
- identity ambiguity;
- selective promotion and duplicate media handling.

## Non-goals for the first implementation

- mirroring complete connector archives into `life_atlas.sqlite3`;
- allowing Life Atlas to mutate WhatsApp or other personal communication sources;
- making MCP a mandatory connector dependency;
- automatic acceptance of AI-derived personal facts without provenance/review policy;
- automatic archival of all full-resolution source media;
- coupling core Life Atlas availability to connector availability.

## Implementation sequence

The agreed implementation order is:

1. freeze/document architecture and baseline tests;
2. add source-neutral connector model to Life Atlas with no real connector;
3. build deterministic mock/reference connector;
4. freeze Connector Protocol v1 schemas and contract tests;
5. create/install the Home Assistant connector repository and prove private app-to-app networking;
6. refactor/wrap Google Photos as the first real connector;
7. prove persistence, failure isolation, backup, restore, upgrade, and authentication recovery;
8. add the read-only WhatsApp connector around a pinned upstream bridge;
9. validate real WhatsApp historical coverage and explicit history sync;
10. implement robust incremental checkpoint sync;
11. integrate provenance plus reference/promotion/derivation states;
12. add federated search;
13. add shared identity resolution;
14. add selective media promotion;
15. add source-neutral AI extraction and review;
16. add source/connector management UI;
17. expose unified ChatGPT-facing Life Atlas retrieval rather than broad source credentials;
18. add upstream update monitoring and contract-gated upgrades;
19. execute the full failure matrix;
20. freeze Connector Protocol v1 after Google Photos and WhatsApp prove the abstraction.
