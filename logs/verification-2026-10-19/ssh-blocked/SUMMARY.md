# SSH 实测阻塞（2026-10-19）

使用修改后的 QEMU（soc-eval）+ M7 启动到 banner，运行
`runtime/protocols/ssh/configure.py`。

现象：

- IOS 平台日志：`%PA-2-UNDEFIO: Unsupported I/O Controller (type 0) in I/O Bay`
- `show ip interface brief` 只有表头，没有任何接口行
- `interface GigabitEthernet0/0` 被 IOS 拒绝：
  `% Invalid input detected at '^' marker.`
- `show diag`：`Slot 0: Unknown (type 0) Port adapter`

结论：**eval `soc-eval` 板级模型没有向 IOS 暴露网络接口**（I/O controller /
port adapter 缺失）。SSH 配置脚本因此无法设置 Gi0/0，也不会保存快照
（符合“配置失败不快照”的设计）。

这不是 SSH 脚本逻辑问题；vendor 包的 `-M octeon -nic tap,model=octeon-eth`
测试能通网，而 eval 去品牌化板级 + `eth` 模型不行。

后续需要：
1. 取得 vendor Octeon 板级/`octeon-eth` 源码并移植 fuzz overlay；或
2. 修复 `soc_eval`/`libmachcore` 的 I/O controller 模型；或
3. 用 vendor binary + stock plugin + 外部 forkserver（路径 B）；
4. 在 sudo/tap1 可用时先对比 vendor 与 eval 的接口枚举。
