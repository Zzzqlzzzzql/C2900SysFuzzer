HTTPS 种子需要包含真实的 SSLv3 握手（C2900 只支持 SSLv3+RC4）。
可用 test/test_web.py 的 tlslite-ng 客户端抓取一次完整会话（ClientHello/
ServerHello/Certificate/.../Application Data），保存为二进制种子。
