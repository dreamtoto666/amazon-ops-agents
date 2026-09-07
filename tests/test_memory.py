from amazon_ops.memory import InMemoryConversationStore
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
from uuid import uuid4


def test_conversation_memory_is_isolated():
    memory = InMemoryConversationStore()

    memory.append("owner-a", "a", role="user", content="第一条")
    memory.append("owner-a", "a", role="assistant", content="第二条")
    memory.append("owner-a", "a", role="user", content="第三条")
    memory.append("owner-a", "a", role="assistant", content="第四条")
    memory.append("owner-a", "a", role="user", content="第五条")
    memory.append("owner-a", "b", role="user", content="另一段对话")

    assert [item["content"] for item in memory.history("owner-a", "a")] == [
        "第一条",
        "第二条",
        "第三条",
        "第四条",
        "第五条",
    ]
    assert memory.history("owner-a", "b") == [{"role": "user", "content": "另一段对话", "kind": "message"}]


def test_conversation_memory_is_isolated_between_owners_with_the_same_conversation_id():
    memory = InMemoryConversationStore()

    memory.append("owner-a", "shared", role="user", content="A 的信息")
    memory.append("owner-b", "shared", role="user", content="B 的信息")

    assert memory.history("owner-a", "shared") == [{"role": "user", "content": "A 的信息", "kind": "message"}]
    assert memory.history("owner-b", "shared") == [{"role": "user", "content": "B 的信息", "kind": "message"}]
    assert memory.conversations("owner-a") == [
        {"conversation_id": "shared", "preview": "A 的信息"}
    ]


def test_conversation_memory_accepts_postgresql_uuid_owner_ids():
    memory = InMemoryConversationStore()
    owner_id = uuid4()

    memory.append(owner_id, "conversation-a", role="user", content="UUID 用户")

    assert memory.history(owner_id, "conversation-a") == [
        {"role": "user", "content": "UUID 用户", "kind": "message"}
    ]


def test_conversation_memory_returns_a_copy_and_can_be_cleared():
    memory = InMemoryConversationStore()
    memory.append("owner-a", "a", role="user", content="原始内容")

    history = memory.history("owner-a", "a")
    history[0]["content"] = "被外部修改"
    memory.clear("owner-a", "a")

    assert memory.history("owner-a", "a") == []


def _add_completed_turns(memory: InMemoryConversationStore, count: int) -> None:
    for index in range(count):
        turn_id = f"turn-{index}"
        memory.append("owner-a", "conversation-a", role="user", content=f"问题 {index}", turn_id=turn_id)
        memory.append("owner-a", "conversation-a", role="assistant", content=f"回复 {index}", turn_id=turn_id)


def test_compacts_oldest_complete_turns_and_preserves_raw_archive():
    memory = InMemoryConversationStore(max_turns=3, compact_turns=2)
    _add_completed_turns(memory, 3)

    assert memory.compact("owner-a", "conversation-a", lambda previous, messages: "压缩摘要")

    history = memory.history("owner-a", "conversation-a")
    assert history[0] == {"role": "assistant", "content": "压缩摘要", "kind": "summary"}
    assert [item["content"] for item in history[1:]] == ["问题 2", "回复 2"]
    assert [item["content"] for item in memory._messages[("owner-a", "conversation-a")]] == [
        "问题 0", "回复 0", "问题 1", "回复 1", "问题 2", "回复 2"
    ]


def test_does_not_compact_incomplete_turns_and_uses_fallback_on_summary_failure():
    memory = InMemoryConversationStore(max_turns=3, compact_turns=2)
    _add_completed_turns(memory, 2)
    memory.append("owner-a", "conversation-a", role="user", content="尚未回复", turn_id="turn-2")
    assert not memory.compact("owner-a", "conversation-a", lambda previous, messages: "不会调用")

    memory.append("owner-a", "conversation-a", role="assistant", content="完成回复", turn_id="turn-2")
    assert memory.compact("owner-a", "conversation-a", lambda previous, messages: (_ for _ in ()).throw(RuntimeError("LLM failed")))
    assert "保底摘要" in memory.history("owner-a", "conversation-a")[0]["content"]


def test_only_one_in_memory_worker_can_compact_the_same_turn_range():
    memory = InMemoryConversationStore(max_turns=3, compact_turns=2)
    _add_completed_turns(memory, 3)
    started = Event()
    calls = 0

    def summarize(previous, messages):
        nonlocal calls
        calls += 1
        started.set()
        sleep(0.05)
        return "摘要"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(memory.compact, "owner-a", "conversation-a", summarize)
        started.wait(timeout=1)
        second = executor.submit(memory.compact, "owner-a", "conversation-a", summarize)
        assert sorted([first.result(), second.result()]) == [False, True]
    assert calls == 1
