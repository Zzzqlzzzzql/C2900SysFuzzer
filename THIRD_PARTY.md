# 第三方组件与许可

本仓库包含/引用以下第三方组件。**私有资产（Cisco 固件、厂商 QEMU eval 包、
libmachcore.so）不在此列，也不在本仓库中分发。**

| 组件 | 位置 | 许可 | 说明 |
|---|---|---|---|
| AFLNet | `vendor/aflnet`（官方 submodule） | Apache-2.0 | 基于 AFL，网络协议 fuzz；`patches/aflnet.patch` 增加 `-Z` 系统模式支持 |
| QEMU | 私有基线 + `qemu/patches/` | GPL-2.0 | 补丁为本项目自有代码；完整 QEMU 源码由维护者私下提供 |
| QemuSysFuzzer | 参考项目 | 见上游仓库 | `tools/coverage2.c` 来源于该项目 |
| glib / pixman / slirp 等运行库 | 私有资产包 | 各自开源许可 | 未随仓库分发 |
| Cisco IOS 固件 | 私有资产 | Cisco 版权 | 未随仓库分发 |

## 自有代码

本仓库自有代码（`scripts/`、`runtime/`、`fw_probe/`、`qemu/patches/`、
`tools/coverage2.c` 等）的许可见 `LICENSE`（如未附，默认保留所有权利，
请联系维护者确认）。

## 参考链接

- QemuSysFuzzer: <https://github.com/Inuyasha-fan/QemuSysFuzzer>
- AFLNet（官方）: <https://github.com/aflnet/aflnet>
- QEMU: <https://www.qemu.org/>
