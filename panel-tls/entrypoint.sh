#!/bin/sh
set -eu

directory=/panel-tls
mkdir -p "$directory"
chown 10001:10001 "$directory"
chmod 700 "$directory"

generated_access_path=0
if [ ! -s "$directory/access-path" ]; then
  umask 077
  openssl rand -base64 48 | tr -dc 'A-Za-z0-9_-' | head -c 48 > "$directory/access-path"
  printf '\n' >> "$directory/access-path"
  chown 10001:10001 "$directory/access-path"
  chmod 600 "$directory/access-path"
  generated_access_path=1
fi

if [ ! -s "$directory/ip" ]; then
  initial_target=${PANEL_CERT_HOST:-localhost}
  case "$initial_target" in
    localhost) ;;
    *[!0-9A-Fa-f:.]*|'') echo "PANEL_CERT_HOST must be localhost or an IP address" >&2; exit 1 ;;
  esac
  printf '%s\n' "$initial_target" > "$directory/ip"
  chown 10001:10001 "$directory/ip"
  chmod 600 "$directory/ip"
fi

if [ "$generated_access_path" = 1 ]; then
  target=$(cat "$directory/ip")
  if [ "$target" = localhost ]; then
    origin="https://localhost:${PANEL_HTTP_PORT:-8080}"
  else
    port=${PANEL_HTTPS_PORT:-443}
    origin="https://$target"
    [ "$port" = 443 ] || origin="$origin:$port"
  fi
  printf '\n[PRNL] Первый запуск. Ссылка для входа: %s/%s/\n' "$origin" "$(cat "$directory/access-path")"
  printf '[PRNL] Это секретная ссылка: не публикуйте её и сохраните в менеджере паролей.\n\n'
fi

render_access_config() {
  path=$(cat "$directory/access-path")
  case "$path" in *[!A-Za-z0-9_-]*|'') return 1 ;; esac
  [ "${#path}" -ge 32 ] || return 1
  cat > "$directory/access-path.conf.new" <<EOF
location = /$path { return 308 /$path/; }
location = /$path/api/login {
  limit_req zone=panel_login burst=8 nodelay;
  limit_req zone=panel_per_ip burst=100 nodelay;
  proxy_pass http://web:80/api/login;
}
location ^~ /$path/ {
  limit_req zone=panel_per_ip burst=100 nodelay;
  proxy_pass http://web:80/;
}
location / { return 404; }
EOF
  chown 10001:10001 "$directory/access-path.conf.new"
  chmod 600 "$directory/access-path.conf.new"
  mv -f "$directory/access-path.conf.new" "$directory/access-path.conf"
}

render_access_config
rendered_access_path=$(cat "$directory/access-path")

ensure_ca() {
  if [ -s "$directory/ca.key" ] && [ -s "$directory/ca.crt" ]; then
    return
  fi
  umask 077
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -subj "/CN=PRNL Local Root CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -keyout "$directory/ca.key.new" -out "$directory/ca.crt.new" >/dev/null 2>&1
  chmod 600 "$directory/ca.key.new"
  chmod 644 "$directory/ca.crt.new"
  mv -f "$directory/ca.key.new" "$directory/ca.key"
  mv -f "$directory/ca.crt.new" "$directory/ca.crt"
}

renew_certificate() {
  target=$(cat "$directory/ip" 2>/dev/null || true)
  target=${target:-localhost}
  issued_target=$(cat "$directory/issued-ip" 2>/dev/null || true)
  format=$(cat "$directory/cert-format" 2>/dev/null || true)
  if [ "$target" = "$issued_target" ] && [ "$format" = 3 ] && [ -s "$directory/key.pem" ] && [ -s "$directory/cert.pem" ] &&
     openssl x509 -in "$directory/cert.pem" -noout -checkend 2592000 >/dev/null 2>&1; then
    return 1
  fi

  san=DNS:localhost,IP:127.0.0.1
  if [ "$target" != localhost ] && [ "$target" != 127.0.0.1 ]; then
    san="$san,IP:$target"
  fi
  umask 077
  openssl req -new -newkey rsa:3072 -sha256 -nodes \
    -subj "/CN=$target" \
    -keyout "$directory/key.new" -out "$directory/request.csr" >/dev/null 2>&1
  cat > "$directory/cert.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$san
EOF
  openssl x509 -req -in "$directory/request.csr" -CA "$directory/ca.crt" -CAkey "$directory/ca.key" \
    -CAcreateserial -out "$directory/cert.new" -days 365 -sha256 -extfile "$directory/cert.ext" >/dev/null 2>&1
  chmod 600 "$directory/key.new"
  chmod 644 "$directory/cert.new"
  mv -f "$directory/key.new" "$directory/key.pem"
  mv -f "$directory/cert.new" "$directory/cert.pem"
  printf '%s\n' "$target" > "$directory/issued-ip"
  chmod 644 "$directory/issued-ip"
  printf '3\n' > "$directory/cert-format"
  chmod 644 "$directory/cert-format"
  return 0
}

ensure_ca
renew_certificate || true
nginx -g 'daemon off;' &
nginx_pid=$!
trap 'kill "$nginx_pid" 2>/dev/null || true' INT TERM

while kill -0 "$nginx_pid" 2>/dev/null; do
  sleep 2
  current_access_path=$(cat "$directory/access-path" 2>/dev/null || true)
  if [ -n "$current_access_path" ] && [ "$current_access_path" != "$rendered_access_path" ]; then
    if render_access_config; then
      nginx -s reload
      rendered_access_path=$current_access_path
    fi
  fi
  if renew_certificate; then
    nginx -s reload
  fi
done
wait "$nginx_pid"
