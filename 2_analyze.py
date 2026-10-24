"""
2_analyze.py：第 2 步，分析爬下來的文章

做什麼：
  1. 讀資料：data/ 裡的文章和留言
  2. 排除不相關的文章（規則在 config.py 第 6 段），排除原因全部記錄下來
  3. 數面向：每篇文章檢查有沒有出現各面向的詞，算出提及比例
  4. 數品牌：每個品牌被多少比例的文章提到
  5. 找高頻詞：大家最常講的詞，用來檢查詞庫有沒有漏掉什麼
  6. 產出表格和圖，放在 output/ 資料夾

產出檔案：
  01_篩選紀錄.csv          每篇文章保留或排除、排除原因
  02_面向統計.csv          每個品項 × 每個面向的完整數字
  03_Top3_<子類別>.png     每個品項前三名面向＋輔助面向（放簡報）
  03_Top3表格.csv          上面那張圖的數字版
  04_面向長條圖_<子類別>.png 子類別整體的面向比例（放簡報）
  05_品牌統計.csv          各品牌提及篇數與比例
  06_高頻詞.csv            各子類別最常出現的 100 個詞

修改詞庫、排除規則後，只要重跑這支，不用重新爬資料。

執行：python 2_analyze.py
"""
import os
import re
from collections import Counter

import pandas as pd
import matplotlib.pyplot as plt

import config

OUT = "output"
ALL = "全部"   # 代表「整個子類別」的那一列名稱


# =====================================================
# 小工具
# =====================================================
def make_matcher(words):
    """把一組詞變成一個「比對器」，用來檢查一段文字有沒有出現其中任何一個詞。
    英文詞會要求前後不能緊接英文字母，避免 "lg" 被 "blog" 裡的 lg 誤判。"""
    parts = []
    for w in words:
        w = w.lower()
        if re.fullmatch(r"[a-z0-9 .']+", w):
            parts.append(rf"(?<![a-z]){re.escape(w)}(?![a-z])")
        else:
            parts.append(re.escape(w))
    return re.compile("|".join(parts))


def setup_chinese_font():
    """讓圖片可以顯示中文（Mac 內建字型）"""
    plt.rcParams["font.sans-serif"] = ["PingFang TC", "Heiti TC", "Arial Unicode MS",
                                       "Microsoft JhengHei", "Noto Sans CJK TC"]
    plt.rcParams["axes.unicode_minus"] = False


# =====================================================
# 步驟 1：讀資料
# =====================================================
def load_data():
    articles = pd.read_csv("data/ptt_articles.csv")
    comments = pd.read_csv("data/ptt_comments.csv")

    # 同一篇文章的留言全部接成一段文字
    comments["comment"] = comments["comment"].fillna("").astype(str)
    comment_text = comments.groupby("url")["comment"].agg(" ".join)
    articles["comments"] = articles["url"].map(comment_text).fillna("")

    for col in ["title", "content", "comments"]:
        articles[col] = articles[col].fillna("").astype(str)
    articles["date"] = pd.to_datetime(articles["date"], errors="coerce")

    print(f"讀入 {len(articles)} 篇文章、{len(comments)} 則留言")
    return articles


# =====================================================
# 步驟 2：排除不相關的文章
# =====================================================
def why_excluded(row):
    """檢查一篇文章要不要排除；要排除就回傳原因，保留就回傳空字串"""
    start = pd.Timestamp(config.START_DATE)
    if pd.isna(row["date"]) or row["date"] < start:
        return f"早於 {config.START_DATE}"
    if row["url"] in config.MANUAL_EXCLUDE:
        return "人工排除"
    if config.EXCLUDE_INFO_POSTS and "[情報]" in row["title"]:
        return "[情報] 文"
    for w in config.ANALYSIS_EXCLUDE_TITLE.get(row["category"], []):
        if w in row["title"]:
            return f"標題含「{w}」"
    return ""


def filter_articles(articles):
    articles["排除原因"] = articles.apply(why_excluded, axis=1)
    articles["保留"] = articles["排除原因"] == ""

    record = articles[["保留", "排除原因", "category", "items", "board", "date", "title", "url"]]
    record.sort_values(["category", "保留"]).to_csv(
        f"{OUT}/01_篩選紀錄.csv", index=False, encoding="utf-8-sig")

    kept = articles[articles["保留"]].copy()
    print(f"\n排除 {len(articles) - len(kept)} 篇，保留 {len(kept)} 篇（排除原因見 01_篩選紀錄.csv）")
    return kept


# =====================================================
# 步驟 3：數面向
# =====================================================
def check_aspects(kept):
    """幫每篇文章加上每個面向的欄位：有提到 = True，沒提到 = False"""
    kept["text"] = (kept["title"] + " " + kept["content"] + " " + kept["comments"]).str.lower()
    for cat in config.CATEGORIES:
        all_aspects = {**config.ASPECTS[cat], **config.AUX_ASPECTS.get(cat, {})}
        is_cat = kept["category"] == cat
        for aspect, words in all_aspects.items():
            m = make_matcher(words)
            kept.loc[is_cat, f"{cat}:{aspect}"] = kept.loc[is_cat, "text"].apply(lambda t: bool(m.search(t)))
    return kept


def split_by_item(kept):
    """一篇文章可能屬於好幾個品項（例如「飼料|罐頭」），拆成每個品項各一列"""
    rows = kept.assign(item=kept["items"].astype(str).str.split("|")).explode("item")
    return rows


def aspect_table(kept, by_item):
    """算出每個品項（以及整個子類別）每個面向的提及篇數和比例"""
    records = []
    for cat, items in config.CATEGORIES.items():
        groups = [(item, by_item[(by_item["category"] == cat) & (by_item["item"] == item)])
                  for item in items]
        groups.append((ALL, kept[kept["category"] == cat]))      # 整個子類別，每篇只算一次

        for item, sub in groups:
            n = len(sub)
            if n == 0:
                continue
            for kind, aspects in [("主要", config.ASPECTS[cat]), ("輔助", config.AUX_ASPECTS.get(cat, {}))]:
                for aspect in aspects:
                    hit = int(sub[f"{cat}:{aspect}"].sum())
                    records.append({"子類別": cat, "品項": item, "面向": aspect, "類型": kind,
                                    "提及篇數": hit, "文章數": n,
                                    "提及比例(%)": round(hit / n * 100, 1)})
    table = pd.DataFrame(records)
    table.to_csv(f"{OUT}/02_面向統計.csv", index=False, encoding="utf-8-sig")
    return table


# =====================================================
# 步驟 6（面向的圖）：Top 3 表格、長條圖
# =====================================================
def top3_table(table):
    rows = []
    for cat, items in config.CATEGORIES.items():
        # 子類別只有一個品項時（例如電視），「全部」那列跟品項那列一樣，就不重複列
        names = list(items) + ([ALL] if len(items) > 1 else [])
        for item in names:
            sub = table[(table["子類別"] == cat) & (table["品項"] == item)]
            if sub.empty:
                continue
            main = sub[(sub["類型"] == "主要") & (sub["提及篇數"] > 0)].sort_values("提及比例(%)", ascending=False)
            top = [f"{r['面向']} {r['提及比例(%)']}%" for _, r in main.head(3).iterrows()]
            top += [""] * (3 - len(top))
            aux = sub[sub["類型"] == "輔助"]
            aux_text = "、".join(f"{r['面向']} {r['提及比例(%)']}%" for _, r in aux.iterrows())
            rows.append({"子類別": cat, "品項": item, "文章數": int(sub["文章數"].iloc[0]),
                         "Top 1": top[0], "Top 2": top[1], "Top 3": top[2], "輔助面向": aux_text})
    result = pd.DataFrame(rows)
    result.to_csv(f"{OUT}/03_Top3表格.csv", index=False, encoding="utf-8-sig")

    # 每個子類別畫一張表格圖
    for cat in config.CATEGORIES:
        t = result[result["子類別"] == cat].drop(columns="子類別")
        if t.empty:
            continue
        fig, ax = plt.subplots(figsize=(15, 0.6 * len(t) + 1.2))
        ax.axis("off")
        tbl = ax.table(cellText=t.values, colLabels=t.columns, cellLoc="center", loc="center",
                       colWidths=[.10, .08, .19, .19, .19, .25])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(11)
        tbl.scale(1, 2.3)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#D9E2F3")
            if r == 0:
                cell.set_facecolor("#DCE6F1")
                cell.set_text_props(weight="bold")
            elif r % 2 == 0:
                cell.set_facecolor("#F5F8FC")
        fig.savefig(f"{OUT}/03_Top3_{cat}.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    print("\n===== 各品項前三名面向 =====")
    print(result.to_string(index=False))
    small = result[(result["文章數"] < 30) & (result["品項"] != ALL)]
    for _, r in small.iterrows():
        print(f"⚠️ {r['子類別']}／{r['品項']} 只有 {r['文章數']} 篇，比例僅供參考")


def aspect_bar_chart(table):
    for cat in config.CATEGORIES:
        d = table[(table["子類別"] == cat) & (table["品項"] == ALL) & (table["類型"] == "主要")]
        if d.empty:
            continue
        d = d.sort_values("提及比例(%)")
        fig, ax = plt.subplots(figsize=(9, 0.45 * len(d) + 1.5))
        bars = ax.barh(d["面向"], d["提及比例(%)"], color="#4C72B0")
        ax.bar_label(bars, fmt="%.1f%%", padding=4, fontsize=10)
        ax.set_title(f"{cat}：各面向提及比例（共 {int(d['文章數'].iloc[0])} 篇）", fontsize=13, fontweight="bold")
        ax.set_xlabel("提到這個面向的文章比例 (%)")
        ax.set_xlim(0, min(100, d["提及比例(%)"].max() * 1.2 + 5))
        ax.spines[["top", "right"]].set_visible(False)
        fig.savefig(f"{OUT}/04_面向長條圖_{cat}.png", dpi=300, bbox_inches="tight")
        plt.close(fig)


# =====================================================
# 步驟 4：數品牌
# =====================================================
def brand_table(kept):
    records = []
    for cat, brands in config.BRANDS.items():
        sub = kept[kept["category"] == cat]
        for brand, names in brands.items():
            m = make_matcher(names)
            hit = int(sub["text"].apply(lambda t: bool(m.search(t))).sum())
            records.append({"子類別": cat, "品牌": brand, "提及篇數": hit, "文章數": len(sub),
                            "提及比例(%)": round(hit / len(sub) * 100, 1) if len(sub) else 0})
    result = pd.DataFrame(records).sort_values(["子類別", "提及篇數"], ascending=[True, False])
    result.to_csv(f"{OUT}/05_品牌統計.csv", index=False, encoding="utf-8-sig")
    print("\n===== 品牌提及前 5 名 =====")
    for cat, g in result.groupby("子類別"):
        print(f"{cat}：" + "、".join(f"{r['品牌']} {r['提及比例(%)']}%" for _, r in g.head(5).iterrows()))


# =====================================================
# 步驟 5：高頻詞
# =====================================================
def top_words(kept, n=100):
    try:
        import jieba
    except ImportError:
        print("\n（沒有安裝 jieba，跳過高頻詞。要的話：pip install jieba）")
        return
    jieba.setLogLevel(60)
    for items in config.CATEGORIES.values():          # 讓斷詞認得搜尋詞
        for words in items.values():
            for w in words:
                jieba.add_word(w)

    records = []
    for cat in config.CATEGORIES:
        counter = Counter()
        for t in kept.loc[kept["category"] == cat, "text"]:
            t = re.sub(r"https?://\S+", " ", t)        # 去掉網址
            for w in jieba.lcut(t):
                w = w.strip()
                if len(w) < 2 or w in config.STOPWORDS:
                    continue
                if not (re.search(r"[一-鿿]", w) or w.isalpha()):   # 只留中文詞或英文單字
                    continue
                counter[w] += 1
        for w, c in counter.most_common(n):
            records.append({"子類別": cat, "詞": w, "次數": c})
    pd.DataFrame(records).to_csv(f"{OUT}/06_高頻詞.csv", index=False, encoding="utf-8-sig")


# =====================================================
# 主流程
# =====================================================
def main():
    os.makedirs(OUT, exist_ok=True)
    setup_chinese_font()

    articles = load_data()                 # 步驟 1
    kept = filter_articles(articles)       # 步驟 2
    kept = check_aspects(kept)             # 步驟 3
    by_item = split_by_item(kept)

    print("\n各品項文章數（一篇屬於兩個品項會各算一次）：")
    for (cat, item), g in by_item.groupby(["category", "item"]):
        print(f"  {cat}／{item}：{len(g)} 篇")

    table = aspect_table(kept, by_item)
    top3_table(table)
    aspect_bar_chart(table)
    brand_table(kept)                      # 步驟 4
    top_words(kept)                        # 步驟 5

    print(f"\n✅ 完成！結果都在 {OUT}/ 資料夾")


if __name__ == "__main__":
    main()
