# 交付范围与脱敏说明

本目录是提交到 GitHub 的公开精简包，不是完整实验主机镜像。

## 纳入

- 9 个公开测试案例及回放证据；
- 专家干预定义；
- 统一 Agent 契约、适配器、运行入口、评分器和审计器；
- 完整流程提示词；
- 当前结果汇总、赛道对比、运行哈希和 Agent 摘要。

## 排除

- `private-oracles/`：避免公开标准答案；
- `runs/`、`runs-native/`：原始运行目录体积大，且可能包含内部环境路径和服务事件；
- `work/`、虚拟环境、`node_modules/`、上游源码快照和系统日志；
- kubeconfig、私有配置、JWT、API Key 和任何本地凭据。

## 当前结果边界

- 机器可读审计记录四个主榜 Agent 为 108/108 严格可比，K8sGPT 为 3/3 真实 kind 专项；
- Mini-Drop 当前互动修订为 C7 3/3、C8 0/3、C9 3/3；
- Mini-Drop 的 Evidence validity 为 0.963，但 Root match 为 0.185；
- 公开结果不应宣称 Mini-Drop 在所有赛道领先；
- 运行前必须通过环境变量提供 API Key，绝不把 Key 写入文件或命令提交。
