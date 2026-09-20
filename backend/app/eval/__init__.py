"""四把尺子：评测基线（M5），先量出 v0 的问题数字。

- judge.py       比价黄金集（规则判定最优解命中率）
- clarify.py     对话澄清集（收敛到可推荐的澄清轮次）
- token_profiler.py  全量 vs 裁剪 token 基线（M7 填裁剪版）
- chaos.py       故障注入（单源 100% 失败，测其余源出结果率 + P95）
"""