import re, io, os, pathlib, datetime as dt, urllib.parse, subprocess, sys, textwrap
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
from PIL import Image  # 画像結合

# ===== 日本語フォント（豆腐対策）=====
rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK JP Regular', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False
# ======================================

VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))        # Top5
RUN_LABEL  = os.getenv("RUN_LABEL", "")         # "AM" / "PM"（手動実行は空）
PUBLIC_DIR = pathlib.Path("public")

TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]  # 1期 / 2期 見出し

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def parse_votes_by_season(html: str):
    """期ごとに『タイトル』 数字を抽出 → {"S1":[(title, vote),...], "S2":[...]}"""
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
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]

def _wrap(s: str, width: int = 18, max_lines: int = 2) -> str:
    """タイトルをいい感じに折り返し（最大2行）"""
    lines = textwrap.wrap(s, width=width)
    lines = lines[:max_lines]
    if len(lines) == max_lines and len(s) > sum(len(x) for x in lines):
        lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines)

def render_image(top_items, caption, bar_color=None):
    """
    横棒グラフ。各バー右に票数（3桁区切り）。タイトルは改行で折り返し。
    bar_color: 例 'tab:orange' / '#7e57c2'
    """
    titles = [f"{i+1}. {_wrap(t[0])}" for i, t in enumerate(top_items)]
    votes  = [int(t[1]) for t in top_items]
    y = list(range(len(titles)))[::-1]

    fig, ax = plt.subplots(figsize=(10, 7), dpi=220)
    bars = ax.barh(y, votes, color=bar_color)
    ax.set_yticks(y)
    ax.set_yticklabels(titles, fontsize=11)
    ax.set_xlabel("Votes", fontsize=11)
    ax.set_title(caption, fontsize=14)
    ax.xaxis.grid(True, linestyle=":", alpha=0.3)

    vmax = max(votes) if votes else 0
    ax.set_xlim(0, vmax * 1.18 if vmax > 0 else 1)

    for bar, v in zip(bars, votes):
        ax.text(
            bar.get_width() + (vmax * 0.02 if vmax > 0 else 0.02),
            bar.get_y() + bar.get_height() / 2,
            f"{v:,}",
            va="center", ha="left", fontsize=11
        )

    # 折り返し分の左余白を確保
    plt.subplots_adjust(left=0.33)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220)
    plt.close(fig)
    buf.seek(0)
    return buf

def stitch_vertical(img1_bytes: io.BytesIO, img2_bytes: io.BytesIO) -> io.BytesIO:
    """2枚のPNGを縦結合して1枚に"""
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
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(filepath)], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)

def post_ifttt(text: str, img_url: str):
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
    month_day  = jst.strftime("%m/%d")
    # 24時間表記で統一
    time_label = "8:00時点" if RUN_LABEL == "AM" else ("20:00時点" if RUN_LABEL == "PM" else jst.strftime("%H:%M時点"))
    label_ja   = "（朝の部）" if RUN_LABEL=="AM" else ("（夜の部）" if RUN_LABEL=="PM" else "")

    html = fetch_html(VOTE_URL)
    by_season = parse_votes_by_season(html)
    if not (by_season["S1"] or by_season["S2"]):
        raise SystemExit("票データが取れませんでした。")

    top_s1 = pick_top(by_season["S1"], TOP_N)
    top_s2 = pick_top(by_season["S2"], TOP_N)

    cap_s1 = f"吸死（1期） 上位{len(top_s1)}（{stamp_full} JST）{label_ja}"
    cap_s2 = f"吸死２（2期） 上位{len(top_s2)}（{stamp_full} JST）{label_ja}"

    # 1期=オレンジ、2期=紫
    img1 = render_image(top_s1, cap_s1, bar_color='tab:orange')
    img2 = render_image(top_s2, cap_s2, bar_color='#7e57c2')
    img  = stitch_vertical(img1, img2) if (top_s1 and top_s2) else (img1 or img2)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    with open(out, "wb") as f:
        f.write(img.read())

    # 公開URL（Public / mainブランチ想定）
    repo = os.getenv("GITHUB_REPOSITORY")
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    git_commit(out, f"Add {fname}")

    # 🐦ツイート文面（24時間表記）
    body = (
        f"🗳️エピソード投票中間結果発表（{month_day} {time_label}）🗳️\n"
        f"投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n"
        f"#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )

    post_ifttt(body, img_url)

    # デバッグ出力
    print(f"IFTTT_TEXT::{body}")
    print(f"IFTTT_IMG::{img_url}")

if __name__ == "__main__":
    main()
