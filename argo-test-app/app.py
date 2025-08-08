from flask import Flask
import os

app = Flask(__name__)

# 환경 변수에서 버전과 색상을 가져옵니다. 기본값은 'v1.0'과 'blue'입니다.
version = os.environ.get('APP_VERSION', 'v1.0')
color = os.environ.get('APP_COLOR', 'blue')

@app.route('/')
def hello():
    # HTML 응답에 버전과 색상 정보를 포함합니다.
    return f'<body style="background-color:{color}; color:white;font-family: sans-serif;"><h1>Hello from Version: {version}({color})</h1></body>'
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)