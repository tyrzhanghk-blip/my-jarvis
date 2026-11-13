"""企微自建应用适配：回调验签解密 + 出站消息。凭据经 keyring。"""
from __future__ import annotations
import base64
import hashlib
import struct

import httpx

from my_jarvis.gateway.protocol import Entrance, InboundMessage


def _cred(ref_key: str) -> str:
    import keyring
    return keyring.get_password("my-jarvis", ref_key) or ""


def _keys():
    return (_cred("my-jarvis/wecom/corp_id"), _cred("my-jarvis/wecom/secret"),
            _cred("my-jarvis/wecom/callback_token"), _cred("my-jarvis/wecom/aes_key"))


def _sha1(*args: str) -> str:
    return hashlib.sha1("".join(sorted(args)).encode()).hexdigest()


def _aes_decrypt(encrypted_b64: str, aes_key_b64: str) -> bytes:
    key = base64.b64decode(aes_key_b64 + "=")
    data = base64.b64decode(encrypted_b64)
    import cryptography.hazmat.primitives.ciphers as C
    from cryptography.hazmat.primitives.ciphers.algorithms import AES
    from cryptography.hazmat.primitives.ciphers.modes import CBC
    dec = C.Cipher(AES(key), CBC(key[:16])).decryptor()
    padded = dec.update(data) + dec.finalize()
    return padded[:-padded[-1]]


def verify_url(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> tuple[bool, str]:
    _, _, token, aes_key = _keys()
    if not token or not aes_key:
        return False, "wecom credentials not configured (keyring: my-jarvis/wecom/*)"
    sig = _sha1(token, timestamp, nonce, echostr)
    if sig != msg_signature:
        return False, "signature mismatch"
    plain = _aes_decrypt(echostr, aes_key)
    msg_len = struct.unpack(">I", plain[16:20])[0]
    return True, plain[20:20 + msg_len].decode()


async def handle_callback(request, router):
    """POST 回调：验签解密 → InboundMessage → router。"""
    import asyncio
    import xml.etree.ElementTree as ET
    from fastapi.responses import PlainTextResponse
    q = request.query_params
    body = await request.body()
    _, _, token, aes_key = _keys()
    if not token or not aes_key:
        return PlainTextResponse("not configured")
    root = ET.fromstring(body)
    encrypt = root.findtext("Encrypt", "")
    sig = _sha1(token, q.get("timestamp", ""), q.get("nonce", ""), encrypt)
    if sig != q.get("msg_signature", ""):
        return PlainTextResponse("signature mismatch")
    plain = _aes_decrypt(encrypt, aes_key)
    msg_len = struct.unpack(">I", plain[16:20])[0]
    m = ET.fromstring(plain[20:20 + msg_len].decode())
    if m.findtext("MsgType", "") != "text":
        return PlainTextResponse("success")
    asyncio.get_event_loop().create_task(
        router.handle(InboundMessage(entrance=Entrance.wecom, text=m.findtext("Content", ""))))
    return PlainTextResponse("success")


async def send_message(text: str, to_user: str = "@all", agentid: int = 0) -> None:
    """出站：应用消息 API。"""
    corp_id, secret, _, _ = _keys()
    if not corp_id or not secret:
        raise RuntimeError("wecom credentials not configured")
    async with httpx.AsyncClient() as client:
        r = await client.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                             params={"corpid": corp_id, "corpsecret": secret})
        token = r.json().get("access_token")
        if not token:
            raise RuntimeError(f"gettoken failed: {r.json()}")
        await client.post(f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                          json={"touser": to_user, "msgtype": "text", "agentid": agentid,
                                "text": {"content": text[:2048]}})
