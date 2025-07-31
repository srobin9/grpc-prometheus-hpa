# check_proto.py

import streaming_pb2

try:
    # TextRequest 메시지에 어떤 필드가 실제로 정의되어 있는지 확인합니다.
    fields = [field.name for field in streaming_pb2.TextRequest.DESCRIPTOR.fields]
    
    print("--- 진단 시작: streaming_pb2.TextRequest 필드 목록 ---")
    print(fields)
    print("-------------------- 진단 종료 --------------------")

except Exception as e:
    print(f"진단 중 오류 발생: {e}")