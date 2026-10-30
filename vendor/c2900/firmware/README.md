# firmware/ —— 私有固件目录

本目录中的固件/磁盘镜像**不随仓库分发**，由维护者私下提供。
仓库只跟踪 `checksums.sha256` 和本说明文件。

## 必需文件

| 文件 | 大小（B） | 用途 |
|---|---:|---|
| `C2900-UN.150.BIN` | 131,292,952 | M7 已解压内层镜像（`-kernel` 直接启动） |
| `c2900-universalk9-mz.SPA.150-1.M9.bin` | 62,750,260 | M9 外层镜像（stub 自行解压） |
| `disk0.raw` | 268,435,456 | disk0 磁盘镜像 |
| `flash.bin` | 16,777,216 | flash 镜像 |
| `mips64_mixed.bin` | 11,712 | bootloader |
| `rommon_var.bin` | 12,800 | rommon / iomem |

## 校验

```bash
./scripts/check-assets.sh
# 或
cd vendor/c2900/firmware && sha256sum -c checksums.sha256
```

哈希与获取方式见 [`../../../docs/PRIVATE-ASSETS.md`](../../../docs/PRIVATE-ASSETS.md)。

> Cisco IOS 固件受版权保护，请勿上传到 GitHub、公开网盘或 Release。

> `disk0.raw`、`flash.bin`、`rommon_var.bin` 是 QEMU 可写的工作副本，
> 运行后哈希可能变化（尤其 `rommon_var.bin`）。`check-assets.sh` 对这三个
> 文件只告警、不硬失败；需要还原时从私有资产包重新复制。
