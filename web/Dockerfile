# The dashboard is built here and baked into the Caddy image: no Node.js on the server, and no web/dist on disk.
# Build context = web/ (docker-compose.yml, service "caddy").
FROM node:24.22.3-alpine3.24 AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# vue-tsc type-checks first; a type error fails the image build
RUN npm run build

FROM caddy:2.11.9-alpine
COPY --from=build /web/dist /srv/web
