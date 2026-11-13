#!/usr/bin/env bash
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

fail() { printf '%s\n' "$*" >&2; exit 1; }
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose --env-file .env --env-file images.env \
      -f docker-compose.yml -f compose.server.yml "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose --env-file .env --env-file images.env \
      -f docker-compose.yml -f compose.server.yml "$@"
  else
    fail "Docker Compose is not installed."
  fi
}

# Create the bind-mount directories configured by PLANE_DATA_DIR.
# Read .env as data; do not source it as shell code.
create_data_directories() {
  local data_dir
  data_dir="$(sed -n 's/^PLANE_DATA_DIR=//p' .env)"
  [[ -n $data_dir ]] || fail 'PLANE_DATA_DIR is missing from .env (example: PLANE_DATA_DIR=../plane-data).'
  case "$data_dir" in
    /*|./*|../*) ;;
    *) fail 'PLANE_DATA_DIR must be absolute or begin with ./ or ../.' ;;
  esac

  mkdir -p -- \
    "$data_dir"/{postgres,redis,rabbitmq,minio} \
    "$data_dir"/logs/{api,worker,beat-worker,migrator} \
    "$data_dir"/proxy/{config,data}
  printf 'Prepared Plane bind-mount directories under: %s\n' "$data_dir"
}

init() {
  [[ ! -e .env ]] || { echo '.env already exists; preserved existing configuration and secrets.'; create_data_directories; return; }
  local url="${1:-}" scheme authority host port http_port=80 https_port=443 site_address
  [[ $url =~ ^(https?)://([[:alnum:]][[:alnum:].-]*)(:([0-9]+))?/?$ ]] || fail 'Usage: ./deploy.sh init https://plane.example.com (or http://SERVER_IP:8080)'
  scheme="${BASH_REMATCH[1]}" host="${BASH_REMATCH[2]}" port="${BASH_REMATCH[4]:-}"
  [[ -z $port || (${#port} -le 5 && $((10#$port)) -ge 1 && $((10#$port)) -le 65535) ]] || fail 'Port must be between 1 and 65535.'
  url="${url%/}" authority="${url#*://}"
  if [[ $scheme == https ]]; then
    site_address="$host"
    https_port="${port:-443}"
  else
    site_address=:80
    http_port="${port:-80}"
  fi
  command -v openssl >/dev/null || fail 'Install openssl to generate deployment secrets.'
  local secret live_secret db_password mq_password access_key storage_secret
  secret="$(openssl rand -hex 32)" live_secret="$(openssl rand -hex 32)"
  db_password="$(openssl rand -hex 24)" mq_password="$(openssl rand -hex 24)"
  access_key="$(openssl rand -hex 10)" storage_secret="$(openssl rand -hex 32)"
  (
    umask 077
    set -o noclobber
    sed \
      -e "s|^APP_DOMAIN=.*|APP_DOMAIN=$authority|" \
      -e "s|^WEB_URL=.*|WEB_URL=$url|" \
      -e "s|^CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=$url|" \
      -e "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$site_address|" \
      -e "s|^LISTEN_HTTP_PORT=.*|LISTEN_HTTP_PORT=$http_port|" \
      -e "s|^LISTEN_HTTPS_PORT=.*|LISTEN_HTTPS_PORT=$https_port|" \
      -e "s|^SECRET_KEY=.*|SECRET_KEY=$secret|" \
      -e "s|^LIVE_SERVER_SECRET_KEY=.*|LIVE_SERVER_SECRET_KEY=$live_secret|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$db_password|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://plane:$db_password@plane-db:5432/plane|" \
      -e "s|^RABBITMQ_PASSWORD=.*|RABBITMQ_PASSWORD=$mq_password|" \
      -e "s|^AMQP_URL=.*|AMQP_URL=amqp://plane:$mq_password@plane-mq:5672/plane|" \
      -e "s|^AWS_ACCESS_KEY_ID=.*|AWS_ACCESS_KEY_ID=$access_key|" \
      -e "s|^AWS_SECRET_ACCESS_KEY=.*|AWS_SECRET_ACCESS_KEY=$storage_secret|" \
      .env.example > .env
  )
  create_data_directories
  echo 'Created .env with random secrets. Keep this file and the Plane data directory between upgrades.'
}

require_configuration() {
  [[ -s .env && -s images.env ]] || fail 'Run ./deploy.sh init <public-url> inside an extracted deployment bundle first.'
  # The config file is dotenv data. Never source it as shell code.
  local key value
  for key in SECRET_KEY LIVE_SERVER_SECRET_KEY POSTGRES_PASSWORD RABBITMQ_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY DATABASE_URL AMQP_URL WEB_URL; do
    value="$(sed -n "s/^$key=//p" .env)"
    [[ -n $value && $value != change-this-key-on-deployment ]] || fail "Missing or placeholder $key in .env"
  done
  compose config --quiet
}

load_images() {
  local arch expected image
  arch="$(docker info --format '{{.Architecture}}')"
  case "$arch" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) fail "Unsupported Docker server architecture: $arch" ;; esac
  expected="$(sed -n 's/^PLANE_ARCH=//p' images.env)"
  [[ $arch == "$expected" ]] || fail "This bundle targets $expected; Docker server is $arch. Download the matching architecture."
  [[ -s SHA256SUMS ]] || fail 'Missing bundle checksums.'
  sha256sum --check --quiet SHA256SUMS
  for image in web space admin live backend proxy dependencies; do
    [[ -s images/$image.tar.gz ]] || fail "Missing images/$image.tar.gz"
    docker load --input "images/$image.tar.gz"
  done
}

case "${1:-help}" in
  init) init "${2:-}" ;;
  load) require_configuration; load_images ;;
  up)
    require_configuration
    load_images
    wait_timeout="${DEPLOY_WAIT_TIMEOUT:-300}"
    compose up -d --no-build --pull never --wait --wait-timeout "$wait_timeout" plane-db plane-redis plane-mq plane-minio
    compose stop --timeout 30 proxy web space admin live api worker beat-worker
    # Explicit one-shot migration: a failure exits before any new application starts.
    compose run --rm --no-deps --pull never migrator
    compose up -d --no-build --pull never --wait --wait-timeout "$wait_timeout" api worker beat-worker web space admin live proxy
    compose ps
    echo 'Deployment started. Open the WEB_URL in .env; first-time instance setup is under /god-mode/.'
    ;;
  status) require_configuration; compose ps ;;
  logs) require_configuration; shift; compose logs --tail 100 "$@" ;;
  stop) require_configuration; compose stop ;;
  *)
    echo 'Usage: ./deploy.sh init <public-url> | up | load | status | logs [service] | stop'
    ;;
esac
