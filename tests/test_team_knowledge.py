import io
import struct
import zipfile

import pytest

from amazon_ops.team_knowledge import (
    InMemoryKnowledgeStore,
    TeamKnowledgeError,
    TeamKnowledgeService,
    TeamKnowledgeUnavailable,
)


def vault_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def damaged_zip(files: dict[str, str]) -> bytes:
    """Declare an entry smaller than its data so reading it fails the CRC check."""

    raw = bytearray(vault_zip(files))
    central = raw.find(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 24, 64)
    return bytes(raw)


def encrypted_zip(files: dict[str, str]) -> bytes:
    """Advertise the encryption flag without actually encrypting anything."""

    raw = bytearray(vault_zip(files))
    central = raw.find(b"PK\x01\x02")
    raw[central + 8] |= 0x01
    return bytes(raw)


def test_damaged_archive_is_invalid_input_and_keeps_the_active_version(tmp_path):
    """A corrupt entry is the uploader's problem, not a service outage (422, not 503)."""

    service = TeamKnowledgeService(InMemoryKnowledgeStore(), tmp_path)
    active = service.upload(
        uploaded_by="admin-a",
        file_name="vault.zip",
        content=vault_zip({"店铺/规则.md": "# 广告规则\n广告预算按周复核。"}),
    )

    with pytest.raises(TeamKnowledgeError, match="损坏") as error:
        service.upload(
            uploaded_by="admin-a",
            file_name="vault.zip",
            content=damaged_zip({"a.md": "# A\n" + "内容" * 300}),
        )

    assert not isinstance(error.value, TeamKnowledgeUnavailable)
    assert service.status().version_id == active.version_id
    assert service.search("广告预算")[0].path == "店铺/规则.md"


def test_encrypted_archive_is_rejected_without_a_password_prompt(tmp_path):
    service = TeamKnowledgeService(InMemoryKnowledgeStore(), tmp_path)

    with pytest.raises(TeamKnowledgeError, match="加密") as error:
        service.upload(
            uploaded_by="admin-a",
            file_name="vault.zip",
            content=encrypted_zip({"a.md": "# A\n正文"}),
        )

    assert not isinstance(error.value, TeamKnowledgeUnavailable)


def test_team_vault_replaces_active_version_only_after_indexing(tmp_path):
    service = TeamKnowledgeService(InMemoryKnowledgeStore(), tmp_path)
    first = service.upload(
        uploaded_by="admin-a",
        file_name="vault.zip",
        content=vault_zip({"店铺/规则.md": "---\nshop: US\n---\n# 广告规则\n广告预算按周复核。", ".obsidian/app.json": "{}", "image.png": "x"}),
    )
    assert first.status == "active"
    assert first.document_count == 1
    assert service.search("广告预算")[0].path == "店铺/规则.md"

    with pytest.raises(TeamKnowledgeError):
        service.upload(uploaded_by="admin-b", file_name="bad.zip", content=vault_zip({"../bad.md": "bad"}))

    assert service.status().version_id == first.version_id
    assert service.search("广告预算")[0].path == "店铺/规则.md"


def test_team_vault_rejects_non_markdown_and_duplicate_paths(tmp_path):
    service = TeamKnowledgeService(InMemoryKnowledgeStore(), tmp_path)
    with pytest.raises(TeamKnowledgeError, match="没有可索引"):
        service.upload(uploaded_by="admin-a", file_name="vault.zip", content=vault_zip({"readme.txt": "x"}))
    with pytest.raises(TeamKnowledgeError, match="重复"):
        service.upload(uploaded_by="admin-a", file_name="vault.zip", content=vault_zip({"A.md": "one", "a.md": "two"}))


def test_wiki_retrieval_prefers_identifiers_and_supports_titles_tags_links_and_chinese_phrases(tmp_path):
    service = TeamKnowledgeService(InMemoryKnowledgeStore(), tmp_path)
    service.upload(
        uploaded_by="admin-a",
        file_name="vault.zip",
        content=vault_zip(
            {
                "店铺/北美旗舰店.md": "---\ntags:\n  - 北美\n  - 广告策略\n---\n# 北美旗舰店广告规则\n[[预算复核]]\nASIN B0WIKI12345 的预算每周复核。",
                "归档/旧广告.md": "# 旧广告\n这里也提到 B0WIKI12345，但不是当前规则。",
                "流程/预算复核.md": "# 预算复核\n广告预算复核的中文短语规则。",
            }
        ),
    )

    assert service.search("B0WIKI12345")[0].path == "店铺/北美旗舰店.md"
    assert service.search("北美旗舰店广告规则")[0].path == "店铺/北美旗舰店.md"
    assert service.search("广告策略")[0].path == "店铺/北美旗舰店.md"
    assert {item.path for item in service.search("预算复核")} >= {"店铺/北美旗舰店.md", "流程/预算复核.md"}
    assert service.search("中文短语规则")[0].path == "流程/预算复核.md"
