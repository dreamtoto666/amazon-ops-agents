"""Guard the SSE stage contract between the controller graph and the console.

The console validates every SSE event with a strict ``z.enum(STAGE_NAMES)``, so a
stage the backend emits but the frontend omits is not merely unrendered: the
event fails validation and the run stream switches to its error state.  Nothing
enforced that sync, so this test reads the real frontend sources and asserts the
contract directly.
"""

from __future__ import annotations

import re
from pathlib import Path

from amazon_ops.events import StageName


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_RUN_TYPES = REPO_ROOT / "frontend/src/features/agent-runs/api/types.ts"
RUN_STREAM_HOOK = REPO_ROOT / "frontend/src/features/agent-runs/hooks/use-run-stream.ts"
CONTROLLER_GRAPH = REPO_ROOT / "src/amazon_ops/graph.py"


def _array_items(source: str, marker: str) -> list[str]:
    """Return the quoted items of an array literal that starts at ``marker``."""

    start = source.index(marker) + len(marker)
    end = source.index("]", start)
    return re.findall(r"'([^']+)'", source[start:end])


def _object_keys(source: str, marker: str) -> list[str]:
    """Return the top-level keys of an object literal that starts at ``marker``."""

    start = source.index(marker) + len(marker)
    end = source.index("}", start)
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", source[start:end], re.MULTILINE)


def frontend_stage_names() -> list[str]:
    return _array_items(
        AGENT_RUN_TYPES.read_text(encoding="utf-8"), "export const STAGE_NAMES = ["
    )


def frontend_stage_order() -> list[str]:
    return _array_items(
        RUN_STREAM_HOOK.read_text(encoding="utf-8"), "const STAGE_ORDER: StageName[] = ["
    )


def frontend_stage_titles() -> list[str]:
    return _object_keys(
        RUN_STREAM_HOOK.read_text(encoding="utf-8"),
        "const STAGE_TITLES: Record<StageName, string> = {",
    )


def controller_graph_stages() -> set[str]:
    """Every stage the controller graph can emit, read from its own source."""

    members = set(re.findall(r"StageName\.([A-Z_]+)", CONTROLLER_GRAPH.read_text(encoding="utf-8")))
    assert members, "the controller graph no longer references StageName directly"
    return {StageName[member].value for member in members}


def test_parent_directories_resolve_to_the_real_frontend_sources():
    for path in (AGENT_RUN_TYPES, RUN_STREAM_HOOK, CONTROLLER_GRAPH):
        assert path.is_file(), f"contract test cannot find {path}"


def test_frontend_accepts_every_stage_the_controller_graph_emits():
    """A missing stage breaks the run stream, so this must never drift."""

    missing = sorted(controller_graph_stages() - set(frontend_stage_names()))

    assert missing == [], (
        f"前端 STAGE_NAMES 缺少控制器图会发出的 stage: {missing}；"
        "它们会被 z.enum 拒绝并让前端进入错误态"
    )


def test_every_accepted_stage_has_a_title():
    missing = sorted(set(frontend_stage_names()) - set(frontend_stage_titles()))

    assert missing == [], f"STAGE_TITLES 缺少表项，会渲染成 undefined: {missing}"


def test_progress_order_only_references_declared_stages():
    """STAGE_ORDER is the linear timeline; waiting stages render from connection state."""

    unknown = sorted(set(frontend_stage_order()) - set(frontend_stage_names()))

    assert unknown == [], f"STAGE_ORDER 引用了未声明的 stage: {unknown}"
    assert set(frontend_stage_order()) <= set(frontend_stage_names())


def test_stage_lists_are_internally_consistent():
    names = frontend_stage_names()

    assert len(names) == len(set(names)), "STAGE_NAMES 存在重复项"
    assert set(frontend_stage_titles()) == set(names), "STAGE_TITLES 与 STAGE_NAMES 不一致"
    order = frontend_stage_order()
    assert order == [name for name in names if name in set(order)], (
        "STAGE_ORDER 的顺序与 STAGE_NAMES 不一致"
    )


def test_titles_are_not_blank():
    source = RUN_STREAM_HOOK.read_text(encoding="utf-8")
    start = source.index("const STAGE_TITLES: Record<StageName, string> = {")

    assert not re.search(r":\s*''", source[start:]), "STAGE_TITLES 存在空标题"


def test_every_stage_hook_call_passes_the_required_arguments():
    """静态守卫：阶段 hook 多在难以覆盖的错误路径里调用。

    回归背景：报告节点的 ``except`` 分支调用 ``stages.fail`` 时少传了 ``stage``，
    于是「优雅降级」路径自己抛 TypeError，把一次可恢复的失败变成整个 run 失败。
    行为测试只能覆盖被走过的路径，这里对所有调用点做静态检查。
    """

    import ast
    import inspect

    from amazon_ops.events import StageController

    required = {
        hook: len(
            [
                parameter
                for parameter in inspect.signature(getattr(StageController, hook)).parameters.values()
                if parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                and parameter.name != "self"
            ]
        )
        for hook in ("start", "progress", "complete", "fail")
    }

    offenders: list[str] = []
    sources = list((REPO_ROOT / "src/amazon_ops").rglob("*.py"))
    assert sources, "找不到后端源码，此守卫需要重新审视"
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            hook = node.func.attr
            if hook not in required or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "stages":
                continue
            if len(node.args) != required[hook]:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} stages.{hook}() "
                    f"传了 {len(node.args)} 个位置参数，需要 {required[hook]}"
                )

    assert offenders == [], "阶段 hook 调用参数个数不正确：" + "；".join(offenders)
