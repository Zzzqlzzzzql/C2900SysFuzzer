# 私有资产清单

本仓库**不包含** Cisco 固件、厂商 QEMU eval 源码和闭源设备库。
请向维护者私下获取一个 **目录形式** 的合并资产包（QEMU 侧 + 固件侧），
复制到 `vendor/` 后运行 `./build.sh`。这些文件没有公开下载链接，也禁止
上传到 GitHub、公开网盘、Release 或 LFS。

## 1. 目录形式资产包

```text
c2900-private-assets/              # 约 624 MB，约 10,676 个文件
├── README.md
├── qemu/
│   ├── src/                       # qemu-8.2-eval-v3，已解包，subprojects 已补齐
│   └── lib/libmachcore.so         # v3 闭源设备库
└── c2900/
    ├── firmware/                  # 6 个固件/磁盘镜像 + checksums + README
    └── lib/                       # 16 个运行库（不含 libcoverage2.so）
```

注意：

- `qemu/src/` 是**未应用 C2900 fuzz 补丁**的厂商基线；`build.sh` 会复制到
  `vendor/qemu/src`，再应用 `qemu/patches/0001-c2900-fuzz-overlay.patch`。
- 包内不再提供 `qemu-8.2-eval-v3.tar.gz` / `qemu-subprojects-fix.tar.gz`。
- 不包含预编译 QEMU；`build.sh` 会从 `qemu/src` 构建。
- `libcoverage2.so` 由仓库 `tools/coverage2.c` 重建，不在包内。

使用：

```bash
cp -a c2900-private-assets vendor/
./build.sh
```

`build.sh` 会把 `qemu/src` 安装到 `vendor/qemu/src`，安装
`libmachcore.so`、固件和运行库；如果 vendor 中已有资产则默认跳过安装，
`--force-assets` 可强制重装（包括覆盖 `vendor/qemu/src`）。资产目录不带
哈希清单，`build.sh` 只做结构检查。

也可以手工安装：

```bash
cp -a c2900-private-assets vendor/

# 或者只装资产，不构建：
./scripts/prepare-private-assets.sh ./vendor/c2900-private-assets
```

目录形式有 1 万多个文件；如果自行压缩传输，`build.sh` 也支持外层
`.tar.zst` / `.tar.gz`（解压后仍是同样的目录结构）。

## 2. 单个文件与哈希

| 文件 | 大小（B） | SHA256 | 仓库位置 |
|---|---:|---|---|
| `libmachcore.so`（v3） | 125,360 | `a49f47f6fe302c063231f195cb4abd52684cc7f81c7cd1dbb6edad572b0e980f` | `vendor/qemu/lib/` |
| `C2900-UN.150.BIN`（M7） | 131,292,952 | `a398d815722310afc21ab670b0b2101decf587663a21456da4326a939ee9f465` | `vendor/c2900/firmware/` |
| `c2900-universalk9-mz.SPA.150-1.M9.bin` | 62,750,260 | `e8c0db800efb5a7bb930ff89277c9906603690e17079033ec3814ddd9639da1a` | `vendor/c2900/firmware/` |
| `disk0.raw` | 268,435,456 | `b58861ff4d93e63760846cc250836d85b1bd84351a0b2011165e721360a8ebd8` | `vendor/c2900/firmware/` |
| `flash.bin` | 16,777,216 | `f3db12f9cd9856654ed772e0516c5742d5ab65bf07b802a5ca46e21f1c1c315a` | `vendor/c2900/firmware/` |
| `mips64_mixed.bin` | 11,712 | `fd5c687d1833c3f8319cf6cef3133d0893a8b02eb153f8a1bb4cd0755f78cfd2` | `vendor/c2900/firmware/` |
| `rommon_var.bin` | 12,800 | `fc615a7ac70a2e1d24d524d01587b2be7205962770524dc7c187ec044fd59b13` | `vendor/c2900/firmware/` |
| `qemu/src/VERSION` | 6 | — | 目录形式源码树（由目录树本身提供） |

固件哈希同时保存在 [`vendor/c2900/firmware/checksums.sha256`](../vendor/c2900/firmware/checksums.sha256)，
可用 `scripts/check-assets.sh` 一键校验。

> `disk0.raw`、`flash.bin`、`rommon_var.bin` 是 QEMU 会写入的工作副本
> （尤其 `rommon_var.bin` 在运行后可能变化）。首次分发哈希以上表为准；
> `check-assets.sh` 对这三个文件只告警、不硬失败，对内核/引导镜像严格校验。
> 需要还原时重新从私有资产目录复制。

> 合并资产包**不包含** `libcoverage2.so`；它由仓库的 `tools/coverage2.c`
> 重建（`scripts/build-coverage-plugin.sh`）。

## 3. 手工安装/替换

```bash
cp -a c2900-private-assets/qemu/src/. vendor/qemu/src/
cp -a c2900-private-assets/qemu/lib/. vendor/qemu/lib/
cp -a c2900-private-assets/c2900/firmware/. vendor/c2900/firmware/
cp -a c2900-private-assets/c2900/lib/. vendor/c2900/lib/
./scripts/prepare-qemu-src.sh      # 确保 fuzz 补丁已应用
./scripts/build-qemu.sh
./scripts/prepare-aflnet.sh
./scripts/check-assets.sh
./scripts/check-env.sh
```

`prepare-qemu-src.sh` 优先使用已存在的 `vendor/qemu/src`（目录形式），
只有在源码目录不存在时才回退到 tarball 解包。

## 4. 放在仓库外

运行脚本支持环境变量，私有资产可以完全放在仓库外：

```bash
export C2900_PKG=/data/c2900-pkg              # 内含 firmware/、lib/、test/
export C2900_QEMU=/data/qemu/bin/qemu-system-mips64
export C2900_QEMU_LIB=/data/qemu/lib          # 内含 libmachcore.so
export QEMU_SRC=/data/qemu-8.2-eval-v3        # 已解包的源码目录
```

## 5. 可选：tarball 回退

如果手上只有旧的 `qemu-8.2-eval-v3.tar.gz` + `qemu-subprojects-fix.tar.gz`
（不在当前分发目录中），仍可放到 `vendor/qemu/`，`prepare-qemu-src.sh`
会在 `vendor/qemu/src` 不存在时自动解包：

```text
vendor/qemu/qemu-8.2-eval-v3.tar.gz
vendor/qemu/qemu-subprojects-fix.tar.gz
```

- 源码包 SHA256：`ce6cbdace15a3e6973d9e6b5acebc58b1cf001531e9d34cbcd97f4c51ea579ab`
- fix 包 SHA256：`e49ceac2a78ad77c5640e2d26a914c3966a76f1f94728b94f01fb8a88a0ade13`

这是兼容路径；正常分发请使用目录形式。

## 6. AFLNet

AFLNet 不属于私有资产：仓库的 submodule 指向**官方**
<https://github.com/aflnet/aflnet>，固定 commit
`96032f8`；`build.sh` 会拉取并应用 `patches/aflnet.patch` 获得
`-Z` 外部 QEMU 系统模式支持。该补丁是 Apache-2.0 项目的衍生修改，
随仓库公开。

## 7. 许可与分发

| 资产 | 性质 |
|---|---|
| QEMU eval 源码 / `libmachcore.so` | 厂商提供，闭源组件，禁止公开再分发 |
| Cisco IOS 固件 / 磁盘镜像 | Cisco 版权，禁止公开再分发 |
| 运行库（glib/pixman/…） | 各自开源许可证；也可用发行版包替代 |
| AFLNet | Apache-2.0（官方 submodule + 本项目补丁） |
| 本项目自有代码 | 见 `THIRD_PARTY.md` |

私有资产仅提供给项目协作者用于研究/测试，不构成任何再分发授权。
