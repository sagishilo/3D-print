# PostgreSQL 16 (the same Alpine image as before) + the pgvector extension for the social agent's embeddings.
# Built on the existing base on purpose: switching to a Debian-based image would change text collation (musl vs
# glibc) under the existing data directory and silently break text indexes.
FROM postgres:16.16-alpine

ARG PGVECTOR_VERSION=v0.8.0
RUN apk add --no-cache --virtual .build-pgvector git build-base \
 && git clone --depth 1 --branch "${PGVECTOR_VERSION}" https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && cd /tmp/pgvector \
 && make with_llvm=no OPTFLAGS="" \
 && make with_llvm=no install \
 && cd / && rm -rf /tmp/pgvector \
 && apk del .build-pgvector
