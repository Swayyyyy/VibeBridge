FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3456

ENV HOST=0.0.0.0
ENV PORT=3456

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3456"]
