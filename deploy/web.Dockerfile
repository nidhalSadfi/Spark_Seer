# Builds the React dashboard and serves it with Caddy (HTTPS + /api reverse proxy).
FROM node:22-alpine AS build
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci --no-audit --no-fund
COPY frontend/ ./
# Same origin: the browser calls https://<domain>/api/..., which Caddy forwards to the API.
ENV VITE_API_URL=/api
RUN npm run build

FROM caddy:2.8-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
