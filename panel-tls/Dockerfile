FROM nginx:1.28-alpine
RUN apk add --no-cache openssl
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /usr/local/bin/panel-tls-entrypoint
RUN chmod 755 /usr/local/bin/panel-tls-entrypoint
ENTRYPOINT ["/usr/local/bin/panel-tls-entrypoint"]
