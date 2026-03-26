import json
import sys
sys.path.insert(0, '.')
from wechat_crawler import load_json, get_articles

config = load_json('config.json')
token = config.get('token')
cookie = config.get('cookie')

# 爱在冰川的 fakeid
fakeid = "MzIxNDUxNTAxMQ=="
print(f'抓取公众号: 爱在冰川')
print(f'fakeid: {fakeid}')
print()

articles, total, _ = get_articles(fakeid, token, cookie, begin=0, count=10)

if articles:
    print(f'获取到 {len(articles)} 篇文章:\n')
    for i, a in enumerate(articles[:5], 1):
        title = a.get('title', 'N/A')
        link = a.get('link', 'N/A')
        create_time = a.get('create_time', 0)
        from wechat_crawler import _format_publish_times
        time_info = _format_publish_times(create_time)
        print(f'{i}. {title}')
        print(f'   时间: {time_info["published_at"]}')
        print(f'   链接: {link}')
        print()
else:
    print('无法获取文章')
