# build context = repo root (the gateway imports the shared contracts from src/printfarm)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY gateway/requirements.txt .
RUN pip install -r requirements.txt

# printfarm.contracts + printfarm.bus only need pydantic and nats-py (installed above), so skip the agents' extra deps.
COPY pyproject.toml /srv/pkg/pyproject.toml
COPY src/ /srv/pkg/src/
RUN pip install --no-deps /srv/pkg && rm -rf /srv/pkg

COPY gateway/app/ /srv/app/

RUN useradd --system --uid 10001 pf && chown -R pf /srv
USER pf

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
