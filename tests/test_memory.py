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


def test_a_replayed_turn_does_not_record_the_answer_twice():
    """Recovering an interrupted run replays the node that was in flight."""

    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-1", role="user", content="看下利润", turn_id="run-1")
    memory.append("owner-a", "run-1", role="assistant", content="利润是 10%。", turn_id="run-1")
    # 恢复后重跑同一个 turn
    memory.append("owner-a", "run-1", role="assistant", content="利润是 10%。", turn_id="run-1")

    assert [item["content"] for item in memory.history("owner-a", "run-1")] == [
        "看下利润",
        "利润是 10%。",
    ]


def test_the_first_write_of_a_turn_wins():
    """A retry can produce different text; the answer already shown is kept."""

    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-2", role="assistant", content="第一次的回答", turn_id="run-2")
    memory.append("owner-a", "run-2", role="assistant", content="重试后不同的回答", turn_id="run-2")

    assert [item["content"] for item in memory.history("owner-a", "run-2")] == ["第一次的回答"]


def test_both_roles_of_one_turn_are_kept():
    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-3", role="user", content="问题", turn_id="run-3")
    memory.append("owner-a", "run-3", role="assistant", content="答案", turn_id="run-3")

    assert [item["content"] for item in memory.history("owner-a", "run-3")] == ["问题", "答案"]


def test_distinct_turns_are_all_kept():
    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-4", role="assistant", content="答案一", turn_id="run-4")
    memory.append("owner-a", "run-4", role="assistant", content="答案二", turn_id="run-5")

    assert [item["content"] for item in memory.history("owner-a", "run-4")] == ["答案一", "答案二"]


def test_writes_without_a_turn_id_are_not_deduplicated():
    """Without a turn id there is nothing that identifies a repeat."""

    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-6", role="assistant", content="同样的内容")
    memory.append("owner-a", "run-6", role="assistant", content="同样的内容")
    memory.append("owner-a", "run-6", role="assistant", content="同样的内容", turn_id="   ")

    assert len(memory.history("owner-a", "run-6")) == 3


def test_turn_deduplication_is_scoped_to_one_conversation_and_owner():
    memory = InMemoryConversationStore()

    memory.append("owner-a", "run-7", role="assistant", content="A 的答案", turn_id="shared-turn")
    memory.append("owner-a", "run-8", role="assistant", content="另一段对话的答案", turn_id="shared-turn")
    memory.append("owner-b", "run-7", role="assistant", content="B 的答案", turn_id="shared-turn")

    assert [item["content"] for item in memory.history("owner-a", "run-7")] == ["A 的答案"]
    assert [item["content"] for item in memory.history("owner-a", "run-8")] == ["另一段对话的答案"]
    assert [item["content"] for item in memory.history("owner-b", "run-7")] == ["B 的答案"]
