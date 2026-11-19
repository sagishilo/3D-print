# The backup service: the Postgres client tools of the same major version as the database, plus age (encryption)
# and rclone (cloud storage) for the encrypted off-site copies. See backup.sh.
FROM postgres:16.16-alpine
RUN apk add --no-cache age rclone
COPY backup.sh /backup.sh
ENTRYPOINT ["/bin/sh", "/backup.sh"]
CMD ["loop"]
