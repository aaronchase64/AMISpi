FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
COPY pyproject.toml /app/
COPY amis_pi/ /app/amis_pi/
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Läuft als root im Container, damit Port 502 und das UART-Device ohne Zusatzrechte nutzbar sind.
ENTRYPOINT ["python", "-m", "amis_pi"]
CMD ["-c", "/config/config.toml"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=150s --retries=3 CMD ["python", "-m", "amis_pi.healthcheck"]

FROM runtime AS test
RUN pip install --no-cache-dir '.[test]'
COPY tests/ /app/tests/
ENTRYPOINT ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM runtime AS production
