FROM python:3.12-slim

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendored cross-repo packages (CSSV1 D8 helium_hash — canonical SHA-256
# primitives shared across HB / Relay / Float SDK / Reader-Scout SDK).
# Mirrored from helium-services-phase3/packages/helium-hash/. See
# `vendor/README.md` for the update protocol.
COPY vendor/ vendor/
ENV PYTHONPATH=/app/vendor

# Copy source
COPY src/ src/

# Expose port
EXPOSE 8082

# Run with factory pattern
CMD ["uvicorn", "src.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8082"]
