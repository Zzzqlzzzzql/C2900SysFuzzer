# 重新解压 qemu-8.2-eval(1).tar.gz 与对比（2026-10-19）

## 操作

```bash
tar -xzf "<PRIVATE_ASSETS>/qemu-8.2-eval(1).tar.gz" -C <PRIVATE_ASSETS> --overwrite
```

## 解压前后关键文件

| 文件 | SHA256 | 结果 |
|---|---|---|
| `VERSION` | `f922c99d...` | 相同（8.2.0） |
| `hw/mips/soc_eval.c` | `e430239a...` | 相同 |
| `roms/machcore/libmachcore.so` | `a2d10b42...` | 相同 |

tarball 中没有 `hw/mips/octeon.c`、`hw/net/octeon_pko.c`、`octeon-eth` 等
厂商网络模型文件；它是同一份去品牌化 `soc-eval` 基线，不是厂商源码。

## 纯净源码构建 + 启动对比

- 用解压后的源码在 `build-pristine/` 重新 configure/build；
- M7 + `-M soc-eval -cpu Octeon52XX -nic socket,model=eth` 启动到 banner；
- `show ip interface brief`：

```text
Interface                  IP-Address      OK? Method Status                Protocol
c2900>
```

只有表头，没有接口；`show diag`：

```text
Slot 0:
        Unknown (type 0) Port adapter
        Port adapter is disabled
```

与之前修改版 QEMU 的结果一致：**重新解压 tarball 没有补上 I/O controller /
port adapter 网络模型，网络阻塞不变。**

## 恢复

- 解压会覆盖 `soc_eval.c` 以外的 core 文件（我们的 `fuzz` 修改），因此对比后
  重新执行了 `qemu/apply-patch.sh`；
- 重新构建的 fuzz binary 已验证 `-fuzz`、`-M soc-eval`，并重新跑通
  `scripts/verify-crash.sh 4`；
- 产物已同步到 `vendor/qemu/bin` 与 `vendor/qemu/src`。
