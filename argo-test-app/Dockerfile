FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
# 환경 변수는 Kubernetes 매니페스트에서 설정할 것입니다.
# ENV APP_VERSION=v1.0
# ENV APP_COLOR=blue
EXPOSE 8080
CMD ["python", "app.py"]