FROM alpine:3.6
RUN apk -v --update add \
        python \
        py-pip \
        groff \
        less \
        mailcap \
        mongodb-tools \
        dcron \
        wget \
        rsync \
        ca-certificates \
        openssl \
        && \
    pip install --upgrade awscli==1.14.5 s3cmd==2.0.1 python-magic && \
    apk -v --purge del py-pip && \
    rm /var/cache/apk/*

RUN mkdir -p /var/log/cron && mkdir -m 0644 -p /var/spool/cron/crontabs && touch /var/log/cron/cron.log && mkdir -m 0644 -p /etc/cron.d

ENV CRON_TIME="0 3,15 * * *" \
  TZ=Europe/Berlin \
  CRON_TZ=Europe/Berlin

COPY run.sh /run.sh

CMD sh /run.sh
