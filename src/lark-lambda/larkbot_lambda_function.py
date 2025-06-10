import json
import re
from typing import Dict, Any, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    ReplyMessageResponse
)

from intent_recognition import check_if_aws_question

# 应用ID和密钥映射
APP_ID_SECRET_MAP = {
    
}

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    print(f"收到事件: {event.get('body', {})}")

    try:
        data = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return _create_response(400, "Invalid JSON")

    # 处理URL验证请求
    if "challenge" in data:
        challenge = data["challenge"]
        return _create_response(200, json.dumps({"challenge": challenge}))
    
    # 处理消息事件
    try:
        response = process_message(data)
        return _create_response(200, response)
    except Exception as e:
        print(f"处理消息时发生错误: {e}")
        return _create_response(200, "Error")


def _create_response(status_code: int, body: str) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    }


def remove_mentions(text: str) -> str:
    return re.sub(r'\\?@\w+', '', text).strip()


def process_message(data: Dict[str, Any]) -> str:
    # 检查消息类型
    if data.get("event", {}).get("message", {}).get("message_type") != "text":
        return send_lark_request(
            data.get("header", {}).get("app_id", ""),
            data.get("event", {}).get("message", {}).get("message_id", ""),
            json.dumps({"text": "解析消息失败，请发送文本消息"})
        )

    # 提取消息信息
    app_id = data.get("header", {}).get("app_id", "")
    message_id = data.get("event", {}).get("message", {}).get("message_id", "")
    
    try:
        content = json.loads(data.get("event", {}).get("message", {}).get("content", "{}"))
        message_text = content.get("text", "")
    except (json.JSONDecodeError, AttributeError):
        return send_lark_request(app_id, message_id, json.dumps({"text": "解析消息内容失败"}))
    
    # 移除@提及
    message_text = remove_mentions(message_text)
    
    # 使用AWS Bedrock Claude进行意图识别
    try:
        is_aws_question = check_if_aws_question(message_text)
        
        if is_aws_question:
            response_text = "收到问题，处理中..."
        else:
            response_text = "抱歉，我只能回答关于AWS和软件开发相关的问题。"
            
        return send_lark_request(app_id, message_id, json.dumps({"text": response_text}))
    except Exception as e:
        print(f"意图识别过程中发生错误: {e}")
        return send_lark_request(app_id, message_id, json.dumps({"text": "处理消息时发生错误，请稍后再试"}))


def send_lark_request(app_id: str, message_id: str, content: str) -> str:
    # 检查必要参数
    if not app_id or not message_id or not content:
        print("缺少必要参数")
        return "Missing required parameters"
    
    # 获取应用密钥
    app_secret = APP_ID_SECRET_MAP.get(app_id)
    if not app_secret:
        print(f"未找到应用ID对应的密钥: {app_id}")
        return "Invalid app_id"
    
    try:
        # 创建客户端
        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )
        
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
            return response.msg

        return str(response.code)
    except Exception as e:
        print(f"发送飞书消息时发生错误: {e}")
        return f"Error: {str(e)}"