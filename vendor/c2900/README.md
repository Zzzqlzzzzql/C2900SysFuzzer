# C2900 QEMU 测试包
> **仓库分发说明**：本仓库只跟踪 `test/` 脚本与 `firmware/checksums.sha256`。
> `firmware/`、`lib/`、`bin/` 为私有资产，由维护者私下提供；完整清单见
> [`../../docs/PRIVATE-ASSETS.md`](../../docs/PRIVATE-ASSETS.md)。
> **项目副本说明（2026-10-29）**：为减小分发体积，已移除 `bin/`（厂商 QEMU 参考二进制）、未使用的 `c2900-universalk9-mz.SPA.150-1.M7.bin` 外层镜像与 `c2900-universalk9-mz.SPA.155-3.M.bin`。运行与 campaign 所需的 M7 内层 `C2900-UN.150.BIN`、M9 外层镜像、disk0/flash/rommon 均保留。

本项目提供自包含的 Cisco C2900 QEMU 环境，以及用于启动设备、配置协议和验证服务的测试脚本。
## 一、运行准备
先使用 `cd` 命令进入本项目根目录。后续所有命令都必须在项目根目录执行，因为启动脚本会将当前工作目录作为软件包路径。
首次使用时执行依赖安装脚本。该脚本只需要执行一次：
```shell
./test/download_deps.sh
```
脚本会安装 curl、socat、iproute2、SNMP 工具、strongSwan、tmux 和 Python 3。README 中的 Telnet、SSH 和抓包命令还要求宿主机已提供相应客户端工具。
## 二、启动与控制
执行启动脚本：
```shell
./test/start_c2900.sh
```
脚本会自动重建 `tap0`、启动 QEMU，并进入名为 `console` 的 tmux 串口会话。等待串口出现 `c2900>` 后，再从另一个宿主机终端运行配置脚本。
在串口中按 `Ctrl-B d` 可以退出 tmux 而不停止设备。重新进入串口时执行：
```shell
tmux attach -t console
```
停止设备并删除 `tap0`：
```shell
./test/stop_c2900.sh
```
## 四、Telnet 测试
先运行 Telnet 配置脚本。该脚本会为 `GigabitEthernet0/0` 配置 `172.16.100.2/24`，并启用 Telnet 登录：
```shell
python3 test/config_telnet.py
```
测试 Telnet：
```shell
telnet 172.16.100.2
```
登录密码为 `cisco`。
## 五、SSH 测试
Telnet 测试完成后运行 SSH 配置脚本：
```shell
python3 test/config_ssh.py
```
该脚本会把 VTY 输入协议切换为 SSH，因此执行后 Telnet 将不可用。连接前先删除该地址已有的 SSH 主机密钥记录：
```shell
ssh-keygen -R 172.16.100.2
```
测试 SSH：
```shell
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 -o HostKeyAlgorithms=+ssh-rsa -o Ciphers=+aes128-cbc admin@172.16.100.2
```
SSH 密码为 `cisco`。
## 六、SNMP 测试
运行 SNMP 配置脚本：
```shell
python3 test/config_snmp.py
```
遍历 Cisco 企业 MIB：
```shell
snmpwalk -v 2c -c public 172.16.100.2 .1.3.6.1.4.1.9
```
读取设备描述对象：
```shell
snmpget -v 2c -c public 172.16.100.2 .1.3.6.1.2.1.47.1.1.1.1.13.1
```
## 七、HTTP 与 HTTPS 测试
运行 Web 配置脚本，一次完成 HTTP 与 HTTPS 的配置：
```shell
python3 test/config_web.py
```
该脚本会先校准设备时钟（模拟器 RTC 年份比宿主机少 100 年，时钟无效时设备无法生成 RSA 密钥和证书，必须在启用 HTTPS 前校准）、关闭控制台分页，确保存在 RSA 密钥（已有则保留），再启用 HTTP 与 HTTPS 服务，并在结束时校验时钟、HTTP server 与 secure server 状态。**每次设备重启后都需要重新运行本脚本。**

验证 HTTP 与 HTTPS 服务：
```shell
python3 test/test_web.py
```
HTTP 验证使用 curl，等价于手工执行的验证命令 `curl -v -u admin:cisco http://172.16.100.2`；HTTPS 验证输出 `TLS 握手成功，cipher = 0x5` 与 `HTTP 响应：HTTP/1.1 200 OK`（页面为 c2900 Home Page）。两个服务都输出 `HTTP 验证通过`、`HTTPS 验证通过` 即验证成功。也可以只验证其中一个服务：
```shell
python3 test/test_web.py --http
python3 test/test_web.py --https
```
HTTP 与 HTTPS 用户名均为 `admin`，密码为 `cisco`。

说明：设备 HTTPS 仅支持 SSLv3 与 RC4/DES/3DES 旧密码套件（ServerHello 版本 0300），现代 curl/openssl（OpenSSL 3 已移除这些套件）无法完成握手，因此 HTTPS 验证使用 tlslite-ng（已随 `test/download_deps.sh` 安装，也可手动 `pip3 install tlslite-ng`）。
## 八、IPsec 测试
IPsec 依赖 `securityk9` 许可。首次进行 IPsec 测试时，从另一个宿主机终端执行许可配置脚本：
```shell
python3 test/config_ipsec_license.py
```
`config_ipsec_license.py` 只需要成功执行一次。许可在下次启动时生效，因此配置完成后必须停止并重新启动 C2900：
```shell
./test/stop_c2900.sh
./test/start_c2900.sh
```
重启后运行 IPsec 配置脚本：
```shell
python3 test/config_ipsec.py
```
该脚本先配置 C2900，再配置宿主机 strongSwan。它会覆盖宿主机的 `/etc/ipsec.conf` 和 `/etc/ipsec.secrets`，并尝试建立名为 `c2900` 的隧道。
在宿主机终端启动 10 秒抓包：
```shell
sudo timeout 10 tcpdump -i tap0 -nn
```
在抓包运行期间，从 C2900 串口执行：
```text
ping 172.16.100.1
```
抓包中出现 C2900 与宿主机之间的流量，即可用于检查 IPsec 通信情况。
## 九、网络地址
- 宿主机 `tap0`：`172.16.100.1/24`
- C2900 `GigabitEthernet0/0`：`172.16.100.2/24`

## 十、主要脚本
| 文件 | 用途 |
| --- | --- |
| `test/download_deps.sh` | 一次性安装运行依赖 |
| `test/start_c2900.sh` | 创建 TAP、启动 QEMU 并进入串口 |
| `test/stop_c2900.sh` | 停止 QEMU 并删除 TAP |
| `test/test_coverage.sh` | 覆盖率试运行：tmux 后台跑 QEMU（无终端输出、`-nic user`、`-snapshot` 不动状态盘），插件常开 |
| `test/config_ipsec_license.py` | 一次性启用 `securityk9` 许可 |
| `test/config_telnet.py` | 配置接口地址和 Telnet |
| `test/config_ssh.py` | 配置 SSH |
| `test/config_snmp.py` | 配置 SNMP |
| `test/config_web.py` | 配置 HTTP 与 HTTPS |
| `test/test_web.py` | 验证 HTTP 与 HTTPS 服务（HTTP 用 curl，HTTPS 用 tlslite-ng） |
| `test/config_ipsec.py` | 配置 C2900 IPsec 和宿主机 strongSwan |
| `test/connect_console.py` | 连接串口并过滤已知无关告警 |

## 十一、包内容
| 目录 | 说明 |
| --- | --- |
| `bin/` | MIPS64 OCTEON QEMU 可执行文件 |
| `lib/` | QEMU 所需动态库、覆盖率插件 `libcoverage2.so` |
| `firmware/` | C2900 IOS 固件、bootloader、flash 和磁盘镜像 |
| `test/` | 启停、配置和测试脚本 |
| `logs/` | 覆盖率采集日志目录（仅启用插件后生成） |

## 十二、覆盖率采集（可选）

测试包内置 QEMU TCG 覆盖率插件 `lib/libcoverage2.so`，可实时采集翻译块（TB）级覆盖率。默认关闭，需要采集时用环境变量启用：

```shell
C2900_COVERAGE=1 ./test/start_c2900.sh
```

只想快速验证覆盖率插件、又不想动 tap 和状态盘时，可用隔离试运行脚本（tmux 后台运行、无终端输出，不需 sudo）：

```shell
./test/test_coverage.sh          # 启动（-nic user、-snapshot、插件常开）
./test/test_coverage.sh stop     # 停止（正常退出，插件落最终快照）
```

启用后，QEMU 一启动就在项目根目录生成 `logs/coverage.log`，实时追加 CSV 行：

```text
virt_addr,trans_count,exec_count,insn_count,first_insn
0x81000538,1,0,9,0x00042d02
0x81000538,1,1,9,0x00042d02
```

`first_insn` 是该翻译块第一条指令的原始字节（按 guest 内存序拼成十六进制数；mips64 大端下即 CPU 取指的指令字，如 `0x3c1c0004` 为 `lui gp,0x4`），位宽为指令字节数 × 2（MIPS 指令 4 字节即 8 位）。

计数在内存中累积，仅在某个 TB 的 `trans_count` 或 `exec_count` 跨过 2 的幂（1、2、4、8……）时写一行并刷盘（对数级采样），退出时再为每个 TB 补一行最终计数。可 `tail -f logs/coverage.log` 实时观察；文件为追加式，同一 `virt_addr` 最后一次出现的行即其最终计数（正常退出后；被强杀则为最后一次采样值）。

**地址规则**：① 只跟踪起始虚拟地址 > `0x81000000` 的翻译块（剔除 `0x21xxxxxx`/`0x25xxxxxx` 等零扩展低地址 TLB 映射代码，实测占全部日志行 ~98%）；② 只保留低 32 位首字符为 `8` 的条目，即 KSEG0 低窗口 `0x80000000`–`0x8fffffff`（IOS 内核/异常向量），boot ROM（`0xffffffffbfc00xxx`）与 KSEG1 别名（`0xffffffffa1xxxxxx`）都不记。日志中地址按低 32 位输出（统一去掉符号扩展的 8 个 `f`：`0xffffffff81000538` → `0x81000538`；该窗口物理地址 = 显示值 − `0x80000000`）。不满足规则的块在翻译回调里直接跳过，连执行回调都不挂，日志量显著下降。

日志体积与运行时长、执行速率无关，整场上界约为每 TB 65 行、总量几 MB 量级；即使 guest 卡在崩溃重启死循环中长时间采集也不会失控。（早期版本按每次 TB 执行逐行刷盘，死循环下曾几分钟写出 GB 级日志，已修复。）

插件使用 stock TCG 插件 ABI（API v1），源码位于 QemuSysFuzzer 项目 `Qemu/contrib/coverage2.c`。如需修改并重建：

```shell
cc -m64 -mcx16 -O0 -g -fPIC -Wall $(pkg-config --cflags glib-2.0) \
   -I<QemuSysFuzzer>/Qemu/include/qemu -I<QemuSysFuzzer>/Qemu/include -shared \
   -o lib/libcoverage2.so <QemuSysFuzzer>/Qemu/contrib/coverage2.c \
   $(pkg-config --libs glib-2.0)
```
