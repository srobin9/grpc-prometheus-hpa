import grpc
import time
import threading
import argparse
import random

import streaming_pb2
import streaming_pb2_grpc

def generate_messages(stream_id: int):
    """메시지를 무한정 생성하는 제너레이터"""
    i = 0
    while True:
        yield streaming_pb2.TextRequest(message=f"[Stream {stream_id}] This is message number {i}")
        i += 1
        time.sleep(0.1) 

def run_stream_on_channel(channel: grpc.Channel, stream_id: int, channel_id: int):
    """
    미리 생성된 채널을 사용하여 단일 gRPC 스트림을 실행하는 함수.
    서버의 max_connection_age로 인한 재연결을 시도합니다.
    """
    while True: # 서버 연결 종료 시 자동 재연결을 위한 루프
        try:
            stub = streaming_pb2_grpc.StreamerStub(channel)
            print(f"Channel-{channel_id}: Starting Stream-{stream_id}...")
            
            # 스트림 시작
            response_iterator = stub.ProcessTextStream(generate_messages(stream_id))
            
            # 스트림에서 오는 메시지를 소비해야 연결이 유지됩니다.
            # 실제로는 이 부분에서 응답을 처리하는 로직이 들어갈 수 있습니다.
            for response in response_iterator:
                # 이 예제에서는 서버가 스트림 종료 시에만 단일 응답을 보내므로,
                # 이 루프는 스트림이 정상 종료될 때 한 번 실행됩니다.
                print(f"Channel-{channel_id}, Stream-{stream_id}: Server processed {response.message_count} messages.")

        except grpc.RpcError as e:
            # 서버가 max_connection_age로 연결을 종료하면 UNAVAILABLE 코드가 발생합니다.
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                print(f"Channel-{channel_id}, Stream-{stream_id}: Connection likely closed by server. Reconnecting automatically...")
            else:
                # 그 외 다른 RPC 오류
                print(f"Channel-{channel_id}, Stream-{stream_id}: Stream failed with RPC error: {e.code()} - {e.details()}. Retrying...")

        except Exception as e:
            print(f"Channel-{channel_id}, Stream-{stream_id}: An unexpected error occurred: {e}. Retrying...")

        # 재연결 전, 약간의 무작위 지연(Jitter)을 줍니다.
        reconnect_delay = random.uniform(1, 5) 
        print(f"Channel-{channel_id}, Stream-{stream_id}: Will attempt to reconnect in {reconnect_delay:.2f} seconds.")
        time.sleep(reconnect_delay)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gRPC stream load balancing test client")
    parser.add_argument("server_address", help="The gRPC server address (e.g., 34.12.34.56:443)")
    parser.add_argument("--streams", type=int, default=15, help="Total number of concurrent streams to run")
    parser.add_argument("--channels", type=int, default=2, help="Number of gRPC channels to create and reuse") # <<< 채널 개수 인자 추가
    parser.add_argument("--cert_file", help="Path to the server's certificate file", required=True)
    args = parser.parse_args()

    # 인증서 파일을 읽어들입니다.
    with open(args.cert_file, 'rb') as f:
        root_certs = f.read()
    
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
    
    # --- 핵심 변경 사항: 지정된 수의 채널을 미리 생성 ---
    print(f"Creating a pool of {args.channels} channels...")
    channel_pool = []
    for i in range(args.channels):
        channel = grpc.secure_channel(
            args.server_address, 
            credentials, 
            options=(('grpc.ssl_target_name_override', 'grpc.example.com'),)
        )
        channel_pool.append(channel)
        print(f"Channel-{i} created for {args.server_address}")

    threads = []
    try:
        # --- 핵심 변경 사항: 스트림을 채널 풀에 분산하여 할당 ---
        for i in range(args.streams):
            # 라운드 로빈 방식으로 채널 선택
            channel_index = i % args.channels
            selected_channel = channel_pool[channel_index]
            
            # 스트림 실행 함수에 (채널, 스트림 ID, 채널 ID)를 전달
            thread = threading.Thread(
                target=run_stream_on_channel, 
                args=(selected_channel, i, channel_index)
            )
            threads.append(thread)
            thread.start()
            
            # 고객 요청대로 스트림 시작 사이에 랜덤 지연 추가
            time.sleep(random.uniform(0.1, 0.5))

        for thread in threads:
            thread.join()
            
    finally:
        # 모든 작업이 끝나면 채널을 닫아줍니다.
        print("Closing all channels...")
        for channel in channel_pool:
            channel.close()