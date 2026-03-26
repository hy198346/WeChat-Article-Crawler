import requests
import re
import base64

# 直接访问文章链接验证
target_link = "https://mp.weixin.qq.com/s/6ZYhtR-neKKx4hWdupOALQ"

print(f'分析文章链接: {target_link}')
print()

try:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(target_link, headers=headers, timeout=10)
    resp.encoding = "utf-8"

    # 尝试多种方式提取 __biz
    # 方式1: 从 var biz 中提取
    biz_patterns = [
        r'var biz = "([A-Za-z0-9_=]+)"',
        r'"biz":"([A-Za-z0-9_=]+)"',
        r'__biz=([A-Za-z0-9_=]+)',
        r'&biz=([A-Za-z0-9_=]+)',
    ]

    biz = None
    for pattern in biz_patterns:
        match = re.search(pattern, resp.text)
        if match:
            biz = match.group(1)
            print(f'找到 __biz: {biz}')
            break

    if biz:
        # 尝试解码
        try:
            # 补齐 base64 填充
            padding = 4 - len(biz) % 4
            if padding != 4:
                biz_padded = biz + '=' * padding
            else:
                biz_padded = biz

            decoded = base64.b64decode(biz_padded).decode('utf-8')
            print(f'解码后: {decoded}')
        except Exception as e:
            print(f'Base64 解码失败: {e}')

    # 尝试从页面内容中提取公众号信息
    # 从页面中搜索所有可能的公众号名称
    nicknames = re.findall(r'nickname[=:]\s*["\']([^"\']+)["\']', resp.text)
    if nicknames:
        print(f'\n可能的公众号名称: {set(nicknames)}')

    # 从页面中搜索 fakeid 相关信息
    fakeids = re.findall(r'fakeid[=:]\s*["\']([A-Za-z0-9_=]+)["\']', resp.text)
    if fakeids:
        print(f'可能的 fakeid: {set(fakeids)}')

    # 打印页面部分内容用于调试
    if not biz and not nicknames and not fakeids:
        print('\n页面内容片段（前2000字符）:')
        print(resp.text[:2000])

except Exception as e:
    print(f'访问失败: {e}')
