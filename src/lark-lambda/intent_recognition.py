import json
import boto3
from typing import Dict, Any, List

def check_if_aws_question(text: str) -> bool:
    bedrock = boto3.client(service_name="bedrock-runtime", region_name="us-west-2")

    prompt = f"""
    请判断以下文本是否是关于AWS和软件开发相关的问题
    只需回答"是"或"否"。
    
    文本: {text}
    """

    # 构建请求体
    request_body: Dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "temperature": 0.5,  # 降低温度以获得更确定的回答
        "messages": [{"role": "user", "content": prompt}],
    }
    
    try:
        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-5-haiku-20241022-v1:0",
            body=json.dumps(request_body),
        )
        
        response_body = json.loads(response.get("body").read())
        content_list: List[Dict[str, str]] = response_body.get("content", [{}])
        result = content_list[0].get("text", "").strip().lower()
        print(f"Claude意图识别结果: {result}")
        
        return "是" in result
    except Exception as e:
        print(f"调用Bedrock API时发生错误: {e}")
        # 发生错误时默认返回False，避免误判
        return False