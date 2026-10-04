FROM node:22-bookworm-slim AS build
ENV npm_config_update_notifier=false
WORKDIR /build/console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine
LABEL org.opencontainers.image.source="https://github.com/To6enceto/IntentLatch"
COPY deploy/docker/console-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/console/dist /usr/share/nginx/html
EXPOSE 8080
