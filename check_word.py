"""
check_word.py：抽查工具，看某個詞在文章裡實際是怎麼被使用的

為什麼需要：
  分析的方式是「出現某個詞，就算有提到這個面向」。
  但有些詞會誤判，例如「不吃」可能是「貓不吃這款」（適口性），
  也可能是「醫生說不吃東西要注意」（不是在講適口性）。
  抽幾段前後文出來看，就能判斷這個詞要不要留在詞庫裡。

用法（在終端機輸入）：
  python check_word.py 詞
  python check_word.py 詞 子類別

  例：python check_word.py 不吃
      python check_word.py 吋 電視機
      python check_word.py lg 電視機

會印出：
  1. 這個詞出現在幾篇文章、出現在標題／內文／留言各幾次
  2. 隨機 10 段前後文（每段前後各 30 個字）

只看分析時有保留的文章（會讀 output/01_篩選紀錄.csv）。
每次執行抽到的段落不一樣，可以多跑幾次。
"""
import os
import re
import sys
import random

import pandas as pd

SHOW = 10      # 每次顯示幾段
AROUND = 30    # 前後各顯示幾個字

if len(sys.argv) < 2:
    sys.exit("用法：python check_word.py 詞 [子類別]\n例：python check_word.py 不吃 貓食品")
word = sys.argv[1]
category = sys.argv[2] if len(sys.argv) > 2 else None

# ---- 讀資料 ----
articles = pd.read_csv("data/ptt_articles.csv")
comments = pd.read_csv("data/ptt_comments.csv")
comments["comment"] = comments["comment"].fillna("").astype(str)
comment_text = comments.groupby("url")["comment"].agg(" / ".join)
articles["comments"] = articles["url"].map(comment_text).fillna("")
for col in ["title", "content", "comments"]:
    articles[col] = articles[col].fillna("").astype(str)

# 只看分析時保留的文章
record_file = "output/01_篩選紀錄.csv"
if os.path.exists(record_file):
    record = pd.read_csv(record_file)
    kept_urls = set(record.loc[record["保留"], "url"])
    articles = articles[articles["url"].isin(kept_urls)]
else:
    print("（還沒跑過 2_analyze.py，所以看的是全部文章）")

if category:
    articles = articles[articles["category"] == category]

# ---- 找出所有出現的地方 ----
# 比對方式跟 2_analyze.py 一樣：英文詞前後不能緊接英文字母（"lg" 不會比對到 "blog"）
if re.fullmatch(r"[A-Za-z0-9 .']+", word):
    pattern = re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])", re.IGNORECASE)
else:
    pattern = re.compile(re.escape(word), re.IGNORECASE)
found = []                                   # (文章標題, 位置, 前後文)
counts = {"標題": 0, "內文": 0, "留言": 0}
hit_articles = 0

for _, a in articles.iterrows():
    hit = False
    for place, col in [("標題", "title"), ("內文", "content"), ("留言", "comments")]:
        text = a[col]
        for m in pattern.finditer(text):
            hit = True
            counts[place] += 1
            s, e = max(0, m.start() - AROUND), m.end() + AROUND
            snippet = text[s:e].replace("\n", " ")
            snippet = snippet.replace(m.group(), f"【{m.group()}】")   # 用【】標出這個詞
            found.append((a["title"], place, snippet))
    hit_articles += hit

# ---- 印出結果 ----
scope = category or "全部子類別"
print(f"\n「{word}」在 {scope}（{len(articles)} 篇）中：")
print(f"  出現在 {hit_articles} 篇文章（{hit_articles / max(len(articles), 1) * 100:.1f}%）")
print(f"  標題 {counts['標題']} 次、內文 {counts['內文']} 次、留言 {counts['留言']} 次")

if not found:
    sys.exit()

print(f"\n隨機抽 {min(SHOW, len(found))} 段：\n")
for title, place, snippet in random.sample(found, min(SHOW, len(found))):
    print(f"［{place}］{title}")
    print(f"   …{snippet}…\n")
