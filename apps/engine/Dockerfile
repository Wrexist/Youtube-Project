FROM python:3.11-slim

# MoviePy shells out to ffmpeg, and ImageMagick backs its TextClip subtitle
# rendering. Neither is optional — the render stage fails at runtime without them.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg imagemagick fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Debian's ImageMagick policy blocks the @-file reads TextClip relies on.
RUN sed -i 's/rights="none" pattern="@\*"/rights="read|write" pattern="@*"/' \
      /etc/ImageMagick-6/policy.xml || true

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY engine ./engine

EXPOSE 8080
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8080"]
