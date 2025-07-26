import grpc
import time
import threading
import argparse
import random

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
    """서버의 연결 종료를 예상하고 자동으로 재연결하는 단일 gRPC 스트림을 실행하는 함수"""
    
    while True: # 스트림이 어떤 이유로든 종료되면, 자동으로 재시도하기 위한 무한 루프
        try:
            credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
            with grpc.secure_channel(
                server_address, 
                credentials, 
                options=(('grpc.ssl_target_name_override', 'grpc.example.com'),)
            ) as channel:
                stub = streaming_pb2_grpc.StreamerStub(channel)
                print(f"Starting a new stream to {server_address}...")
                
                # 스트림 시작
                response = stub.ProcessTextStream(generate_messages())
                
                # 스트림이 정상적으로 완료된 경우 (실제로는 거의 발생하지 않음)
                print(f"Stream finished cleanly. Server processed {response.message_count} messages.")
                break # 정상 종료 시에는 루프 탈출

        except grpc.RpcError as e:
            # 서버가 max_connection_age로 연결을 종료하면 UNAVAILABLE 코드가 발생합니다.
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                print(f"Connection likely closed by server for rebalancing. Reconnecting automatically...")
            else:
                # 그 외 다른 RPC 오류 (네트워크 문제 등)
                print(f"Stream failed with RPC error: {e.code()} - {e.details()}. Retrying...")

        except Exception as e:
            # gRPC 외의 예외 처리
            print(f"An unexpected error occurred: {e}. Retrying...")

        # 재연결 전, 모든 클라이언트가 동시에 재연결을 시도하는 것을 막기 위해
        # 약간의 무작위 지연(Jitter)을 줍니다.
        reconnect_delay = random.uniform(1, 5) 
        print(f"Will attempt to reconnect in {reconnect_delay:.2f} seconds.")
        time.sleep(reconnect_delay)

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
