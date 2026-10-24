import base64
import hashlib
import hmac
import io
import json

import pytest

from app import create_app
from app.api import graph as graph_api
from app.config import Config
from app.models.project import ProjectManager
from app.utils import predictor_ticket
from app.utils.predictor_ticket import TicketError, consume_ticket, verify_ticket

OID = "0b5a9e1c-3f7d-4c2a-9e8b-1d2c3b4a5f60"
# ceoai.kr functions/_predictor.js 의 makeTicket('cross-check-secret', OID, 2100-01-01 - 1일)이 만든 값.
# 두 저장소의 형식이 어긋나면 이 시험이 먼저 깨진다.
JS_TICKET = (
    "eyJ2IjoxLCJvaWQiOiIwYjVhOWUxYy0zZjdkLTRjMmEtOWU4Yi0xZDJjM2I0YTVmNjAiLCJleHAiOjQxMDI0NDQ4MDAwMDB9"
    ".9af3079049e8d225b444c8a1eeeff18aada075d7f9a04f452c19e6989c255dc4"
)
JS_REFUND_SIGNATURE = "7300b98c49e7f191c05b1bbe399c481a837b097b6eab30401af97478b9fc6b5a"


def _ticket(secret, claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), f"ticket.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def test_ticket_issued_by_ceoai_verifies_and_callback_signature_matches():
    assert verify_ticket(JS_TICKET, now_ms=1_700_000_000_000, secret="cross-check-secret") == OID
    assert predictor_ticket._sign(f"refund.{OID}", "cross-check-secret") == JS_REFUND_SIGNATURE


@pytest.mark.parametrize("ticket", [
    "",
    "no-dot",
    JS_TICKET[:-1] + "0",                                  # 서명 위조
    _ticket("other-secret", {"v": 1, "oid": OID, "exp": 9e15}),
    _ticket("cross-check-secret", {"v": 2, "oid": OID, "exp": 9e15}),
    _ticket("cross-check-secret", {"v": 1, "oid": "nope", "exp": 9e15}),
    _ticket("cross-check-secret", {"v": 1, "oid": OID, "exp": 1}),  # 만료
])
def test_bad_tickets_are_rejected(ticket):
    with pytest.raises(TicketError):
        verify_ticket(ticket, now_ms=1_700_000_000_000, secret="cross-check-secret")


def test_ticket_can_be_consumed_only_once(tmp_path):
    consume_ticket(OID, "proj_1", tickets_dir=str(tmp_path))
    with pytest.raises(TicketError):
        consume_ticket(OID, "proj_2", tickets_dir=str(tmp_path))


def _post(client, ticket=None):
    data = {
        "simulation_requirement": "출시 반응을 예측하라.",
        "files": (io.BytesIO(b"A short source document."), "source.md"),
    }
    if ticket is not None:
        data["ticket"] = ticket
    return client.post("/api/graph/ontology/generate", data=data, content_type="multipart/form-data")


@pytest.fixture
def gated_app(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PREDICTOR_TICKET_SECRET", "cross-check-secret")
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(predictor_ticket, "_TICKETS_DIR", str(tmp_path / "tickets"))
    sent = []
    monkeypatch.setattr(predictor_ticket, "notify_order", lambda oid, action, reason="": sent.append((oid, action)))
    monkeypatch.setattr(graph_api, "notify_order", lambda oid, action, reason="": sent.append((oid, action)))
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client(), sent


def test_project_creation_requires_a_paid_ticket(gated_app):
    client, _ = gated_app
    response = _post(client)
    assert response.status_code == 402
    assert response.json["need"] == "ticket"
    assert "ceoai.kr" in response.json["error"]


def test_ticket_is_spent_once_and_failed_ontology_is_refunded(gated_app, monkeypatch):
    client, sent = gated_app

    class FailingGenerator:
        def generate(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)
    first = _post(client, JS_TICKET)
    assert first.status_code == 500
    project = ProjectManager.get_project(first.json["data"]["project_id"])
    assert project.order_id == OID
    assert sent == [(OID, "refund")]

    again = _post(client, JS_TICKET)
    assert again.status_code == 409
    assert "이미 사용한" in again.json["error"]
    assert sent == [(OID, "refund")]


def test_without_secret_engine_stays_open_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(Config, "PREDICTOR_TICKET_SECRET", "")
    calls = []
    monkeypatch.setattr(predictor_ticket.threading, "Thread", lambda **kwargs: calls.append(kwargs))
    predictor_ticket.notify_order(OID, "refund")
    assert calls == []
    assert predictor_ticket.ticket_required() is False
