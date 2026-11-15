# syntax=docker/dockerfile:1
# The landing page as static files behind nginx. Build from the repository root, since the page
# draws Bleep from the dashboard's component:  docker build -f site/Dockerfile -t bleep-site .

FROM node:24-slim AS build
WORKDIR /repo/site
COPY site/package.json site/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/components/bleep /repo/frontend/components/bleep
COPY frontend/lib /repo/frontend/lib
COPY frontend/app/globals.css frontend/app/icon.svg /repo/frontend/app/
COPY site ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM nginx:1.29-alpine
COPY site/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/site/out /usr/share/nginx/html
EXPOSE 80
