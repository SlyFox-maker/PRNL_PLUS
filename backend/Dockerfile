FROM golang:1.26.1-bookworm AS creators
RUN apt-get update && apt-get install -y --no-install-recommends git python3 && rm -rf /var/lib/apt/lists/*
ARG UPSTREAM_REF=820a6310af5fc1e4f77ef7327bb2594664542a09
ARG XRAY_REF=v26.7.28
RUN git init /src && cd /src && git remote add origin https://github.com/kulikov0/whitelist-bypass.git && git fetch --depth 1 origin "$UPSTREAM_REF" && git checkout FETCH_HEAD
COPY integration /integration
RUN python3 /integration/patch_upstream.py /src
WORKDIR /src
RUN mkdir /out && for platform in vk telemost wbstream dion bitrix; do go build -trimpath -o /out/headless-$platform-creator ./headless/$platform; done && go build -trimpath -o /out/relay ./relay
RUN git clone --depth 1 --branch "$XRAY_REF" https://github.com/XTLS/Xray-core.git /xray-src && cd /xray-src && CGO_ENABLED=0 go build -trimpath -buildvcs=false -o /out/xray ./main

FROM node:22-bookworm AS desktop_creator
COPY --from=creators /src/creator-app /src/creator-app
WORKDIR /src/creator-app
RUN npm ci && npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers WLP_TELEMETRY=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
RUN apt-get update && apt-get install -y --no-install-recommends libgtk-3-0 libnss3 libasound2 libxss1 libxtst6 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libgbm1 libx11-xcb1 libxcb-dri3-0 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxshmfence1 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 10001 wlp && mkdir /runtime /panel-tls && chown wlp:wlp /runtime /panel-tls && chmod 700 /runtime /panel-tls
COPY --from=creators /out/ /opt/creators/
COPY --from=desktop_creator /src/creator-app /opt/desktop-creator/creator-app
COPY --from=creators /src/LICENSE /opt/desktop-creator/whitelist-bypass-LICENSE
COPY --from=creators /src/hooks /opt/desktop-creator/hooks
COPY --from=creators /out/headless-vk-creator /opt/desktop-creator/headless/vk/headless-vk-creator
COPY --from=creators /out/headless-telemost-creator /opt/desktop-creator/headless/telemost/headless-telemost-creator
COPY --from=creators /out/headless-wbstream-creator /opt/desktop-creator/headless/wbstream/headless-wbstream-creator
COPY --from=creators /out/headless-dion-creator /opt/desktop-creator/headless/dion/headless-dion-creator
COPY --from=creators /out/headless-bitrix-creator /opt/desktop-creator/headless/bitrix/headless-bitrix-creator
COPY --from=creators /out/relay /opt/desktop-creator/relay/relay
COPY --from=creators /out/xray /usr/local/bin/xray
COPY backend/app ./app
USER wlp
ENV RUNTIME_DIR=/runtime
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
