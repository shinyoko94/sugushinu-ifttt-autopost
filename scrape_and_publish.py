import re, io, os, json, pathlib, datetime as dt, urllib.parse, subprocess, sys
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
from PIL import Image  # ← 画像結合用

# ===== 日本語フォント（豆腐対策）=====
# Actions 側で fonts-noto-cjk を入れてる想定（post.yml に apt-get あり）
rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK JP Regular', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False
# ======================================

VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))        # ← デフォは Top5
RUN_LABEL  = os.getenv("RUN_LABEL", "")         # "AM" / "PM"（手動実行は空）
PUBLIC_DIR = pathlib.Path("public")             # 画像の出力先（リポ直下）

TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]  # 1期 / 2期 見出し

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def parse_votes_by_season(html: str):
    """
    期ごとに『タイトル』 数字を抽出して {"S1":[(title,vote),...], "S2":[...]} を返す
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)

    # 見出しの位置を探してブロック分割
    positions = []
    for p in TITLE_PREFIXES:
        i = text.find(p)
        if i != -1:
            positions.append((i, p))
    positions.sort()
    positions.append((len(text), "END"))

    pat = re.compile(r"『([^』]+)』\s*([0-9]{1,6})")
    out = {"S1": [], "S2": []}
    for i in range(len(positions) - 1):
        start, name = positions[i]
        end, _ = positions[i + 1]
        block = text[start:end]
        items = [(m.group(1).strip(), int(m.group(2))) for m in pat.finditer(block)]
        if name == "吸血鬼すぐ死ぬ":
            out["S1"].extend(items)
        elif name == "吸血鬼すぐ死ぬ２":
            out["S2"].extend(items)
    return out

def pick_top(items, n=5):
    # 票数降順、同数はタイトル昇順
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]

def _short(s: str, n: int = 34) -> str:
    """ラベルが長いときに末尾を省略"""
    return s if len(s) <= n else s[: n - 1] + "…"

def render_image(top_items, caption):
    """
    横棒グラフを生成。各バーの右側に票数（3桁区切り）を描画。
    """
    titles = [f"{i+1}. {_short(t[0])}" for i, t in enumerate(top_items)]
    votes  = [int(t[1]) for t in top_items]
    y = list(range(len(titles)))[::-1]

    fig, ax = plt.subplots(figsize=(10, 7), dpi=220)
    bars = ax.barh(y, votes)  # 色は指定しない
    ax.set_yticks(y)
    ax.set_yticklabels(titles, fontsize=11)
    ax.set_xlabel("Votes", fontsize=11)
    ax.set_title(caption, fontsize=14)
    ax.xaxis.grid(True, linestyle=":", alpha=0.3)

    # 票数ラベルのために右側に余白
    vmax = max(votes) if votes else 0
    ax.set_xlim(0, vmax * 1.15 if vmax > 0 else 1)

    # 各バーの右側に票数を描く（3桁区切り）
    for bar, v in zip(bars, votes):
        ax.text(
            bar.get_width() + (vmax * 0.02 if vmax > 0 else 0.02),
            bar.get_y() + bar.get_height() / 2,
            f"{v:,}",
            va="center", ha="left", fontsize=11
        )

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220)
    plt.close(fig)
    buf.seek(0)
    return buf

def stitch_vertical(img1_bytes: io.BytesIO, img2_bytes: io.BytesIO) -> io.BytesIO:
    """2枚のPNGを縦に結合して1枚のPNGに"""
    img1 = Image.open(img1_bytes).convert("RGBA")
    img2 = Image.open(img2_bytes).convert("RGBA")
    w = max(img1.width, img2.width)
    h = img1.height + img2.height
    canvas = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    canvas.paste(img1, (0, 0))
    canvas.paste(img2, (0, img1.height))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out

def git_commit(filepath: pathlib.Path, msg: str):
    """Actions 内から画像をコミット & push"""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(filepath)], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)

def post_ifttt(text: str, img_url: str):
    """
    IFTTT Webhooks に value1/value2 を直接POST
    （Tweet text={{Value1}}, Image URL={{Value2}} に設定してね）
    """
    key = os.getenv("IFTTT_KEY")
    event = os.getenv("IFTTT_EVENT")
    if not (key and event):
        print("IFTTT_KEY/IFTTT_EVENT 未設定なので送信スキップ", file=sys.stderr)
        return False
    url = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
    r = requests.post(url, json={"value1": text, "value2": img_url}, timeout=30)
    print("IFTTT status:", r.status_code, r.text[:200])
    return r.ok

def main():
    jst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))  # JST
    stamp_full = jst.strftime("%Y/%m/%d %H:%M")
    stamp_day  = jst.strftime("%Y-%m-%d")
    label_ja = "（朝の部）" if RUN_LABEL == "AM" else ("（夜の部）" if RUN_LABEL == "PM" else "")

    html = fetch_html(VOTE_URL)
    by_season = parse_votes_by_season(html)
    if not (by_season["S1"] or by_season["S2"]):
        raise SystemExit("票データが取れませんでした。ページ構造変更の可能性。")

    top_s1 = pick_top(by_season["S1"], TOP_N)
    top_s2 = pick_top(by_season["S2"], TOP_N)

    cap_s1 = f"吸死（1期） 上位{len(top_s1)}（{stamp_full} JST）{label_ja}"
    cap_s2 = f"吸死２（2期） 上位{len(top_s2)}（{stamp_full} JST）{label_ja}"

    img1 = render_image(top_s1, cap_s1)
    img2 = render_image(top_s2, cap_s2)

    # どちらかが空の時はある方だけ、両方あれば縦結合
    if top_s1 and top_s2:
        img = stitch_vertical(img1, img2)
    elif top_s1:
        img = img1
    else:
        img = img2

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    with open(out, "wb") as f:
        f.write(img.read())

    # 公開URL（Publicリポ想定。mainブランチ）
    repo = os.getenv("GITHUB_REPOSITORY")        # owner/repo
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    # 画像コミット
    git_commit(out, f"Add {fname}")

    # 本文
    body = (
        f"【自動集計】吸死アニメ投票 1期Top{len(top_s1)}＋2期Top{len(top_s2)} {label_ja}\n"
        f"{stamp_day} JST 時点\n#吸血鬼すぐ死ぬ #吸死アニメ"
    )

    # IFTTT に直接送信（Value1=本文 / Value2=画像URL）
    post_ifttt(body, img_url)

    # デバッグログ
    print(f"IFTTT_TEXT::{body}")
    print(f"IFTTT_IMG::{img_url}")

if __name__ == "__main__":
    main()
