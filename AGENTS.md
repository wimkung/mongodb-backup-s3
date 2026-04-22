# AGENTS.md

Guidance for AI coding agents working in this repo.

## What this project is

A single Docker image that runs `cron` → `mongodump --archive --gzip` → `openssl smime -aes256` (asymmetric) → `aws s3 cp`. Restore reverses the pipeline with the RSA private key. There is **no application code** — everything is shell + Docker.

Key files (whole codebase):
- `Dockerfile` — `debian:bookworm-slim` + MongoDB Database Tools `100.x` (official `.deb`) + AWS CLI v2. Multi-arch via `TARGETARCH` → `MDB_DISTRO`/`MDB_ARCH`/`AWS_ARCH` mapping (`amd64→debian12/x86_64/x86_64`, `arm64→ubuntu2204/arm64/aarch64`). Note: mongodb.org doesn't publish a `debian12-arm64` `.deb`, so arm64 pulls the `ubuntu2204-arm64` build (ABI-compatible with bookworm).
- `run.sh` — container entrypoint (`CMD`). **Generates** `/backup.sh`, `/restore.sh`, `/listbackups.sh` at runtime via heredocs, symlinks them into `/usr/bin`, then either runs `INIT_BACKUP`/`INIT_RESTORE` once and/or installs the crontab and `tail -f`s `/mongo_backup.log` as PID 1.
- `docker-compose.yml`, `README.md` — usage docs.

## Architecture gotchas (read before editing `run.sh`)

1. **Heredoc quoting is load-bearing.** `/backup.sh` and `/restore.sh` are emitted with an *unquoted* `<<EOF`, so variables split into two categories:
   - Expanded at **generation time** (baked into the script): `${S3PATH}`, `${TARGET_STR}`, `${DB_STR}`, `${REGION_STR}`, `${EXTRA_OPTS}`.
   - Escaped with `\$` so they expand at **run time**: `\${TIMESTAMP}`, `\${BACKUP_NAME}`, `\${BACKUP_PUBLIC_KEY}`, `\${BACKUP_PRIVATE_KEY}`, `\${1}`. Preserve this split when modifying — flipping it breaks cron runs silently.
2. **Cron loses the environment.** `run.sh` snapshots only `AWS*` vars via `printenv | sed ... | grep -E "^export AWS" > /root/project_env.sh` and the crontab sources that file. Any new runtime variable the backup script needs at cron time must also be written into `project_env.sh` (extend the `grep -E` filter) **or** baked into the generated script at generation time.
3. **Connection-string precedence.** `MONGODB_URI` wins; when unset, discrete `MONGODB_HOST/PORT/USER/PASS/AUTH_DB` are used, with legacy Docker link fallbacks (`MONGODB_PORT_27017_TCP_ADDR`, `MONGODB_PORT_1_27017_TCP_PORT`, `MONGODB_ENV_MONGODB_USER`, …). Keep both paths working.
4. **Encryption is asymmetric S/MIME**, not symmetric. Backup uses `openssl smime -encrypt -aes256 -binary -outform DEM` with an X.509 cert (`BACKUP_PUBLIC_KEY`); restore uses `-decrypt -inform DEM -inkey $BACKUP_PRIVATE_KEY`. Uploaded objects are `backup_<UTC-ish timestamp>.dump.gz.ssl` plus a rolling `latest.dump.gz.ssl` under `s3://$BUCKET/$BACKUP_FOLDER`.
5. **SigV4 is set per invocation** inside the generated scripts via `aws configure set default.s3.signature_version s3v4` — required for non–`us-east-1` buckets. Don't remove it.
6. **Lifecycle flags** in `run.sh`: `INIT_BACKUP` / `INIT_RESTORE` trigger a one-shot at startup; `DISABLE_CRON` skips installing the crontab entirely (and therefore skips the `tail -f`, so the container exits — this is intentional for one-shot restore jobs).
7. **Dual cron binary support.** `run.sh` prefers `cron` (Debian) and falls back to `crond` (Alpine) so the script stays portable if the base image ever changes.

## Build / verify workflow

```bash
# Build (auto-detects arch; override tools version if needed)
docker build -t mongodb-backup-s3:local .
docker build --build-arg MONGO_TOOLS_VERSION=100.10.0 -t mongodb-backup-s3:local .

# Smoke-test the bundled binaries
docker run --rm mongodb-backup-s3:local mongodump --version
docker run --rm mongodb-backup-s3:local aws --version

# On-demand ops against a running container
docker exec mongodb-backup-s3 backup
docker exec mongodb-backup-s3 listbackups
docker exec mongodb-backup-s3 restore 20260406T155812   # or no arg for latest
```

Releases: push to `master`/`main` or tag `vX.Y.Z` — `.github/workflows/docker-publish.yml` builds multi-arch (`linux/amd64,linux/arm64`) and pushes to Docker Hub using `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` / optional `DOCKERHUB_REPO` secrets.

## Conventions

- Keep `run.sh` POSIX-ish bash and portable across Debian/Alpine (see cron fallback).
- New env vars: document in `README.md`'s tables (AWS / MongoDB / Encryption / Scheduling sections) **and**, if needed at cron time, propagate via `project_env.sh`.
- `mongorestore` is always invoked with `--drop` — call this out in docs if you change it.
- `EXTRA_OPTS` is appended verbatim to **both** `mongodump` and `mongorestore`; only add flags valid for both, or split into two vars.

