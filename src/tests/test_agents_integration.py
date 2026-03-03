import asyncio
import json
import pytest



def _require_fastapi():
    pytest.importorskip("fastapi")


def test_agents_process_endpoint_returns_expected_shape():
    _require_fastapi()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    payload = {"user_id": "u1", "message": "Where are my brakes?"}
    r = client.post("/api/v1/agents/process", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "conversation_id" in body
    assert "agent" in body
    assert "response" in body


def test_router_low_confidence_fallback(monkeypatch):
    # Patch BaseAgent.call_llm to return a low-confidence routing JSON
    from src.agents.ai_agents import RouterAgent

    async def fake_call(self, messages, **kw):
        return {"content": json.dumps({"agent": "parts_finder", "confidence": 0.4, "extracted_data": {}}), "model": "x", "tokens": 3}

    monkeypatch.setattr(RouterAgent, "call_llm", fake_call)

    router = RouterAgent()
    loop = asyncio.new_event_loop()
    routing = loop.run_until_complete(router.route("I need brakes", None))
    assert routing["agent"] == "service" or routing.get("reason")
