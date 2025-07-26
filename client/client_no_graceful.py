import grpc
import time
import threading
import argparse

import streaming_pb2
import streaming_pb2_grpc

def generate_messages():
    """메시지를 무한정 생성하는 제너레이터"""
    i = 0
    while True:
        yield streaming_pb2.TextRequest(message=f"This is message number {i}")
        i += 1
        time.sleep(0.1) # 0.1초마다 메시지 전송

def run_stream(server_address: str, root_certs: bytes):
    """단일 gRPC 스트림을 실행하는 함수"""
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
    # insecure_channel을 secure_channel로 변경하고, 인증서 정보를 전달합니다.
    # 'grpc.ssl_target_name_override' 옵션은 자체 서명 인증서의 도메인 이름을 지정합니다.
    with grpc.secure_channel(
        server_address, 
        credentials, 
        options=(('grpc.ssl_target_name_override', 'grpc.example.com'),)
    ) as channel:
        stub = streaming_pb2_grpc.StreamerStub(channel)
        print(f"Starting a new stream to {server_address}...")
        try:
            response = stub.ProcessTextStream(generate_messages())
            print(f"Stream finished. Server processed {response.message_count} messages.")
        except grpc.RpcError as e:
            print(f"Stream failed with error: {e.code()} - {e.details()}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("server_address", help="The gRPC server address (e.g., 34.12.34.56:50051)")
    parser.add_argument("--streams", type=int, default=5, help="Number of concurrent streams to run")
    parser.add_argument("--cert_file", help="Path to the server's certificate file", required=True)
    args = parser.parse_args()

    # 인증서 파일을 읽어들입니다.
    with open(args.cert_file, 'rb') as f:
        root_certs = f.read()
    
    threads = []
    for _ in range(args.streams):
        # run_stream 함수에 인증서 내용을 전달합니다.
        thread = threading.Thread(target=run_stream, args=(args.server_address, root_certs))
        threads.append(thread)
        thread.start()
        time.sleep(0.5) # 스트림을 약간의 시간차를 두고 시작

    for thread in threads:
        thread.join()
