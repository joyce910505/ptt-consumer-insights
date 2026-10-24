"""
1_scrape.py：第 1 步，從 PTT 爬文章和留言

做什麼：
  照著 config.py 的清單，到每個看板搜尋每個搜尋詞。
  從最新的文章往前翻，翻到比 START_DATE 還舊的文章就停。
  把文章和留言存成兩個 CSV：
    data/ptt_articles.csv   一篇文章一列
    data/ptt_comments.csv   一則留言一列（用 url 對回是哪篇文章的留言）

中途停掉也沒關係：
  每搜完一組就會存檔。再執行一次，已經抓過的文章會跳過，不會重抓。

執行：python 1_scrape.py
"""
import os
import re
import time
import random
from datetime import datetime

import requests
import pandas as pd
from bs4 import BeautifulSoup

import config

PTT = "https://www.ptt.cc"
START = datetime.strptime(config.START_DATE, "%Y-%m-%d")

# 跟 PTT 連線的設定：假裝是一般瀏覽器，並先通過「已滿 18 歲」確認
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})
session.cookies.set("over18", "1")


# =====================================================
# 小工具
# =====================================================
def rest():
    """每次連到 PTT 後休息 1.5～3 秒，太快會被 PTT 擋"""
    time.sleep(random.uniform(1.5, 3.0))


def get_page(url, params=None):
    """打開一個網頁。連不上會自動重試 3 次（等 5、15、30 秒），還是不行就放棄"""
    for wait in [5, 15, 30]:
        try:
            r = session.get(url, params=params, timeout=15)
            rest()
            return r
        except requests.exceptions.RequestException:
            print(f"    ⚠️ 連線失敗，{wait} 秒後重試")
            time.sleep(wait)
    print(f"    ❌ 放棄：{url}")
    return None


def date_from_url(url):
    """從文章網址讀出發文時間。
    PTT 網址長這樣：.../M.1791549222.A.317.html，中間那串數字就是發文時間"""
    m = re.search(r"/M\.(\d+)\.", url)
    return datetime.fromtimestamp(int(m.group(1))) if m else None


def add_label(old, new):
    """把新的品項加進去，例如「飼料」加上「罐頭」變成「飼料|罐頭」；已經有了就不重複加"""
    labels = [x for x in str(old).split("|") if x and x != "nan"]
    if new not in labels:
        labels.append(new)
    return "|".join(labels)


# =====================================================
# 步驟 A：搜尋，收集文章網址
# =====================================================
def search(board, keyword, max_pages=None):
    """在看板裡搜尋關鍵字，從最新往舊一頁一頁翻，翻到 START_DATE 之前就停。
    回傳 [(標題, 網址), ...]"""
    found = []
    skipped = 0     # 標題有排除詞而跳過的篇數
    pages_read = 0  # 實際讀到內容的頁數
    page = 0
    while True:
        page += 1
        if max_pages and page > max_pages:       # 只有測試模式會限制頁數
            break
        r = get_page(f"{PTT}/bbs/{board}/search", params={"q": keyword, "page": page})
        if r is None or r.status_code != 200:
            if page == 1:
                print(f"    ⚠️ 搜不到東西，請確認看板名稱「{board}」是否正確")
            break

        rows = BeautifulSoup(r.text, "html.parser").select("div.r-ent")
        if not rows:                              # 沒有下一頁了
            break
        pages_read += 1

        reached_start = False
        for row in rows:
            link = row.select_one("div.title a")
            if not link:                          # 被刪除的文章沒有連結
                continue
            title, url = link.text.strip(), PTT + link["href"]
            posted = date_from_url(url)
            if posted and posted < START:         # 比起始日期還舊
                reached_start = True
                continue
            if any(w in title for w in config.TITLE_EXCLUDE):
                skipped += 1
                continue
            found.append((title, url))

        if page % 10 == 0:
            print(f"    …已翻到第 {page} 頁")
        if reached_start:                         # 這頁已經出現太舊的文章，後面只會更舊
            break

    print(f"    翻了 {pages_read} 頁，找到 {len(found)} 篇，標題含排除詞跳過 {skipped} 篇")
    return found


# =====================================================
# 步驟 B：打開文章，抓內容和留言
# =====================================================
def read_article(url):
    """打開一篇文章，拆出作者、時間、內文、留言"""
    r = get_page(url)
    if r is None or r.status_code != 200:
        return None
    main = BeautifulSoup(r.text, "html.parser").select_one("#main-content")
    if main is None:
        return None

    # 文章最上面的「作者 / 標題 / 時間」
    info = {}
    for line in main.select("div.article-metaline"):
        tag = line.select_one(".article-meta-tag")
        value = line.select_one(".article-meta-value")
        if tag and value:
            info[tag.text] = value.text

    # 留言（推、噓、→）
    comments = []
    for c in main.select("div.push"):
        tag = c.select_one(".push-tag")
        user = c.select_one(".push-userid")
        text = c.select_one(".push-content")
        if tag and user and text:
            comments.append({"type": tag.text.strip(),
                             "user": user.text.strip(),
                             "comment": text.text.lstrip(": ").strip()})

    # 把標頭和留言拿掉，剩下的就是內文；「※ 發信站」之後是簽名檔，不要
    for el in main.select("div.article-metaline, div.article-metaline-right, div.push"):
        el.decompose()
    content = main.get_text().split("※ 發信站")[0].strip()

    return {"author": info.get("作者", ""), "time": info.get("時間", ""),
            "content": content, "comments": comments}


# =====================================================
# 存檔與讀檔
# =====================================================
def load(path):
    """讀回上次存的 CSV；檔案不存在或是空的，就當作沒有資料"""
    if not os.path.exists(path):
        return []
    try:
        return pd.read_csv(path).to_dict("records")
    except pd.errors.EmptyDataError:
        return []


def save(articles, comments, articles_file, comments_file):
    pd.DataFrame(list(articles.values())).to_csv(articles_file, index=False, encoding="utf-8-sig")
    pd.DataFrame(comments).to_csv(comments_file, index=False, encoding="utf-8-sig")


# =====================================================
# 主流程
# =====================================================
def main():
    os.makedirs("data", exist_ok=True)

    if config.TEST_MODE:
        # 測試模式：只跑一個小組合，存到另外的檔案，不影響正式資料
        categories = {"電視機": {"電視": ["電視"]}}
        boards = {"電視機": ["E-appliance"]}
        max_pages = 3
        articles_file, comments_file = "data/test_articles.csv", "data/test_comments.csv"
        print("🧪 測試模式（只搜家電版「電視」，翻 3 頁）\n")
    else:
        categories, boards, max_pages = config.CATEGORIES, config.BOARDS, None
        articles_file, comments_file = "data/ptt_articles.csv", "data/ptt_comments.csv"
        print(f"🚀 正式爬取（{config.START_DATE} 之後的文章）\n")

    # 讀回上次的進度。articles 用「網址 → 文章」存，方便查有沒有抓過
    articles = {a["url"]: a for a in load(articles_file)}
    comments = load(comments_file)
    if articles:
        print(f"📂 接續上次進度：已有 {len(articles)} 篇文章\n")

    try:
        for category, items in categories.items():
            for board in boards[category]:
                for item, keywords in items.items():
                    for kw in keywords:
                        print(f"[{category}] {board} 版 / {item} / 搜「{kw}」")
                        results = search(board, kw, max_pages)

                        new = 0
                        for title, url in results:
                            # 已經抓過的文章：不重抓，只補記這次的品項和搜尋詞
                            if url in articles:
                                articles[url]["items"] = add_label(articles[url]["items"], item)
                                articles[url]["keywords"] = add_label(articles[url]["keywords"], kw)
                                continue

                            data = read_article(url)
                            if data is None:
                                continue
                            new += 1
                            if new % 10 == 0:
                                print(f"    …已抓 {new} 篇新文章")

                            articles[url] = {
                                "category": category,
                                "items": item,
                                "keywords": kw,
                                "board": board,
                                "date": date_from_url(url).strftime("%Y-%m-%d") if date_from_url(url) else "",
                                "title": title,
                                "author": data["author"],
                                "content": data["content"],
                                "comment_count": len(data["comments"]),
                                "url": url,
                            }
                            for c in data["comments"]:
                                comments.append({"url": url, **c})

                        print(f"    新抓 {new} 篇\n")
                        save(articles, comments, articles_file, comments_file)   # 每搜完一組就存檔
    finally:
        # 不管是正常結束還是中途按 Ctrl+C，都會先存檔
        save(articles, comments, articles_file, comments_file)
        print(f"完成！共 {len(articles)} 篇文章、{len(comments)} 則留言")
        print(f"存在：{articles_file}、{comments_file}")


if __name__ == "__main__":
    main()
