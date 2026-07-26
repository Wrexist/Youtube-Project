FROM python:3.11-slim

# MoviePy shells out to ffmpeg. Not optional — the render stage fails without it.
# fonts-dejavu-core is the fallback engine/services/fonts.py resolves to, and a
# missing font fails TextClip at construction, i.e. after the footage is downloaded.
#
# ImageMagick used to be installed here, with a sed patching its policy.xml so
# TextClip's @-file reads would work. That was MoviePy 1.x behaviour: MoviePy 2
# renders text through Pillow and never invokes ImageMagick. The patch was also
# targeting /etc/ImageMagick-6/, which does not exist on current Debian — the
# `|| true` had been hiding that for as long as it has been here.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY engine ./engine

EXPOSE 8080
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8080"]
