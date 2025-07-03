import json
import re
import os
import boto3
from typing import Dict, Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    ReplyMessageResponse,
)

from intent_recognition import check_if_valid_question
import requests

"""
飞书应用 APP_ID 和 SECRET，可支持多应用：
LARK_APP_ID: ID_1,ID_2
LARK_SECRET: SECRET_1,SECRET_2
"""
LARK_APP_ID = os.environ.get("LARK_APP_ID").strip()
LARK_SECRET = os.environ.get("LARK_SECRET").strip()
APP_ID_SECRET_MAP = {}

for i, app_id in enumerate(LARK_APP_ID.split(",")):
    secret = LARK_SECRET.split(",")[i].strip()
    APP_ID_SECRET_MAP[app_id] = secret

KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "").strip()
KNOWLEDGE_MODEL_ARN = os.environ.get("MODEL_ARN", "").strip()

QLI_SERVER_ADDRESS = os.environ.get("QLI_SERVER_ADDRESS", "").strip()

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    print(f"收到事件: {event.get('body', {})}")

    try:
        data = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return create_response(400, "Invalid JSON")

    # 处理飞书开放平台的 URL 合法性验证请求
    if "challenge" in data:
        challenge = data["challenge"]
        return create_response(200, json.dumps({"challenge": challenge}))

    # 处理消息事件
    try:
        response = process_message(data)
        return create_response(200, response)
    except Exception as e:
        print(f"处理消息时发生错误: {e}")
        # 仍然返回 200，否则飞书开放平台会反复重试
        return create_response(200, "Error")


def create_response(status_code: int, body: str) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    }


def remove_mentions(text: str) -> str:
    return re.sub(r"\\?@\w+", "", text).strip()


def process_message(data: Dict[str, Any]) -> str:
    """
    在1秒内返回首次回复，否则飞书会重试
    """
    # 检查消息类型是否为纯文字
    if data.get("event", {}).get("message", {}).get("message_type") != "text":
        return send_lark_request(
            data.get("header", {}).get("app_id", ""),
            data.get("event", {}).get("message", {}).get("message_id", ""),
            json.dumps({"text": "解析消息失败，请发送文本消息"}),
        )

    # 提取消息信息
    app_id = data.get("header", {}).get("app_id", "")
    message_id = data.get("event", {}).get("message", {}).get("message_id", "")

    try:
        content = json.loads(
            data.get("event", {}).get("message", {}).get("content", "{}")
        )
        # 移除 @at
        message_text = remove_mentions(content.get("text", ""))

        # 使用 AWS Bedrock LLM 进行意图识别
        question_type = check_if_valid_question(message_text)

        if question_type:
            if question_type == "AWS":
                # 转发 data 请求到 QLI_SERVER_ADDRESS
                print(f"Sending message to: {QLI_SERVER_ADDRESS}...")
                response = requests.post(
                    QLI_SERVER_ADDRESS,
                    data=json.dumps(data),
                    headers={"Content-Type": "application/json"},
                )
                return {"Result": "Send message successfully."}
            elif question_type == "知识库":
                if KNOWLEDGE_BASE_ID and KNOWLEDGE_MODEL_ARN:
                    knowledge_base_client = boto3.client(
                        service_name="bedrock-agent-runtime"
                    )
                    response = knowledge_base_client.retrieve_and_generate(
                        input={"text": message_text},
                        retrieveAndGenerateConfiguration={
                            "type": "KNOWLEDGE_BASE",
                            "knowledgeBaseConfiguration": {
                                "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                                "modelArn": KNOWLEDGE_MODEL_ARN,
                            },
                        },
                    )
                    response_text = response["output"]["text"]
                    return send_lark_request(
                        app_id,
                        message_id,
                        json.dumps({"text": f"调用知识库的结果：{response_text}"}),
                    )
                else:
                    return send_lark_request(
                        app_id, message_id, json.dumps({"text": "知识库尚未实现"})
                    )
            else:
                return send_lark_request(
                    app_id,
                    message_id,
                    json.dumps({"text": f"未知问题类型：{question_type}"}),
                )

        else:
            response_text = "抱歉，我只能回答关于 AWS、软件开发相关以及企业内的知识库问题。"
            return send_lark_request(
                app_id, message_id, json.dumps({"text": response_text})
            )

    except Exception as e:
        print(f"处理问题时发生错误: {e}")
        return send_lark_request(
            app_id, message_id, json.dumps({"Error": "处理问题时发生错误"})
        )


def send_lark_request(app_id: str, message_id: str, content: str) -> str:
    # 检查必要参数
    if not app_id or not message_id or not content:
        print("缺少必要参数")
        return {"Error": "缺少必要参数"}

    # 获取应用密钥
    app_secret = APP_ID_SECRET_MAP.get(app_id)
    if not app_secret:
        print(f"未找到应用ID对应的密钥: {app_id}")
        return {"Error": "未找到应用ID对应的密钥"}

    try:
        # 创建飞书客户端
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

        # 构造请求对象
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type("text")
                .reply_in_thread(False)
                .build()
            )
            .build()
        )

        # 发起请求
        response: ReplyMessageResponse = client.im.v1.message.reply(request)

        # 处理响应
        if not response.success():
            print(f"飞书API调用失败: {response.msg}")
            return {"Error": f"{response.msg}"}
    except Exception as e:
        print(f"发送飞书消息时发生错误: {e}")
        return {"Error": str(e)}
