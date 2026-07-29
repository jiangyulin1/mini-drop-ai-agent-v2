"""任务状态机单元测试。

覆盖：
- 初始迁移（None → PENDING）
- 完整正向链路（PENDING → RUNNING → UPLOADING → ANALYZING → DONE）
- 每个中间状态的失败路径
- reason 为空/纯空白的拒绝
- 从终态迁移的拒绝
- 非法跳级的拒绝
- is_terminal 判定
"""

import pytest

from server.app.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    Actor,
    StatusEvent,
    TaskStatus,
    build_status_event,
    is_terminal,
    validate_transition,
)


class TestValidateTransition:
    """validate_transition() 的参数校验和路径合法性。"""

    def test_initial_transition_requires_reason(self):
        """初次迁移（None → PENDING），reason 不能为空。"""
        with pytest.raises(ValueError, match="reason"):
            validate_transition(None, TaskStatus.PENDING, "")

    def test_accepts_valid_initial_transition(self):
        """None → PENDING 是合法的初次迁移。"""
        validate_transition(None, TaskStatus.PENDING, "任务由 Web 请求创建")

    def test_rejects_whitespace_reason(self):
        """纯空白 reason 等同于空。"""
        with pytest.raises(ValueError, match="reason"):
            validate_transition(TaskStatus.PENDING, TaskStatus.RUNNING, "   ")

    def test_rejects_none_reason(self):
        """显式传入 None 也应拒绝。"""
        with pytest.raises(ValueError, match="reason"):
            validate_transition(TaskStatus.PENDING, TaskStatus.RUNNING, None)

    def test_rejects_illegal_skip(self):
        """不允许跳过中间状态（PENDING → DONE 不合法）。"""
        with pytest.raises(ValueError, match="非法的状态迁移"):
            validate_transition(TaskStatus.PENDING, TaskStatus.DONE, "直接完成")

    def test_rejects_transition_from_terminal_done(self):
        """DONE 是终态，不允许再迁移。"""
        with pytest.raises(ValueError, match="非法的状态迁移"):
            validate_transition(TaskStatus.DONE, TaskStatus.RUNNING, "重跑已完成任务")

    def test_rejects_transition_from_terminal_failed(self):
        """FAILED 也是终态。"""
        with pytest.raises(ValueError, match="非法的状态迁移"):
            validate_transition(TaskStatus.FAILED, TaskStatus.PENDING, "重试失败任务")

    def test_each_active_state_can_fail(self):
        """PENDING / RUNNING / UPLOADING / ANALYZING 都可以直接走向 FAILED。"""
        active_states = [
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.UPLOADING,
            TaskStatus.ANALYZING,
        ]
        for state in active_states:
            validate_transition(state, TaskStatus.FAILED, f"{state.value} 阶段发生错误")

    def test_each_active_state_can_be_cancelled(self):
        for state in [
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.UPLOADING,
            TaskStatus.ANALYZING,
        ]:
            validate_transition(state, TaskStatus.CANCELLED, "用户取消")


class TestBuildStatusEvent:
    """build_status_event() 构造完整事件对象。"""

    def test_creates_event_with_all_fields(self):
        """校验通过后返回的 StatusEvent 各字段值与输入一致。"""
        event = build_status_event(
            task_id="task_001",
            from_status=None,
            to_status=TaskStatus.PENDING,
            reason="Web 创建任务",
            actor=Actor.WEB,
            metadata={"target_pid": 1234},
        )

        assert event.task_id == "task_001"
        assert event.from_status is None
        assert event.to_status == TaskStatus.PENDING
        assert event.reason == "Web 创建任务"
        assert event.actor == Actor.WEB
        assert event.metadata == {"target_pid": 1234}
        assert event.created_at is not None

    def test_full_happy_path(self):
        """完整正向链路：PENDING → RUNNING → UPLOADING → ANALYZING → DONE。"""
        events: list[StatusEvent] = []

        events.append(
            build_status_event(
                "task_happy",
                None,
                TaskStatus.PENDING,
                "Web 创建任务",
                Actor.WEB,
            )
        )
        events.append(
            build_status_event(
                "task_happy",
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                "Agent 心跳拉取任务",
                Actor.SERVER,
            )
        )
        events.append(
            build_status_event(
                "task_happy",
                TaskStatus.RUNNING,
                TaskStatus.UPLOADING,
                "采集完成，准备上传产物",
                Actor.AGENT,
            )
        )
        events.append(
            build_status_event(
                "task_happy",
                TaskStatus.UPLOADING,
                TaskStatus.ANALYZING,
                "产物上传成功，等待分析",
                Actor.SERVER,
            )
        )
        events.append(
            build_status_event(
                "task_happy",
                TaskStatus.ANALYZING,
                TaskStatus.DONE,
                "分析完毕，火焰图已生成",
                Actor.ANALYZER,
            )
        )

        assert len(events) == 5
        assert events[-1].to_status == TaskStatus.DONE

    def test_failure_paths_leave_correct_status(self):
        """从 RUNNING 直接失败，事件记录的 to_status 应为 FAILED。"""
        event = build_status_event(
            "task_fail",
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            "目标 PID 不存在",
            Actor.AGENT,
        )
        assert event.to_status == TaskStatus.FAILED
        assert event.reason == "目标 PID 不存在"

    def test_reason_is_stripped(self):
        """reason 头尾空白应被 strip。"""
        event = build_status_event(
            "task_trim",
            None,
            TaskStatus.PENDING,
            "   创建 by Web   ",
            Actor.WEB,
        )
        assert event.reason == "创建 by Web"


class TestAllowedTransitions:
    """ALLOWED_TRANSITIONS 表的结构正确性。"""

    def test_terminal_states_have_no_exits(self):
        """所有终态的目标集必须为空。"""
        assert ALLOWED_TRANSITIONS[TaskStatus.DONE] == set()
        assert ALLOWED_TRANSITIONS[TaskStatus.FAILED] == set()
        assert ALLOWED_TRANSITIONS[TaskStatus.CANCELLED] == set()

    def test_active_states_have_at_least_one_exit(self):
        """每个活跃状态至少有 FAILED 出口。"""
        for state in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING]:
            assert TaskStatus.FAILED in ALLOWED_TRANSITIONS[state], f"{state} 缺少 FAILED 出口"

    def test_null_entry_only_goes_to_pending(self):
        """None（初始创建）唯一合法目标是 PENDING。"""
        assert ALLOWED_TRANSITIONS[None] == {TaskStatus.PENDING}


class TestTerminalCheck:
    """is_terminal() 辅助函数。"""

    def test_done_and_failed_are_terminal(self):
        assert is_terminal(TaskStatus.DONE) is True
        assert is_terminal(TaskStatus.FAILED) is True
        assert is_terminal(TaskStatus.CANCELLED) is True

    def test_active_states_are_not_terminal(self):
        for state in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING]:
            assert is_terminal(state) is False
