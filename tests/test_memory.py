from amazon_ops.memory import InMemoryConversationStore


def test_conversation_memory_is_isolated_and_bounded():
    memory = InMemoryConversationStore(max_messages=4)

    memory.append("a", role="user", content="第一条")
    memory.append("a", role="assistant", content="第二条")
    memory.append("a", role="user", content="第三条")
    memory.append("a", role="assistant", content="第四条")
    memory.append("a", role="user", content="第五条")
    memory.append("b", role="user", content="另一段对话")

    assert [item["content"] for item in memory.history("a")] == [
        "第二条",
        "第三条",
        "第四条",
        "第五条",
    ]
    assert memory.history("b") == [{"role": "user", "content": "另一段对话"}]


def test_conversation_memory_returns_a_copy_and_can_be_cleared():
    memory = InMemoryConversationStore()
    memory.append("a", role="user", content="原始内容")

    history = memory.history("a")
    history[0]["content"] = "被外部修改"
    memory.clear("a")

    assert memory.history("a") == []
