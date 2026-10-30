#!/usr/bin/env bash
# 让外部普通浏览器访问 C2900 Web UI 的 TLS 终结代理：
#   浏览器 --(现代 TLS, 8443)--> 本机 socat --(明文 HTTP)--> 172.16.100.2:80
#
# 设备自带的 HTTPS 只支持 SSLv3 + RC4/DES（768 位临时自签证书、每次连接重新签发），
# 现代浏览器在 TLS 握手上就会被拒绝（sslv3 alert handshake failure），
# 换设备证书/导入信任库都救不了，因此代理走明文 HTTP 上游。
# 浏览器侧看到的是本脚本自签的现代证书：首次访问点一次"高级 -> 继续前往"即可；
# 要完全无警告就在客户端机器上导入米本证书，或改用 mkcert/真实域名证书替换 certs/。
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$SCRIPT_DIR/certs"
ROUTER_HTTP="172.16.100.2:80"
PORT="${1:-8443}"
SESSION="webproxy"

if tmux has-session -t "$SESSION" 2>/dev/null; then
	echo "webproxy already running (tmux session '$SESSION'), port $PORT"
	exit 0
fi

if [[ ! -f "$CERT_DIR/proxy.crt" || ! -f "$CERT_DIR/proxy.key" ]]; then
	mkdir -p "$CERT_DIR"
	openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
		-keyout "$CERT_DIR/proxy.key" -out "$CERT_DIR/proxy.crt" \
		-subj "/CN=c2900.lab" \
		-addext "subjectAltName=IP:$(hostname -I | awk '{print $1}'),IP:172.16.100.1,IP:127.0.0.1,DNS:localhost"
fi

tmux new-session -d -s "$SESSION" \
	"exec socat OPENSSL-LISTEN:$PORT,reuseaddr,fork,cert=$CERT_DIR/proxy.crt,key=$CERT_DIR/proxy.key,verify=0 TCP:$ROUTER_HTTP"

sleep 1
if ss -ltn | grep -q ":$PORT "; then
	echo "proxy listening on 0.0.0.0:$PORT -> http://$ROUTER_HTTP"
	echo "external browsers: https://$(hostname -I | awk '{print $1}'):$PORT/"
	echo "stop: tmux kill-session -t $SESSION"
else
	echo "proxy failed to start; check: tmux capture-pane -p -t $SESSION" >&2
	exit 1
fi
