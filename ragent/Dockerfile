FROM python:3.11-slim
WORKDIR /app
COPY ragent/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY common /app/common
COPY ragent/app.py /app/
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
