import sys

from scripts.wechat_article_crawler import bootstrap_refresh_auth as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
