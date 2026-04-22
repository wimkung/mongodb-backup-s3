# syntax=docker/dockerfile:1.6
#
# mongodb-backup-s3
#
# - MongoDB Database Tools 100.x  -> supports server 4.4 through 8.x (incl. 8.2)
# - AWS CLI v2                    -> current, signature v4 by default
# - OpenSSL                       -> S/MIME asymmetric encryption of dumps
# - cron                          -> scheduled backups
#
FROM debian:bookworm-slim

ARG MONGO_TOOLS_VERSION=100.10.0
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    CRON_TIME="0 3,15 * * *" \
    TZ=Europe/Berlin \
    CRON_TZ=Europe/Berlin

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        cron \
        gnupg \
        openssl \
        tzdata \
        unzip \
        xz-utils; \
    \
    # ---- MongoDB Database Tools (official .deb from mongodb.org) -------------
    case "${TARGETARCH:-amd64}" in \
        amd64) MDB_ARCH=x86_64 ; AWS_ARCH=x86_64 ;; \
        arm64) MDB_ARCH=arm64  ; AWS_ARCH=aarch64 ;; \
        *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/mdbtools.deb \
        "https://fastdl.mongodb.org/tools/db/mongodb-database-tools-debian12-${MDB_ARCH}-${MONGO_TOOLS_VERSION}.deb"; \
    apt-get install -y --no-install-recommends /tmp/mdbtools.deb; \
    rm -f /tmp/mdbtools.deb; \
    \
    # ---- AWS CLI v2 ----------------------------------------------------------
    curl -fsSLo /tmp/awscli.zip \
        "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip"; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscli.zip /tmp/aws; \
    \
    # ---- cleanup -------------------------------------------------------------
    apt-get purge -y --auto-remove curl gnupg unzip xz-utils; \
    rm -rf /var/lib/apt/lists/*; \
    \
    mkdir -p /var/spool/cron/crontabs /etc/cron.d; \
    touch /mongo_backup.log

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["bash", "/run.sh"]

