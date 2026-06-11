import json
import sys
sys.path.insert(0, '.')
from wechat_crawler import load_json, get_articles

config = load_json('config.json')
token = config.get('token')
cookie = config.get('cookie')

# 尝试用之前的 fakeid 获取文章
old_fakeid = 'MzIxNDUxNTAxMQ=='
print(f'测试 fakeid: {old_fakeid}')
articles, total, _ = get_articles(old_fakeid, token, cookie, begin=0, count=5)

if articles:
    print(f'获取到 {len(articles)} 篇文章:')
    for a in articles[:2]:
        title = a.get('title', 'N/A')
        link = a.get('link', 'N/A')
        print(f'  - {title}')
        print(f'    链接: {link}')
else:
    print('无法获取文章，fakeid可能已失效')
