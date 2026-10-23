# BMS Monitor frontend image.
#
# Multi-stage: build the production bundle with Node, then serve the
# static files with nginx, which also proxies /api to the backend so
# the browser only ever talks to one origin (no CORS, no second port
# to publish). Used by the `frontend` service in docker-compose.yml.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Relative base: every API call goes through the nginx proxy below.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
