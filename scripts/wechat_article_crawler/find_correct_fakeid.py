import json
import sys
sys.path.insert(0, '.')
from wechat_crawler import load_json, search_accounts, get_articles

config = load_json('config.json')
token = config.get('token')
cookie = config.get('cookie')

# 搜索爱在冰川
query = "爱在冰川"
print(f'搜索公众号: {query}')
print()

results = search_accounts(query, token, cookie, begin=0, count=10)

if results:
    print(f'找到 {len(results)} 个结果:\n')
    for i, item in enumerate(results, 1):
        nickname = item.get('nickname', 'N/A')
        fakeid = item.get('fakeid', 'N/A')
        alias = item.get('alias', 'N/A')  # 微信号
        signature = item.get('signature', 'N/A')[:50]  # 简介，截取前50字符
        print(f'{i}. {nickname}')
        print(f'   fakeid: {fakeid}')
        print(f'   微信号: {alias}')
        print(f'   简介: {signature}...')
        print()
    
    # 验证第一个结果的 fakeid
    if results:
        first_fakeid = results[0].get('fakeid')
        print(f'验证第一个结果的 fakeid: {first_fakeid}')
        print()
        articles, total, _ = get_articles(first_fakeid, token, cookie, begin=0, count=5)
        if articles:
            print(f'获取到 {len(articles)} 篇文章:')
            for a in articles[:3]:
                title = a.get('title', 'N/A')
                link = a.get('link', 'N/A')
                print(f'  - {title}')
                print(f'    链接: {link}')
        else:
            print('无法获取文章')
else:
    print('未找到结果')
