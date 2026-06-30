"""统一长任务进度展示。

设计目标（为何这样做）：
- 短任务静默：任务在「浮现阈值」(reveal_after) 内完成则全程不打印，避免秒级操作刷屏。
- 长任务自动浮现：运行超过阈值仍未结束，自动打印起始行并按档位刷新
  「已完成 / 已用 / 预计剩余」。
- 大活儿提前告知：若进入循环时按数量×单项经验耗时预估就明显是大任务
  （超过 eager_est_sec），则立即打印起始行，不必等到浮现阈值。
- 线程安全：并发循环可从多线程调用 advance()。

为什么不是「死等满 30 秒才显示」：那样长任务前 30 秒仍是黑屏，用户照样会
误以为卡死。取较小的浮现阈值（默认 5s）即可覆盖「任何耗时任务」，又不打扰
秒级短任务——这正是「超过 30s 的任务都要有进度」诉求的更优实现。

用法 1（串行循环，最常用）：
    from ..utils import track
    for a in track(assets, label="图片识别", est_per_item=2.5):
        do_heavy(a)

用法 2（并发循环 / 需手动推进）：
    from ..utils import ProgressTracker
    tr = ProgressTracker(len(imgs), label="图片识别", est_per_item=2.5)
    with ThreadPoolExecutor(...) as ex:
        futs = [ex.submit(work, a) for a in imgs]
        for _ in as_completed(futs):
            tr.advance()
    tr.finish()
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")

# 全局开关：客户端/CLI 默认开启；测试或被其他程序调用时可置 False 静默。
_ENABLED = True


def set_enabled(flag: bool) -> None:
    """全局开关进度输出（如单元测试想静默时调用 set_enabled(False)）。"""
    global _ENABLED
    _ENABLED = bool(flag)


def fmt_dur(sec: float) -> str:
    """把秒数格式化成人类可读时长（中文）。"""
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec} 秒"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} 分 {s} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


class ProgressTracker:
    """通用长任务进度器：惰性浮现 + 实时剩余估算 + 线程安全。

    参数：
        total:         总项数（已知）。<=0 时进度器自动禁用。
        label:         任务名（如「图片识别」「整理落盘」）。
        est_per_item:  单项经验耗时（秒），仅用于浮现行的量级预估与「大活儿提前告知」。
        reveal_after:  运行超过该秒数仍未结束则浮现进度（默认 5s）。
        eager_est_sec: 进入时预估总耗时≥该值则立即浮现（默认 10s）。
        enabled:       局部开关；与全局开关同时为真才输出。
    """

    def __init__(
        self,
        total: int,
        label: str = "处理中",
        *,
        est_per_item: Optional[float] = None,
        reveal_after: float = 5.0,
        eager_est_sec: float = 10.0,
        enabled: bool = True,
    ) -> None:
        self.total = max(0, int(total))
        self.label = label
        self.est_per_item = est_per_item
        self.reveal_after = reveal_after
        self.eager_est_sec = eager_est_sec
        self.enabled = enabled and _ENABLED and self.total > 0
        self._done = 0
        self._t0 = time.time()
        self._revealed = False
        self._lock = threading.Lock()
        self._step = max(1, self.total // 20)  # ~5% 一档

        # 大活儿提前告知：按数量预估就明显耗时 → 立即显示起始行。
        if self.enabled and est_per_item and est_per_item * self.total >= eager_est_sec:
            self._reveal(eager=True)

    # ---- 内部 ----
    def _emit(self, msg: str) -> None:
        try:
            print(msg, file=sys.stdout, flush=True)
        except Exception:  # noqa: BLE001 输出失败绝不影响主流程
            pass

    def _reveal(self, eager: bool = False) -> None:
        self._revealed = True
        if self.est_per_item:
            est = self.est_per_item * self.total
            tail = "，开始处理…" if eager else "，处理中…"
            self._emit(f"⏳ {self.label}：共 {self.total} 项，预计约 {fmt_dur(est)}{tail}")
        else:
            self._emit(f"⏳ {self.label}：共 {self.total} 项，处理中…")

    # ---- 对外 ----
    def advance(self, n: int = 1) -> None:
        """完成 n 项后调用。线程安全。"""
        if not self.enabled:
            return
        with self._lock:
            self._done += n
            done = self._done
            el = time.time() - self._t0
            if not self._revealed:
                # 浮现条件：运行超过阈值且尚未全部完成。
                if el >= self.reveal_after and done < self.total:
                    self._reveal()
                else:
                    return
            # 已浮现：按 ~5% 档位刷新（最后一项必刷）。
            if done % self._step and done != self.total:
                return
            rate = done / el if el > 0 else 0
            remain = (self.total - done) / rate if rate > 0 else 0
            pct = done * 100 // self.total
            self._emit(
                f"   …{done}/{self.total} ({pct}%)  已用 {fmt_dur(el)}"
                f"  预计剩余 {fmt_dur(remain)}"
            )

    def finish(self) -> None:
        """收尾：仅当曾经浮现过才打印完成汇总（短任务保持静默）。"""
        if not self.enabled:
            return
        with self._lock:
            if self._revealed:
                el = time.time() - self._t0
                self._emit(f"✅ {self.label}完成：{self.total} 项，用时 {fmt_dur(el)}")


def track(
    iterable: Iterable[T],
    total: Optional[int] = None,
    label: str = "处理中",
    *,
    est_per_item: Optional[float] = None,
    reveal_after: float = 5.0,
    eager_est_sec: float = 10.0,
    enabled: bool = True,
) -> Iterator[T]:
    """串行循环进度糖：`for x in track(items, label=...)`。

    每产出一项后自动 advance；循环结束（含异常）自动 finish。
    total 缺省时尝试 len(iterable)，取不到则禁用进度。
    """
    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = 0
    tr = ProgressTracker(
        total, label,
        est_per_item=est_per_item,
        reveal_after=reveal_after,
        eager_est_sec=eager_est_sec,
        enabled=enabled,
    )
    try:
        for item in iterable:
            yield item
            tr.advance()
    finally:
        tr.finish()
