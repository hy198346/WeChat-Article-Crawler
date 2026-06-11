import sys

from scripts.wechat_article_crawler import article_analysis as _impl

sys.modules[__name__] = _impl
