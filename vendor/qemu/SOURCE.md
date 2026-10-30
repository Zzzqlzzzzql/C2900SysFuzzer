# vendor/qemu 来源与构建记录

- 私有分发：`c2900-private-assets/qemu/src/`（**私有目录，不随仓库分发**）
  - 已解包的 `qemu-8.2-eval-v3`，`subprojects/` 已补齐，未应用 C2900 fuzz 补丁。
  - 获取方式：联系维护者私下提供，见 [`../../docs/PRIVATE-ASSETS.md`](../../docs/PRIVATE-ASSETS.md)
  - 原始 tarball 仅作来源记录/可选回退：
    SHA256 `ce6cbdace15a3e6973d9e6b5acebc58b1cf001531e9d34cbcd97f4c51ea579ab`。
- 工作树：`src/`（由 `build.sh`/`scripts/prepare-private-assets.sh` 从私有目录安装，
  构建时应用 `qemu/patches/0001-c2900-fuzz-overlay.patch`，不入库）
- 机器/网卡：`-M octeon` / `model=octeon-eth`
- 设备库：`lib/libmachcore.so`（v3，125,360 B，BUILD_ID `EVAL-20261028-F`）
  - SHA256：`a49f47f6fe302c063231f195cb4abd52684cc7f81c7cd1dbb6edad572b0e980f`
- 预编译 QEMU：`bin/qemu-system-mips64`（可选，私有资产或本地构建）
  - SHA256：`19361740250410d5a849dbbf03d5b05acfee99e676011214822147172c0b1a42`
  - 2026-10-29 体积裁剪：`strip --strip-debug`（59 MB → 17 MB）
  - `src/build/` 不入库，可由 `scripts/build-qemu.sh` 重新生成。

## v3 相对 v2 的变化

- QEMU C 源码逐字节相同；8 个被补丁修改的文件与 v2 叠加前备份一致。
- 闭源 `libmachcore.so` 更新（BUILD_ID `EVAL-20261006-E` → `EVAL-20261028-F`）。
  功能差异只在 `dev.db` MMIO 写处理函数：删除
  `((data >> 16) & 0x3f) <= 1` 时跳过发送的过滤条件，**修复 guest→host TX**。
- 原始 v3 tarball 的 `subprojects/` 不完整：`berkeley-softfloat-3` /
  `berkeley-testfloat-3` / `keycodemapdb` 只有空 `.git` 目录，
  `libvhost-user` / `libvduse` 缺 `include/`。当前私有分发包中的 `qemu/src`
  已经补齐；只有在用旧 tarball 回退时才需要
  `qemu-subprojects-fix.tar.gz`（387 KB，
  SHA256 `e49ceac2a78ad77c5640e2d26a914c3966a76f1f94728b94f01fb8a88a0ade13`）。
  **否则 meson 报 “Subproject exists but has no meson.build file” 或缺 `include/atomic.h`。**
- 源码仍不含 `hw/mips/octeon_pow.c` / `hw/net/octeon_pko.c`；TX 实现在闭源
  machcore blob 内。

## 构建配置

```bash
cd src
./configure --target-list=mips64-softmmu -Dpixman=disabled \
  --disable-gtk --disable-sdl --disable-vnc --disable-docs \
  --disable-werror --disable-guest-agent
ninja -C build qemu-system-mips64
```

项目根执行 `./scripts/build-qemu.sh` 会自动应用补丁、构建并同步
`bin/`、`lib/`。

## 验收记录（2026-10-29）

- M7 `C2900-UN.150.BIN` + `-fuzz runtime/c2900-ios-m7.json`：
  约 23 分钟到 IOS banner（插桩 + `-icount shift=1`）。
- 0x01 save snapshot：status=1，baseline edge=62,811。
- 0x02 loadvm：status=1，约 1.25–1.41 s；覆盖率重置到 86 边。
- 0x03 end round：status=1，exit status `0xdeadbeef`（未崩溃）。
- 带 ARP 输入连续 3 轮：edge 5,868 / 5,870 / 5,869；每轮 guest 都回 ARP。
- 2026-10-29 正式 campaign（campaign.sh + AFLNet 真实 SSH）：
  `start` 完成 SSH 就绪与 0x01；AFLNet 60s 试跑 9 execs、
  bitmap_cvg 13.48%、stability 100%、paths_total 1、unique_crashes 0。
- 日志：`logs/v3-fuzz-acceptance-20261029/`、`logs/ssh-campaign-20261029/`。

## 回滚

- 源码树未入库；重新解包私有 tarball 即可回到干净基线。
- 已应用补丁的树可执行 `./qemu/apply-patch.sh vendor/qemu/src --revert`。
