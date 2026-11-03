# Docker storage inventory (read-only, 2026-11-03)

Measured 2026-11-03 ~17:40 UTC with `docker system df -v`, `docker inspect`, `du -sb` on volume mountpoints. **Nothing was removed.** Classification vocabulary: REQUIRED / IN_USE / ROLLBACK/RECOVERY / VERIFIED_UNUSED / UNKNOWN / UNCERTAIN. A volume/image is never VERIFIED_UNUSED merely because no running container uses it (all 17 containers are running; there are no stopped containers).

Totals: images 19 (23.48 GB, 7.151 GB reclaimable per Docker), 17 containers, volumes 51 (15 linked, 36 unlinked), build cache 0.

## Images

| ID | Tag | Size / unique (recoverable) | Created | Containers | Other references | Class |
|---|---|---|---|---|---|---|
| bf7efaf6e2bf | autosparefinder-backend:latest | 11.1 GB | 3 mo | backend, sandbox_backend | compose | **IN_USE** |
| 3d31f892e727 | autosparefinder-frontend:latest | 103 MB | 3 d | frontend | compose | **IN_USE** |
| ea5f109c3d86 | autospare_sandbox_tools:latest | 768 MB | 3 mo | sandbox_tools | sandbox/docker-compose.sandbox.yml | **IN_USE** |
| 010341a778fe | getmeili/meilisearch:v1.7 | 245 MB | 2 y | meilisearch, sandbox_meili | compose | **IN_USE** |
| 00ba258a66da | pgvector/pgvector:pg16 | 621 MB | 5 mo | 3 postgres | compose | **IN_USE** |
| 6ab0b6e73817 | redis:7-alpine | 57.8 MB | 5 mo | redis, sandbox_redis | compose | **IN_USE** |
| 8b1e78743a03 | nginx:alpine | 93.6 MB | 5 mo | nginx | compose | **IN_USE** |
| fb4cd12c85ee | node:20-alpine | 194 MB | 6 mo | whatsapp_bridge | compose | **IN_USE** |
| 139dfee1c6f8 | ghcr.io/flaresolverr/flaresolverr:latest | 1.04 GB | 5 mo | flaresolverr | compose | **IN_USE** |
| 9a0137866062 | hotmail-mcp-hotmail-mcp:latest | 268 MB | 2 mo | hotmail_mcp | /root/mcp-servers/hotmail-mcp/docker-compose.yml | **IN_USE** |
| f70742ebe42b | prodrigestivill/postgres-backup-local | 665 MB | 13 mo | 2 backup containers | compose | **IN_USE** |
| 8f693eaa7e0a | node:20 | 1.59 GB / 1.592 GB unique | 6 mo | none | docker-compose.yml + frontend/Dockerfile (frontend builder; owner KEEP) | **REQUIRED** |
| 0d3b80406a13 | nginx:stable-alpine | 93.3 MB / 0.9 MB unique | 4 mo | none | frontend/Dockerfile, CLAUDE.md §5 (`nginx -t` rehearsal image) | **REQUIRED** |
| 83f487e0a634 | node:22-slim | 329 MB / 329.2 MB unique | 2 mo | none | base of /root/mcp-servers/hotmail-mcp/whatsapp-relay/Dockerfile (rebuild input) | **REQUIRED (build input)** |
| 6725052037e3 | hotmail-mcp-hotmail-wa-relay:latest | 957 MB / 843.3 MB unique | 2 mo | none (service defined, not running) | hotmail-mcp/docker-compose.yml service `hotmail-wa-relay` | **UNCERTAIN** |
| 0cefaabd882e | postgres:16-alpine | 420 MB / 419.7 MB unique | 12 d | none | candidates/supplier-lifecycle-2026-10-27 rehearsal scripts + compose (re-runnable rehearsal evidence) | **ROLLBACK/RECOVERY** |
| ba06152be75b | gstack-sandbox-gstack_sandbox:latest | 3.91 GB / 3.909 GB unique | 3 mo | none (container gone) | CLAUDE.md §9: design skills must run in this sandbox; no compose file found on host to rebuild it | **UNCERTAIN** |
| 5b10f432ef3d | alpine:latest | 13.1 MB / 88 KB unique | 6 mo | none | sandbox/Dockerfile.tools base | **REQUIRED (build input)** |
| e9b2e45ecd47 | redis:7.4 | 170 MB / 56.12 MB unique | 3 mo | none | none found (grep of repo, candidates/, /root/mcp-servers; only FIXES_TRACKER prose); public image, re-pullable | **VERIFIED_UNUSED** |

**VERIFIED_UNUSED image candidate:** `redis:7.4` (id e9b2e45ecd47) - recoverable **56.12 MB** (113.5 MB of layers are shared with other images and stay). References checked: running+stopped containers (none), docker-compose.yml, sandbox/, candidates/, /root/mcp-servers, scripts, Dockerfiles (none). Re-pullable from Docker Hub. Needs approval; not removed.

## Linked volumes (REQUIRED / IN_USE)

| Volume | Size | Used by | Class |
|---|---|---|---|
| autosparefinder_postgres_catalog_data | 21.86 GB | postgres_catalog | REQUIRED |
| autosparefinder_meili_data | 28.83 GB | meilisearch | REQUIRED |
| autosparefinder_worker_state | 6.903 GB | backend `/app/state` (models, Chrome profiles, logs) | REQUIRED |
| autosparefinder_upload_files | 246 MB | backend | REQUIRED |
| autosparefinder_postgres_pii_data | 181.8 MB | postgres_pii | REQUIRED |
| autosparefinder_redis_data | 73.2 MB | redis | REQUIRED |
| autosparefinder_frontend_build | 5.9 MB | frontend, nginx | REQUIRED |
| autospare_sandbox_sandbox_backups | 3.645 GB | sandbox_backend (July dumps baked in) | IN_USE |
| autospare_sandbox_sandbox_worker_state / _postgres_data / _redis_data / _meili_data | 145.3 MB / 183.7 MB / 28.5 MB / 3.7 MB | sandbox_* | IN_USE |
| 2 anonymous 1-byte volumes (c1edfb58…, 166e4088…) | 1 B each | the two postgres-backup containers | IN_USE |

## Unlinked volumes (36) - all kept

| Volume | Bytes | Created | Class | Evidence |
|---|---|---|---|---|
| 4b254aae6f53cf52… | 0 | 2026-07-23 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; anonymous |
| 21a11e4854b281aa… | 0 | 2026-09-09 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; anonymous |
| 739a4c7f9aaa8074… | 0 | 2026-06-29 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; anonymous |
| 8660cd508091337c… | 0 | 2026-09-09 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; anonymous |
| ac66e4a67da4c289… | 0 | 2026-07-23 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; anonymous |
| autosparefinder_backup_data | 0 | 2026-05-28 | **VERIFIED_UNUSED** | 0 bytes; no running/stopped container; declared in compose but recreated empty on demand |
| 3ef42c5c602e0cd4… | 88 | 2026-10-27 | **VERIFIED_UNUSED** | <=745 B Redis dump.rdb from 2026-10-27 rehearsal; no container references |
| 8846da1717c613c0… | 88 | 2026-10-27 | **VERIFIED_UNUSED** | <=745 B Redis dump.rdb from 2026-10-27 rehearsal; no container references |
| b3c549c464c8eb96… | 88 | 2026-10-27 | **VERIFIED_UNUSED** | <=745 B Redis dump.rdb from 2026-10-27 rehearsal; no container references |
| e710378202b79f96… | 745 | 2026-10-27 | **VERIFIED_UNUSED** | <=745 B Redis dump.rdb from 2026-10-27 rehearsal; no container references |
| 027a8b66b4efd118… | 40,282,406 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 63a2e55c68163853… | 40,331,558 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| d84a13077594cc54… | 40,487,206 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 4b65d90f5d6351fa… | 40,831,270 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 56ba2e617d4b541c… | 40,831,270 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 6592368fda097cd5… | 40,831,270 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| d46fb649c0f2a0ad… | 40,831,270 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| fb16a6cc725249e0… | 42,445,094 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| c85b45b1c5d037ee… | 42,689,326 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 66042f0a44526b4b… | 42,746,670 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 15fa26ccba58919e… | 49,490,678 | 2026-10-09 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 1d013c19aaa093fe… | 49,744,693 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 5bf136c773ec3420… | 49,744,693 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| db9bf1aa5b49b773… | 49,744,693 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| f989fa4b1a739b3d… | 50,529,597 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| e456eb756855ed06… | 50,626,373 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 450f15e333eb363f… | 50,634,565 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 170b1c65f51726c6… | 55,566,613 | 2026-10-10 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| bd147721e244bf66… | 80,435,997 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| be4cb01567e3867d… | 80,589,073 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 5105f2807f854e7b… | 83,026,197 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 0b523329073964f1… | 99,613,469 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| autosparefinder_clamav_data | 175,658,861 | 2026-06-08 | **UNCERTAIN** | declared in docker-compose.yml `volumes:`; ClamAV service decommissioned 2026-07-12; freshclam signature DBs (regenerable) - owner decision |
| 2abef6da9dfffc0a… | 232,188,662 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| 129b78de680d0e6b… | 385,780,513 | 2026-10-27 | **UNKNOWN** | PostgreSQL data dir (rehearsal/test copy, 2026-10-09/10 or 10-27); no provenance recorded per volume; may be a rollback/rehearsal artefact |
| gstack-sandbox_sandbox_claude_home | 466,871,324 | 2026-07-26 | **UNCERTAIN** | Claude home of the gstack sandbox (skills dir); sandbox image/container kept for design skills (CLAUDE.md §9) |

**Totals by class:** VERIFIED_UNUSED: 10 volumes, 0.000 GB (1,009 B); UNKNOWN: 24 volumes, 1.780 GB (1,780,023,156 B); UNCERTAIN: 2 volumes, 0.643 GB (642,530,185 B)

VERIFIED_UNUSED volumes are empty or hold only a <=745 B Redis dump; recoverable space is negligible (see total), so removal is optional. Everything UNKNOWN/UNCERTAIN is preserved. No volume was deleted.
