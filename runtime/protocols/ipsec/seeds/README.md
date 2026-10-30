IPsec 需要自定义 harness：AFLNet 没有 IKE/ESP 的 -P。
IKE 走 UDP/500（或 4500），ESP 是 IP protocol 50。
准备 IKEv1/v2 请求种子，并使用自定义 UDP/raw 注入器；
不能直接用 stock afl-fuzz -P。
