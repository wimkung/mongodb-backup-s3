# mongodb-backup-s3

A Docker image that runs scheduled `mongodump` backups, encrypts them with an
OpenSSL public key, and uploads the resulting archive to an Amazon S3 bucket.
Restore is performed with the matching private key.

> Forked from [halvves/mongodb-backup-s3](https://github.com/halvves/mongodb-backup-s3)
> with added support for AWS Signature Version 4 and asymmetric (OpenSSL S/MIME)
> encryption of the backup archives.

## Features

- Scheduled backups via `cron` (`CRON_TIME`, default `0 3,15 * * *`).
- Streamed `mongodump --archive --gzip` dumps (compressed).
- Asymmetric encryption of each dump with `openssl cms -aes256` (streamed) — only the
  holder of the private key can restore.
- Uploads both a timestamped archive (`backup_YYYYMMDDTHHMMSS.dump.gz.ssl`) and
  a rolling `latest.dump.gz.ssl` to S3.
- Optional run-once backup (`INIT_BACKUP`) or restore (`INIT_RESTORE`) on
  container start.
- Helper scripts: `backup`, `restore`, `listbackups`.
- Supports AWS S3 Signature v4 (fixes `AWS4-HMAC-SHA256` errors).
- Supports linked MongoDB containers — auto-detects host/port from the legacy
  Docker `*_PORT_27017_TCP_*` env vars.
- `MONGODB_URI` for modern setups (replica sets, `mongodb+srv://`, Atlas, TLS).

## Compatibility

| Component | Version |
| --- | --- |
| Base image | `debian:bookworm-slim` |
| MongoDB Database Tools | `100.10.0` (override with `--build-arg MONGO_TOOLS_VERSION=…`) |
| MongoDB server | **4.4 – 8.x**, including **8.2** |
| AWS CLI | v2 (official bundle, `x86_64` + `aarch64`) |

> MongoDB's official `mongodump` / `mongorestore` are shipped as the
> `mongodb-database-tools` package on the `100.x` release line, which MongoDB
> supports against server versions 4.4 through the current 8.x series.

## Generating the encryption key pair

The image encrypts backups with an X.509 / RSA key pair. Generate one **once**
and keep the private key somewhere safe (you need it to restore):

```bash
# private key (keep secret!)
openssl genrsa -out backup.key 4096

# self-signed certificate used as the public key for cms -encrypt
openssl req -x509 -key backup.key -out backup.crt -days 3650 -subj "/CN=mongo-backup"
```

Mount `backup.crt` inside the container and point `BACKUP_PUBLIC_KEY` at it
(used for encryption during backup). For restore, mount `backup.key` and set
`BACKUP_PRIVATE_KEY`.

## Quick start

### Against a replica set / Atlas / TLS (recommended for MongoDB 5.0+)

Use `MONGODB_URI` — it's passed through verbatim as `mongodump --uri=…`, so any
valid connection string works (including `mongodb+srv://`, `tls=true`, auth
source, read preference, etc.):

```bash
docker run -d \
  --name mongodb-backup-s3 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e BUCKET=my-s3-bucket \
  -e BUCKET_REGION=us-east-1 \
  -e BACKUP_FOLDER=prod/mongo/ \
  -e MONGODB_URI='mongodb+srv://backup:secret@cluster0.abcd.mongodb.net/?retryWrites=true&w=majority&authSource=admin' \
  -e BACKUP_PUBLIC_KEY=/keys/backup.crt \
  -v $(pwd)/backup.crt:/keys/backup.crt:ro \
  deenoize/mongodb-backup-s3
```

### Against a single host (legacy / self-hosted)

```bash
docker run -d \
  --name mongodb-backup-s3 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e BUCKET=my-s3-bucket \
  -e BUCKET_REGION=us-east-1 \
  -e BACKUP_FOLDER=prod/mongo/ \
  -e MONGODB_HOST=mongodb \
  -e MONGODB_PORT=27017 \
  -e MONGODB_USER=admin \
  -e MONGODB_PASS=secret \
  -e MONGODB_AUTH_DB=admin \
  -e BACKUP_PUBLIC_KEY=/keys/backup.crt \
  -v $(pwd)/backup.crt:/keys/backup.crt:ro \
  deenoize/mongodb-backup-s3
```

If you link a MongoDB container with alias `mongodb`, the image auto-detects
host/port:

```bash
docker run -d \
  --link my_mongo_db:mongodb \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -e BUCKET=my-s3-bucket \
  -e BACKUP_FOLDER=prod/mongo/ \
  -e INIT_BACKUP=true \
  -e BACKUP_PUBLIC_KEY=/keys/backup.crt \
  -v $(pwd)/backup.crt:/keys/backup.crt:ro \
  deenoize/mongodb-backup-s3
```

## docker-compose

Automated scheduled backups using Docker secrets for the key material:

```yaml
version: '3'

services:
  mongodbbackup:
    image: deenoize/mongodb-backup-s3:latest
    restart: always
    secrets:
      - BACKUP_PRIV
      - BACKUP_PUB
    environment:
      - AWS_ACCESS_KEY_ID=...
      - AWS_SECRET_ACCESS_KEY=...
      - BUCKET_REGION=us-east-1
      - BUCKET=my-s3-bucket
      - BACKUP_FOLDER=prod/mongo/
      - MONGODB_HOST=mongodb
      - MONGODB_PORT=27017
      - MONGODB_DB=
      - INIT_BACKUP=
      - BACKUP_PRIVATE_KEY=/run/secrets/BACKUP_PRIV
      - BACKUP_PUBLIC_KEY=/run/secrets/BACKUP_PUB

secrets:
  BACKUP_PRIV:
    external: true
  BACKUP_PUB:
    external: true
```

Seed / restore a fresh instance using `INIT_RESTORE` + `DISABLE_CRON`:

```yaml
mongodbbackup:
  image: deenoize/mongodb-backup-s3:latest
  environment:
    - AWS_ACCESS_KEY_ID=...
    - AWS_SECRET_ACCESS_KEY=...
    - BUCKET=my-s3-bucket
    - BACKUP_FOLDER=prod/mongo/
    - INIT_RESTORE=true
    - DISABLE_CRON=true
    - BACKUP_PRIVATE_KEY=/run/secrets/BACKUP_PRIV
  secrets:
    - BACKUP_PRIV
```

## Environment variables

### AWS / S3

| Variable | Description |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | AWS access key with `s3:PutObject` / `s3:GetObject` on the bucket. |
| `AWS_SECRET_ACCESS_KEY` | Matching secret key. |
| `BUCKET` | Target S3 bucket name. |
| `BUCKET_REGION` | Optional. Bucket region (e.g. `us-east-2`). Set this if you see `PermanentRedirect` errors. |
| `BACKUP_FOLDER` | Optional. Prefix/path inside the bucket (e.g. `myapp/db_backups/`). Defaults to bucket root. |

### MongoDB

You can either pass a full connection string via `MONGODB_URI` **or** use the
discrete host/port/user/pass variables. If `MONGODB_URI` is set, the discrete
variables are ignored.

| Variable | Description |
| --- | --- |
| `MONGODB_URI` | Full MongoDB connection string (`mongodb://…` or `mongodb+srv://…`). Takes precedence over host/port/user/pass. Recommended for replica sets, Atlas, and TLS. |
| `MONGODB_HOST` | Mongo host/IP. Auto-detected from a linked container if not set. |
| `MONGODB_PORT` | Mongo port. Auto-detected from a linked container if not set. |
| `MONGODB_USER` | Mongo username. If unset but `MONGODB_PASS` is set, defaults to `admin`. |
| `MONGODB_PASS` | Mongo password. |
| `MONGODB_AUTH_DB` | Authentication database. Default: `admin`. |
| `MONGODB_DB` | Optional. Database to dump. Dumps all databases if unset. |
| `EXTRA_OPTS` | Optional. Extra flags appended to both `mongodump` and `mongorestore` (e.g. `--tls --tlsCAFile=/keys/ca.pem --numParallelCollections=4`). |

### Encryption

| Variable | Description |
| --- | --- |
| `BACKUP_PUBLIC_KEY` | Path (inside the container) to the X.509 certificate / public key used to encrypt backups. **Required for `backup.sh`.** |
| `BACKUP_PRIVATE_KEY` | Path (inside the container) to the matching RSA private key used to decrypt backups. **Required for `restore.sh`.** |

### Scheduling / lifecycle

| Variable | Description |
| --- | --- |
| `CRON_TIME` | Cron schedule for backups. Default: `0 3,15 * * *` (03:00 and 15:00 daily). |
| `TZ` | Container timezone. Default: `Europe/Berlin`. |
| `CRON_TZ` | Cron timezone. Default: `Europe/Berlin`. |
| `INIT_BACKUP` | If set (non-empty), run a backup immediately on container start. |
| `INIT_RESTORE` | If set, restore from the latest backup on container start. |
| `DISABLE_CRON` | If set, skip installing the cron job (useful for one-shot seed/restore containers). |

## Helper scripts

The entrypoint generates three scripts and symlinks them onto `PATH`:

| Command | Description |
| --- | --- |
| `backup` (`/backup.sh`) | Dump, compress, encrypt, upload as `backup_<timestamp>.dump.gz.ssl` and `latest.dump.gz.ssl`. |
| `restore [TIMESTAMP]` (`/restore.sh`) | Download, decrypt, and `mongorestore --drop` the given backup (defaults to `latest`). |
| `listbackups` (`/listbackups.sh`) | `aws s3 ls` the backup folder. |

### Trigger a backup on demand

```bash
docker exec mongodb-backup-s3 backup
```

### List available backups

```bash
docker exec mongodb-backup-s3 listbackups
```

### Restore

Restore the latest backup:

```bash
docker exec mongodb-backup-s3 restore
```

Restore a specific backup (pass just the timestamp portion of the filename):

```bash
docker exec mongodb-backup-s3 restore 20260406T155812
```

> Restore uses `mongorestore --drop`, which **drops each collection before
> restoring it**. Be careful when pointing this at a live database.

## Decrypting a backup outside the container

You can also decrypt an archive manually with OpenSSL and restore it with your
own `mongorestore`:

```bash
aws s3 cp s3://my-s3-bucket/prod/mongo/latest.dump.gz.ssl ./latest.dump.gz.ssl

openssl cms -decrypt \
  -in latest.dump.gz.ssl -binary -inform DER \
  -inkey backup.key \
  -out latest.dump.gz

mongorestore --gzip --archive=latest.dump.gz --drop
```

## Logs

Cron writes backup output to `/mongo_backup.log`, which is tailed as the
container's foreground process, so `docker logs -f mongodb-backup-s3` shows
backup activity.

## Building the image locally

```bash
# default (MongoDB Database Tools 100.10.0, amd64 or arm64 auto-detected)
docker build -t mongodb-backup-s3:local .

# pin a different tools release
docker build \
  --build-arg MONGO_TOOLS_VERSION=100.10.0 \
  -t mongodb-backup-s3:local .
```

Verify the bundled tools version after a build:

```bash
docker run --rm mongodb-backup-s3:local mongodump --version
docker run --rm mongodb-backup-s3:local aws --version
```

## Continuous builds (GitHub Actions → Docker Hub)

The repo includes a workflow at
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
that builds a multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it
to Docker Hub.

### Required repository secrets

Configure these under **Settings → Secrets and variables → Actions** on GitHub:

| Secret | Description |
| --- | --- |
| `DOCKERHUB_USERNAME` | Your Docker Hub username. |
| `DOCKERHUB_TOKEN` | A Docker Hub [access token](https://hub.docker.com/settings/security) with **Read & Write** scope. |
| `DOCKERHUB_REPO` | *Optional.* Full repository name (e.g. `myorg/mongodb-backup-s3`). Defaults to `<DOCKERHUB_USERNAME>/<github-repo-name>` if unset. |

### Triggers

- Push to `master` / `main` → builds and pushes `:latest` + `:sha-<short>`.
- Push a tag matching `v*` (e.g. `v1.2.3`) → pushes `:1.2.3`, `:1.2`, `:1`.
- Pull request → builds only (no push), for verification.
- Manual run (**Actions → Build and push Docker image → Run workflow**) — lets
  you override `MONGO_TOOLS_VERSION` at dispatch time.

### Cutting a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow will publish:

- `yourrepo/mongodb-backup-s3:1.0.0`
- `yourrepo/mongodb-backup-s3:1.0`
- `yourrepo/mongodb-backup-s3:1`
- and `:latest` (when the tagged commit is on the default branch).

## Acknowledgements

- Forked from [halvves/mongodb-backup-s3](https://github.com/halvves/mongodb-backup-s3)
- Which was forked from [futurist's fork](https://github.com/futurist) of
  [tutumcloud/mongodb-backup](https://github.com/tutumcloud/mongodb-backup)
