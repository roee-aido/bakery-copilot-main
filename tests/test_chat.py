"""Integration tests: the API with the model replaced by a scripted fake."""
from app import agent as agent_module
from app.agent import ModelEvent, run_turn
from app.costs import Usage
from app.db import Conversation, Message, Run, SessionLocal, User
from tests.conftest import read_sse


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_required_and_wrong_password(client):
    client.cookies.clear()
    assert client.get("/api/conversations").status_code == 401
    bad = client.post("/api/login", json={"name": "Eve", "password": "nope"})
    assert bad.status_code == 401
    assert client.post("/api/login", json={"name": "E", "password": "test-pass"}).status_code in (400, 422)


def test_chat_streams_text_and_saves_once(client, db, logged_in, fake):
    fake.turns.append([
        ModelEvent(type="text", text="The Great Pyramid is dated "),
        ModelEvent(type="text", text="to an estimated 2700-2500 BCE."),
        ModelEvent(type="stop", stop_reason="end_turn", usage=Usage(120, 18),
                   content=[{"type": "text", "text": "The Great Pyramid is dated to an estimated 2700-2500 BCE."}]),
    ])
    r = client.post("/api/chat", json={"message": "When was the Great Pyramid built?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = read_sse(r)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "The Great Pyramid is dated to an estimated 2700-2500 BCE."
    conv_id = events[0]["conversation_id"]
    msgs = db.query(Message).filter_by(conversation_id=conv_id).order_by(Message.id).all()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "The Great Pyramid is dated to an estimated 2700-2500 BCE."
    run = db.query(Run).filter_by(conversation_id=conv_id).one()
    assert run.status == "ok" and run.input_tokens == 120 and run.output_tokens == 18
    assert events[-1]["cost_usd"] == 0.0  # fake model has no price table


def test_tool_loop_runs_the_site_tool_and_sums_both_model_calls(client, db, logged_in, fake):
    tool_turn = [
        ModelEvent(type="text", text="Checking the records. "),
        ModelEvent(type="stop", stop_reason="tool_use", usage=Usage(300, 40), content=[
            {"type": "text", "text": "Checking the records. "},
            {"type": "tool_use", "id": "tu_1", "name": "site_timeline", "input": {"site": "Great Pyramid of Giza"}},
        ]),
    ]
    final_turn = [
        ModelEvent(type="text", text="Estimated 2700-2500 BCE, with a competing radiocarbon dating."),
        ModelEvent(type="stop", stop_reason="end_turn", usage=Usage(420, 30),
                   content=[{"type": "text", "text": "Estimated 2700-2500 BCE, with a competing radiocarbon dating."}]),
    ]
    fake.turns.extend([tool_turn, final_turn])

    events = read_sse(client.post("/api/chat", json={"message": "When was the Great Pyramid built?"}))
    tools = [e for e in events if e["type"] == "tool"]
    assert [t["status"] for t in tools] == ["start", "done"] and tools[0]["name"] == "site_timeline"
    assert "Great Pyramid of Giza" in tools[1]["summary"] and "dating claims" in tools[1]["summary"]
    run = db.query(Run).filter_by(conversation_id=events[0]["conversation_id"]).one()
    assert run.tool_calls == 1 and run.input_tokens == 720  # both model calls of the turn are summed


def test_tool_loop_reports_a_site_that_is_not_in_the_records(client, db, logged_in, fake):
    fake.turns.extend([
        [ModelEvent(type="stop", stop_reason="tool_use", usage=Usage(100, 10), content=[
            {"type": "tool_use", "id": "tu_9", "name": "site_timeline", "input": {"site": "Atlantis"}}])],
        [ModelEvent(type="text", text="Atlantis is not in our records."),
         ModelEvent(type="stop", stop_reason="end_turn", usage=Usage(150, 10),
                    content=[{"type": "text", "text": "Atlantis is not in our records."}])],
    ])
    events = read_sse(client.post("/api/chat", json={"message": "Take me to Atlantis"}))
    done = [e for e in events if e["type"] == "tool" and e["status"] == "done"]
    assert done and "not found" in done[0]["summary"]


def test_refusal_is_reported_not_hidden(client, db, logged_in, fake):
    fake.turns.append([ModelEvent(type="stop", stop_reason="refusal", usage=Usage(50, 0), content=[])])
    events = read_sse(client.post("/api/chat", json={"message": "do something odd"}))
    assert any(e["type"] == "text" and "can't help" in e["text"] for e in events)
    conv_id = events[0]["conversation_id"]
    assert db.query(Run).filter_by(conversation_id=conv_id).one().status == "refusal"


def test_dropped_connection_leaves_no_half_row(client, db, logged_in, fake):
    fake.turns.append([
        ModelEvent(type="text", text="This reply will be cut "),
        ModelEvent(type="text", text="in the middle."),
        ModelEvent(type="stop", stop_reason="end_turn", usage=Usage(10, 5), content=[{"type": "text", "text": "x"}]),
    ])
    with SessionLocal() as s:
        user = s.query(User).filter_by(name="Dana").one()
        gen = run_turn(s, user=user, conversation=None, text="cut me off", image=None, client=fake)
        meta = next(gen)
        first = next(gen)
        assert meta["type"] == "meta" and first["type"] == "text"
        gen.close()  # the browser went away
        conv_id = meta["conversation_id"]
    roles = [m.role for m in db.query(Message).filter_by(conversation_id=conv_id).all()]
    assert roles == ["user"]  # the user's message is kept, no partial assistant row
    run = db.query(Run).filter_by(conversation_id=conv_id).one()
    assert run.status == "aborted"


def test_image_message_carries_a_reference(client, db, logged_in, fake):
    import base64

    fake.turns.append([ModelEvent(type="stop", stop_reason="end_turn", usage=Usage(10, 2),
                                  content=[{"type": "text", "text": "Got the image."}])])
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    events = read_sse(client.post("/api/chat", json={"message": "", "image": {"media_type": "image/png", "data": png}}))
    assert events[-1]["type"] == "done"
    sent = fake.last_messages[-1]["content"]
    assert sent[0]["type"] == "image" and sent[0]["source"]["media_type"] == "image/png"
    assert "image reference: img_" in sent[1]["text"]
    conv_id = events[0]["conversation_id"]
    user_msg = db.query(Message).filter_by(conversation_id=conv_id, role="user").one()
    assert user_msg.has_image is True and "[image img_" in user_msg.content


def test_digest_endpoint_needs_secret(client, logged_in):
    assert client.post("/internal/digest").status_code == 401
    r = client.post("/internal/digest", headers={"X-Cron-Secret": "cron-test"})
    assert r.status_code == 200
    assert r.json()["text"].startswith("GeoChronos Time-Travel Concierge —")
    assert r.json()["sent"] is False
