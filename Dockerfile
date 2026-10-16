FROM ghcr.io/astral-sh/uv:0.11.16-python3.12-trixie

WORKDIR /app
ENV UV_NO_DEV=1
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src"
ENTRYPOINT ["python", "-m", "osm_polygon_lucas"]
