"""
예측 실행권（ceoai.kr 크레딧 결제 연동）

ceoai.kr 이 크레딧을 차감하고 발급한 실행권을 확인·소진하고, 예측이 실패하면
환불을、성공하면 완료를 ceoai.kr 에 알린다.

실행권 형식（ceoai.kr functions/_predictor.js 와 같아야 한다）:
    base64url(JSON{"v":1,"oid":<UUID>,"exp":<ms>}) + "." + hex(HMAC-SHA256(secret, "ticket." + payload))
콜백 서명:
    X-Predictor-Signature = hex(HMAC-SHA256(secret, action + "." + oid))   # action: complete | refund

PREDICTOR_TICKET_SECRET 이 비어 있으면 실행권 없이 열린다（로컬 개발용）.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from typing import Optional

import httpx

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.predictor_ticket')

_OID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TICKETS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'tickets')


class TicketError(ValueError):
    """실행권이 없거나、위조됐거나、만료됐거나、이미 쓰였다"""


def ticket_required() -> bool:
    return bool(Config.PREDICTOR_TICKET_SECRET)


def _sign(message: str, secret: Optional[str] = None) -> str:
    key = (secret or Config.PREDICTOR_TICKET_SECRET or "").encode()
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def verify_ticket(ticket: str, now_ms: Optional[int] = None, secret: Optional[str] = None) -> str:
    """실행권 서명과 만료를 확인하고 주문번호를 돌려준다"""
    if not ticket or ticket.count(".") != 1:
        raise TicketError("예측 실행권이 없습니다. ceoai.kr 에서 예측을 시작해 주세요.")
    payload, signature = ticket.split(".")
    if not hmac.compare_digest(_sign("ticket." + payload, secret), signature):
        raise TicketError("예측 실행권이 올바르지 않습니다.")
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TicketError("예측 실행권이 올바르지 않습니다.") from exc
    oid = str(claims.get("oid", ""))
    if claims.get("v") != 1 or not _OID_RE.match(oid):
        raise TicketError("예측 실행권이 올바르지 않습니다.")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now_ms:
        raise TicketError("예측 실행권이 만료되었습니다. ceoai.kr 에서 다시 시작해 주세요.")
    return oid


def consume_ticket(oid: str, project_id: str, tickets_dir: Optional[str] = None) -> None:
    """실행권을 한 번만 쓰게 표시한다（파일 생성의 원자성으로 동시 요청도 하나만 통과）"""
    directory = tickets_dir or _TICKETS_DIR
    os.makedirs(directory, exist_ok=True)
    try:
        fd = os.open(os.path.join(directory, f"{oid}.json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise TicketError("이미 사용한 예측 실행권입니다. ceoai.kr 에서 새로 시작해 주세요.") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"oid": oid, "project_id": project_id, "used_at": time.time()}, f)


def _post_callback(oid: str, action: str, reason: str) -> bool:
    body = {"oid": oid, "action": action, "reason": reason[:80]}
    headers = {"X-Predictor-Signature": _sign(f"{action}.{oid}")}
    delay = 2.0
    for attempt in range(4):
        try:
            response = httpx.post(Config.PREDICTOR_CALLBACK_URL, json=body, headers=headers, timeout=15)
            if response.status_code < 500:
                if response.status_code != 200:
                    logger.error("예측 콜백 거절: %s %s → HTTP %s", action, oid, response.status_code)
                return response.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("예측 콜백 실패(%s회): %s %s: %s", attempt + 1, action, oid, exc)
        time.sleep(delay)
        delay *= 2
    logger.error("예측 콜백을 끝내 보내지 못했습니다: %s %s", action, oid)
    return False


def notify_order(oid: Optional[str], action: str, reason: str = "") -> None:
    """
    complete（완료）·refund（실패 환불）를 ceoai.kr 에 알린다. 주문번호가 없으면（개발 모드）아무것도 안 한다.
    ceoai.kr 쪽이 같은 알림을 한 번만 반영하므로 여러 번 보내도 안전하다. 요청 스레드를 막지 않게 뒤에서 보낸다.
    """
    if not oid or not ticket_required() or action not in ("complete", "refund"):
        return
    threading.Thread(target=_post_callback, args=(oid, action, reason), daemon=True).start()
