import sys, tempfile, time
sys.path.insert(0, "/app/.proposals/087baea9")
import telegram_outbox as to

with tempfile.TemporaryDirectory() as td:
    ob = to.TelegramOutbox(td, clock=lambda: time.time())
    a = ob.prepare_text("call-key-1", peer_id=-1001240718803, text="одна и та же реплика",
                        run_id="run-X", call_id="c1", purpose="tool:reply")
    b = ob.prepare_text("call-key-2", peer_id=-1001240718803, text="одна и та же реплика",
                        run_id="run-X", call_id="c2", purpose="tool:reply")
    assert a["key"] == b["key"], (a["key"], b["key"])
    assert b["state"] in {"pending", "accepted"}
    # другой текст — отдельный интент
    c = ob.prepare_text("call-key-3", peer_id=-1001240718803, text="другая реплика",
                        run_id="run-X", call_id="c3", purpose="tool:reply")
    assert c["key"] == "call-key-3"
    # другой run — тоже отдельный
    d = ob.prepare_text("call-key-4", peer_id=-1001240718803, text="одна и та же реплика",
                        run_id="run-Y", call_id="c4", purpose="tool:reply")
    assert d["key"] == "call-key-4"
    # тот же call_id повторно — идемпотентность не сломана
    e = ob.prepare_text("call-key-1", peer_id=-1001240718803, text="одна и та же реплика",
                        run_id="run-X", call_id="c1", purpose="tool:reply")
    assert e["key"] == "call-key-1"
    # другая тема — отдельный интент
    f = ob.prepare_text("call-key-5", peer_id=-1001240718803, topic_id=96709,
                        text="одна и та же реплика",
                        run_id="run-X", call_id="c5", purpose="tool:reply")
    assert f["key"] == "call-key-5"
    print("test_turn_dedupe: OK")
