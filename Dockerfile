FROM python:3.13-slim

WORKDIR /

# OS deps (build tools are useful for native wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Put the virtualenv on PATH for all subsequent layers
ENV VIRTUAL_ENV=/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy deps first for better caching
COPY pyproject.toml uv.lock* ./

# Create venv, install uv *into the venv*, then sync
RUN python -m venv "$VIRTUAL_ENV" \
 && pip install --no-cache-dir uv \
 && uv sync --frozen

# Now copy your app code
COPY . ./

EXPOSE 10001
CMD ["uvicorn", "app:app", "--port", "10001", "--host", "0.0.0.0"]