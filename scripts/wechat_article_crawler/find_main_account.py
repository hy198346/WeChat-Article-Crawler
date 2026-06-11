import json
import sys
import base64
sys.path.insert(0, '.')
from wechat_crawler import load_json, search_accounts, get_articles

config = load_json('config.json')
token = config.get('token')
cookie = config.get('cookie')

target_link = "https://mp.weixin.qq.com/s/6ZYhtR-neKKx4hWdupOALQ"

# 从文章链接提取的 biz
target_biz = "MzU2NzEwMDc1MA=="
print(f'从文章链接提取的 __biz: {target_biz}')
print(f'解码: {base64.b64decode(target_biz).decode()}')
print()

# 尝试所有可能的搜索词
search_terms = ["爱在冰川", "冰川", "3567100750"]

for term in search_terms:
    print(f'搜索: {term}')
    results = search_accounts(term, token, cookie, begin=0, count=20)

    for item in results:
        nickname = item.get('nickname', '')
        fakeid = item.get('fakeid', '')

        # 获取文章并查找目标文章
        articles, total, _ = get_articles(fakeid, token, cookie, begin=0, count=20)
        if articles:
            for a in articles:
                title = a.get('title', '')
                link = a.get('link', '')

                # 检查是否是目标文章
                if target_link in link:
                    print(f'  ✅ 找到目标文章!')
                    print(f'     公众号: {nickname}')
                    print(f'     fakeid: {fakeid}')
                    print(f'     标题: {title}')
                    break

                # 检查是否是 2026-3-25 数据 格式的文章
                if '2026-3-25' in title and '数据' in title:
                    print(f'  ✅ 找到匹配文章格式!')
                    print(f'     公众号: {nickname}')
                    print(f'     fakeid: {fakeid}')
                    print(f'     标题: {title}')
                    print(f'     链接: {link}')
    print()
