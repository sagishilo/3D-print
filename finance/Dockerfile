# build context = repo root (so common/ can be copied)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY finance/requirements.txt .
RUN pip install -r requirements.txt

COPY common/ /srv/common/
COPY finance/app/ /srv/app/
ENV PYTHONPATH=/srv/common

RUN useradd --system --uid 10001 pf && chown -R pf /srv
USER pf

EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
