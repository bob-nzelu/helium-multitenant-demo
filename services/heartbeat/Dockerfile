FROM python:3.12-slim

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source + databases (schema.sql needed for DB init)
COPY src/ src/
COPY databases/ databases/

# Expose HeartBeat port
EXPOSE 9000

# Run HeartBeat Primary
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9000"]
