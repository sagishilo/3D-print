FROM python:3.12-slim

# OpenSCAD lets the personalization agent render real STL files (≈150 MB).
# Build with --build-arg INSTALL_OPENSCAD=0 for a slimmer image (estimates only).
ARG INSTALL_OPENSCAD=1
RUN if [ "$INSTALL_OPENSCAD" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends openscad fonts-noto-core \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# Social Listening: the Claude SDK and a local multilingual embedding model (~0.5 GB with ONNX runtime), downloaded
# here so the agent needs no internet for embeddings. --build-arg INSTALL_SOCIAL=0 leaves it out (no social agent).
ARG INSTALL_SOCIAL=1
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN if [ "$INSTALL_SOCIAL" = "1" ]; then \
      pip install --no-cache-dir "anthropic>=2.8,<3" "fastembed>=0.9.1,<0.10" \
      && python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/opt/fastembed')" \
      && chmod -R a+rX /opt/fastembed; \
    fi

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config

RUN useradd --create-home printfarm && mkdir -p /app/var/artifacts && chown -R printfarm /app/var
USER printfarm

ENTRYPOINT ["printfarm"]
