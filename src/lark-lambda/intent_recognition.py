import json
import boto3
import os
from typing import Dict, Any, List

RECOGNITION_MODEL_ID = os.environ.get("RECOGNITION_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0").strip()
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-west-2").strip()
KNOWLEDGE_SUMMARY = os.environ.get("KNOWLEDGE_SUMMARY", "").strip()

def check_if_valid_question(text: str) -> bool:
    bedrock = boto3.client(service_name="bedrock-runtime", region_name=BEDROCK_REGION)

    prompt = ""
    # 判断 KNOWLEDGE_SUMMARY 是否为空
    if KNOWLEDGE_SUMMARY == "":
        prompt = f"""
        请判断以下<问题></问题>中的内容是否是关于 AWS 或 软件开发相关
        如果是，回答 "AWS"；若不是，回答 "否"。不要回复任何解释。
        
        <问题>{text}</问题>
        """
    else:
        prompt = f"""
        请判断以下 <问题></问题> 中的内容是否是关于 AWS 或 软件开发相关 或 <企业知识库></企业知识库>中的内容范围
        <企业知识库>{KNOWLEDGE_SUMMARY}</企业知识库>

        <问题>{text}</问题>

        若是关于 "AWS和软件开发相关"，回答 "AWS"；若是关于 "企业知识库的内容范围"，回答 "知识库"；若都不是，回答 "否"。不要回复任何解释。
        """

    # 构建请求体
    request_body: Dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 64,
        "temperature": 1,  # 降低温度以获得更确定的回答
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        response = bedrock.invoke_model(
            modelId=RECOGNITION_MODEL_ID,
            body=json.dumps(request_body),
        )
        
        response_body = json.loads(response.get("body").read())
        content_list: List[Dict[str, str]] = response_body.get("content", [{}])
        result = content_list[0].get("text", "").strip().lower()
        print(f"LLM 意图识别结果: {result}")        

        if "AWS" in result.upper():
            return "AWS"
        elif "知识库" in result:
            return "知识库"
        else:
            return False
    except Exception as e:
        print(f"调用Bedrock API时发生错误: {e}")
        # 发生错误时默认返回False，避免误判
        return False