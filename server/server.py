import time
import grpc
from concurrent import futures
import logging
import os
import sys

# --- OpenTelemetry 설정 ---
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer
from opentelemetry.sdk.resources import Resource

# Protobuf 및 헬스 체크 관련 import
import streaming_pb2
import streaming_pb2_grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

# --- 로깅 설정 ---
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    stream=sys.stdout)
logging.getLogger("opentelemetry").setLevel(logging.DEBUG)

# --- OpenTelemetry 메트릭 설정 ---
otel_collector_endpoint = os.getenv("OTEL_COLLECTOR_ENDPOINT", "localhost:4317")
logging.info(f"Sending metrics to OTEL Collector at: {otel_collector_endpoint}")

exporter = OTLPMetricExporter(endpoint=otel_collector_endpoint, insecure=True, timeout=10)
reader = PeriodicExportingMetricReader(exporter, export_interval_millis=5000)

# 모든 메트릭에 'service.name' 레이블을 추가하여 식별 가능하게 만듭니다.
resource = Resource(attributes={
    "service.name": "vac-hub-service"
})

provider = MeterProvider(metric_readers=[reader], resource=resource)
metrics.set_meter_provider(provider)

# gRPC 서버 자동 계측
grpc_server_instrumentor = GrpcInstrumentorServer()
grpc_server_instrumentor.instrument()

# 커스텀 메트릭 생성
meter = metrics.get_meter("grpc.server.python.streaming_service")
processed_message_counter = meter.create_counter(
    name="app.messages.processed.count",
    unit="1",
    description="The total number of messages processed by the streaming service"
)

# StreamerService 클래스
class StreamerService(streaming_pb2_grpc.StreamerServicer):
    """gRPC 스트리밍 서비스 구현"""
    def ProcessTextStream(self, request_iterator, context):
        logging.info("Stream opened.")
        message_count = 0
        try:
            for request in request_iterator:
                message_count += 1
                processed_message_counter.add(1)
                time.sleep(0.01)

            logging.info(f"Stream closed. Processed {message_count} messages.")
            return streaming_pb2.TextResponse(message_count=message_count)
        except grpc.RpcError as e:
            # 에러의 상태 코드를 확인합니다.
            if e.code() == grpc.StatusCode.CANCELLED:
                # 클라이언트가 스트림을 정상적으로 (또는 의도적으로) 취소한 경우 INFO로 기록합니다.
                logging.info(f"Stream cancelled by client after {message_count} messages.")
            else:
                # 그 외 예측하지 못한 RpcError는 ERROR로 기록합니다.
                logging.error(f"Stream broken by unexpected RpcError: {e}. Processed {message_count} messages.")
            
            # 클라이언트가 이미 떠났으므로 응답을 보내는 것은 의미가 없을 수 있지만, 
            # 로직상 리턴 구문은 유지합니다.
            return streaming_pb2.TextResponse(message_count=message_count)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    streaming_pb2_grpc.add_StreamerServicer_to_server(StreamerService(), server)

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    server.add_insecure_port("[::]:50051")
    server.start()
    logging.info("gRPC server started on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    try:
        serve()
    finally:
        logging.info("Flushing remaining metrics before exit.")
        metrics.get_meter_provider().shutdown()