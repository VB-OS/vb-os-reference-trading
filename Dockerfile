FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vendor/vb_os-0.1.0.tar.gz /tmp/vb_os-0.1.0.tar.gz
RUN pip install --no-cache-dir /tmp/vb_os-0.1.0.tar.gz && rm /tmp/vb_os-0.1.0.tar.gz

COPY boundaries/ boundaries/
COPY seed.sql .
COPY seed_template.py .
COPY bootstrap.py .
COPY demo.py .
COPY verify.py .
COPY execution_gateway.py .

ENTRYPOINT ["python3"]
CMD ["demo.py", "--list"]
