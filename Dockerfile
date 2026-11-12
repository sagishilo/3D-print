FROM python:3.12-slim

# OpenSCAD lets the personalization agent render real STL files (≈150 MB).
# Build with --build-arg INSTALL_OPENSCAD=0 for a slimmer image (estimates only).
ARG INSTALL_OPENSCAD=1
RUN if [ "$INSTALL_OPENSCAD" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends openscad fonts-noto-core \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config

RUN useradd --create-home printfarm && mkdir -p /app/var/artifacts && chown -R printfarm /app/var
USER printfarm

ENTRYPOINT ["printfarm"]
