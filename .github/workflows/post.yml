import re, io, os, json, pathlib, datetime as dt, urllib.parse, subprocess, sys
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams

# ===== 日本語フォントを明示（Actionsで fonts-noto-cjk を入れている想定）=====
rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK JP Regular', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False  # マイナス記号の豆腐防止
# ============================================================================

VOTE_URL  = "https://sugushinu-anime.jp/vote/"
TOP_N     = int(os.getenv("TOP_N", "10"))
RUN_LABEL = os.getenv("RUN_LABEL", "")  # "AM"/"PM"（手動実行は空）
PUBLIC_DIR = pathlib.Path("public")

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def parse_votes(html: str):
    """ページの『タイトル』 数字 をまとめて拾って [(title, votes)] を返す"""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    pat = re.compile(r"『([^』]+)』\s*([0-9]{1,6})")
    return [(m.group(1).strip(), int(m.group(2))) for m in pat.finditer(text)]

def pick_top(items, n=10):
    # 票数降順、同票ならタイトル昇順
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]

def _short(s: str, n: int = 34) -> str:
    """ラベルが長すぎるときは省略（見切れ対策）"""
    return s if len(s) <= n else s[: n - 1] + "…"

def render_image(top_items, caption):
    titles = [f"{i+1}. {_short(t[0])}" for i, t in enumerate(top_items)]
    votes  = [t[1] for t in top_items]
    y = list(range(len(titles)))[::-1]

    fig, ax = plt.subplots(figsize=(10, 7), dpi=220)
    ax.barh(y, votes)  # 色指定なし
    ax.set_yticks(y)
    ax.set_yticklabels(titles, fontsize=11)
    ax.set_xlabel("Votes", fontsize=11)
    ax.set_title(caption, fontsize=14)
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220)
    plt.close(fig)
    buf.seek(0)
    return buf

def git_commit(filepath: pathlib.Path, msg: str):
    """Actions 内から画像をコミット＆push"""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(filepath)], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)

def post_ifttt(text: str, img_url: str):
    """IFTTT Webhooks に value1/value2 を直接POST"""
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
    items = parse_votes(html)
    if not items:
        raise SystemExit("票データが取れませんでした。ページ構造変更の可能性。")

    top = pick_top(items, TOP_N)

    caption = f"吸死アニメ人気エピソード投票 上位{len(top)}（{stamp_full} JST）{label_ja}"
    img = render_image(top, caption)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    with open(out, "wb") as f:
        f.write(img.read())

    # raw画像URL（Publicリポ想定）
    repo = os.getenv("GITHUB_REPOSITORY")        # owner/repo
    ref  = os.getenv("GITHUB_REF_NAME", "main")  # 既定は main
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    # 画像コミット
    git_commit(out, f"Add {fname}")

    # 本文
    body = f"【自動集計】吸死アニメ投票 上位{len(top)} {label_ja}\n{stamp_day} JST 時点\n#吸血鬼すぐ死ぬ #吸死アニメ"

    # IFTTT へ直接送信（Value1=本文 / Value2=画像URL）
    post_ifttt(body, img_url)

    # デバッグログ
    print(f"IFTTT_TEXT::{body}")
    print(f"IFTTT_IMG::{img_url}")

if __name__ == "__main__":
    main()
