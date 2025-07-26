# Dockerfile  (inside C:\Bigdata\bd25_project_f8_a\Kafka)

FROM python:3.11-slim

WORKDIR /app

# Create data directory
RUN mkdir -p /app/data

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy producer.py
COPY producer.py .
# Copy your dataset INTO the image
COPY Reddit_datasetv1.txt /app/data/Reddit_datasetv1.txt
COPY Reddit_datasetv1_initial.txt /app/data/Reddit_datasetv1_initial.txt

CMD ["python", "producer.py"]
