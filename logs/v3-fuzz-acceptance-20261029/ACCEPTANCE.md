# 2026-10-29 v3 集成 + `-fuzz` 验收记录

## 环境

- 项目：`<仓库根目录>`
- QEMU 源码：`qemu-8.2-eval-v3` + `qemu/patches/0001-c2900-fuzz-overlay.patch`
- 设备库：`libmachcore.so` v3，BUILD_ID `EVAL-20261028-F`，
  SHA256 `a49f47f6fe302c063231f195cb4abd52684cc7f81c7cd1dbb6edad572b0e980f`
- QEMU 二进制 SHA256：
  `39835d6c9c483521b78cf060abaffe77a80362ea96ecaa1a570fb7cbe4b621a0`
- 固件：M7 `C2900-UN.150.BIN`
- 配置：`runtime/c2900-ios-m7.json`
- 网络：`-nic socket,listen=127.0.0.1:45678`，宿主侧 raw Ethernet peer

## 启动

```bash
export C2900_KERNEL=$PWD/vendor/c2900/firmware/C2900-UN.150.BIN
export C2900_FUZZ_CONFIG=$PWD/runtime/c2900-ios-m7.json
./runtime/start_c2900_fuzz.sh start
```

- 到 IOS banner：约 23 分钟（插桩 + `-icount shift=1`；宿主另有 TCG 负载）。
- IPC：`/tmp/qemu_fuzz_ctl`、`/tmp/qemu_fuzz_st`、shm `0x2000/0x2001` 均就绪。

## forkserver / 覆盖率

- `0x01` save：status=1；baseline edge=62,811（153,132 TB）。
- 无输入轮次：
  - round 1：`0x02` status=1，edge 86；`0x03` status=1。
  - round 2：`0x02` status=1，edge 86；`0x03` status=1，edge 85。
- 注入 raw ARP（who-has 172.16.100.2）轮次：
  - round 3：edge 5,868；guest 正常回 ARP。
  - round 4：edge 5,870；guest 正常回 ARP。
  - round 5：edge 5,869；guest 正常回 ARP。
- `0x02` loadvm 实测 1.25–1.41 s；`0x03` 等待 1.0 s。
- 所有轮次 exit status `0xdeadbeef`（无崩溃；崩溃判定未触发）。

## 网络

- loadvm 后 guest 仍能回应宿主 ARP（MAC `6c:20:56:10:08:28`，
  IP `172.16.100.2`），说明 v3 machcore 的 TX 在快照恢复后仍工作。
- 未做真实 TCP/HTTP 协议验收（需 tap 或 libslirp）；本轮以框架闭环为目标。

## 证据文件

- `coverage.log`、`forkserver.log`：每轮边覆盖与退出状态。
- `serial.log`、`console.log`、`qemu.log`：启动与运行日志。
- `arp-probe{2,3,4}.log`：每轮 ARP 请求/应答。

## 构建注意事项

v3 tarball 的 `subprojects/` 不完整；本次从 v2 备份
`<PRIVATE_ASSETS>/vendor-qemu-v2-backup-20261029.tar.gz` 恢复了
berkeley-softfloat-3、berkeley-testfloat-3、keycodemapdb 完整目录，
以及 libvhost-user/libvduse 的 `include/`。重新解包 v3 后必须重复此步。
