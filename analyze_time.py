import requests
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0'
}

url = 'https://mp.weixin.qq.com/s/Xx1zmOE0K5QlHM_tXyMWyw'
response = requests.get(url, headers=headers)
response.encoding = 'utf-8'
html = response.text

print('=== 查找包含 publish_time 的内容 ===')
publish_time_pattern = r'publish_time\s*[:=]\s*["\']([^"\']+)["\']'
matches = re.findall(publish_time_pattern, html)
print(f'Found {len(matches)} publish_time matches:')
for match in matches:
    print(f'  {match}')

print('\n=== 查找日期格式的字符串 ===')
date_pattern = r'\d{4}-\d{2}-\d{2}'
date_matches = re.findall(date_pattern, html)
print(f'Found {len(date_matches)} date matches:')
for match in date_matches[:10]:  # 只显示前10个
    print(f'  {match}')

# 查找包含 publish_time 的脚本标签
print('\n=== 查找包含 publish_time 的脚本标签 ===')
script_pattern = r'<script[^>]*>(.*?)</script>'
scripts = re.findall(script_pattern, html, re.DOTALL)
for i, script in enumerate(scripts):
    if 'publish_time' in script:
        print(f'Script {i}:')
        # 提取包含 publish_time 的行
        lines = script.split('\n')
        for line in lines:
            if 'publish_time' in line:
                print(f'  {line.strip()}')
        print('---')