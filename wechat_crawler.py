import sys

from scripts.wechat_article_crawler import wechat_crawler as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
