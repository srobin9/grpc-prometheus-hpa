# GKE Autopilot Multi gRPC Channel Test
본 가이드를 실행 전에 우선 **README.md**를 먼저 실행하세요.

### 테스트 전략

1.  **채널 재사용**: 소수의 gRPC 채널(예: 3개)를 미리 생성합니다.
2.  **스트림 다중 실행**: 생성된 채널 풀을 사용해 다수의 스트림(예: 30개)을 실행합니다. 스트림들은 가용한 채널에 순환(Round-robin) 방식으로 할당됩니다.
3.  **랜덤 지연**: 각 스트림을 시작하기 전에 짧은 랜덤 지연 시간을 주어, 모든 스트림이 동시에 시작되지 않도록 합니다.

이 테스트를 통해, 만약 로드밸런서가 **채널 단위**로 분산한다면, 첫 번째 채널의 모든 스트림은 Pod A로, 두 번째 채널의 모든 스트림은 Pod B로 향하게 될 것입니다. 반면 **스트림 단위**로 분산한다면(일반적으로 그렇지 않음), 채널에 상관없이 스트림들이 두 Pod에 무작위로 섞여서 분산될 것입니다.

### 테스트용 클라이언트 코드 : `client_channel_test.py`

### 테스트 실행 및 결과 분석

1.  **Gateway Backend Protocol 및 외부 IP 확인:**
    ```bash
    kubectl get gateway vac-hub-gateway -n grpc-test
    ```
    `ADDRESS` 필드에 나타나는 IP 주소를 복사합니다.

2.  **터미널에서 다음 명령어로 클라이언트를 실행합니다.**
    *   채널 3개, 스트림 30개로 테스트합니다.

    ```bash
    # 1. 가상환경 활성화 및 client 디렉토리로 이동
    cd ~/grpc-prometheus-hpa
    source venv/bin/activate
    cd ~/grpc-prometheus-hpa/client

    # 2. gRPC 라이브러리 설치 (필요시)
    pip install -r requirements.txt

    # 3. client 실행
    python client_channel_test.py [GATEWAY_EXTERNAL_IP]:443 --channels 3 --streams 30 --cert_file ./tls.crt
    ```

3.  **서버 Pod의 로그를 확인합니다.**
    *   각 Pod의 로그를 모니터링하여 어떤 스트림이 어느 Pod으로 들어오는지 확인합니다. 서버 로그는 `logging.info("Stream opened. ...")` 부분을 통해 스트림 시작을 알려줍니다.

    ```bash
    # Pod 1 로그 확인
    kubectl logs -f [POD_1_NAME] -c grpc-test

    # Pod 2 로그 확인
    kubectl logs -f [POD_2_NAME] -c grpc-test

    # Pod 3 로그 확인
    kubectl logs -f [POD_3_NAME] -c grpc-test
    ```

#### 결과 예측 및 분석

**시나리오 1: 채널(Connection) 단위 분산 (가장 유력한 결과)**

GKE Gateway와 함께 사용되는 표준 L7 로드밸런서는 TCP 연결(gRPC에서는 `Channel`)을 기준으로 부하를 분산합니다. 따라서 다음과 같은 결과를 예상할 수 있습니다.

*   **Pod 1 로그:** `Channel-0`에서 시작된 스트림들만 보입니다 (예: Stream-0, Stream-3, Stream-6, ...). 약 10개의 스트림이 이 Pod에 할당됩니다.
*   **Pod 2 로그:** `Channel-1`에서 시작된 스트림들만 보입니다 (예: Stream-1, Stream-4, Stream-7, ...). 나머지 10개의 스트림이 이 Pod에 할당됩니다.
*   **Pod 3 로그:** `Channel-2`에서 시작된 스트림들만 보입니다 (예: Stream-2, Stream-5, Stream-8, ...). 나머지 10개의 스트림이 이 Pod에 할당됩니다.

이 결과는 **로드밸런서가 한 번 수립된 TCP 연결(채널)을 계속 동일한 Pod으로 라우팅**하며, 그 채널을 통해 발생하는 모든 gRPC 스트림은 같은 Pod에서 처리된다는 것을 명확히 보여줍니다.

**시나리오 2: 스트림(Request) 단위 분산 (가능성 낮음)**

만약 로드밸런서가 고도로 지능적이어서 개별 gRPC 스트림을 분산할 수 있다면 (일반적인 HTTP/2 로드밸런서는 이렇게 동작하지 않음), 다음과 같은 결과가 나타날 것입니다.

*   **Pod 1 로그:** `Channel-0`과 `Channel-1`에서 온 스트림들이 모두 섞여서 보입니다.
*   **Pod 2 로그:** `Channel-0`과 `Channel-1`에서 온 스트림들이 모두 섞여서 보입니다.
*   **Pod 3 로그:** `Channel-0`과 `Channel-1`에서 온 스트림들이 모두 섞여서 보입니다.

이 테스트를 통해 고객에게 로드밸런서가 채널 단위로 동작함을 명확한 데이터로 보여줄 수 있을 것입니다.