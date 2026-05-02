import unittest

import wechat_crawler


class TestAuthExpiredDetection(unittest.TestCase):
    def test_looks_like_login_html_by_chinese_hint(self):
        html = "<html><title>微信公众平台</title><body>使用微信扫一扫</body></html>"
        self.assertTrue(wechat_crawler._looks_like_wechat_login_html(html))

    def test_looks_like_login_html_by_url(self):
        html = "<html><script>location.href='https://mp.weixin.qq.com/cgi-bin/login'</script></html>"
        self.assertTrue(wechat_crawler._looks_like_wechat_login_html(html))

    def test_not_login_html(self):
        html = "<html><body>ok</body></html>"
        self.assertFalse(wechat_crawler._looks_like_wechat_login_html(html))


if __name__ == "__main__":
    unittest.main()

