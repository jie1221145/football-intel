#!/usr/bin/env python3
"""
Football Intel Scraper — 免费版
无需 API Key，使用规则引擎分析新闻
支持：BBC Sport Football + Guardian Football
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
from html.parser import HTMLParser

# ─────────────────────────────────────────
# 数据源配置
# ─────────────────────────────────────────
SOURCES = {
    "bbc": {
        "name": "BBC Sport",
        "url": "https://www.bbc.com/sport/football",
        "base": "https://www.bbc.com",
    },
    "guardian": {
        "name": "The Guardian",
        "url": "https://www.theguardian.com/football",
        "base": "https://www.theguardian.com",
    },
}

# ─────────────────────────────────────────
# 关键词分类规则
# ─────────────────────────────────────────
TRANSFER_KW = [
    "transfer", "sign", "signing", "signed", "deal", "fee", "bid",
    "move", "loan", "contract", "window", "rumour", "rumor", "target",
    "pursue", "approach", "agree", "complete", "unveil", "medical",
    "£", "€", "million", "wage", "sell", "buy", "swap",
]
MATCH_KW = [
    "match report", "result", "win", "wins", "lost", "loss", "draw",
    "goal", "goals", "defeat", "victory", "beat", "beats", "vs",
    "score", "final", "fixture", "minute", "penalty", "hat-trick",
    "equaliser", "comeback", "stunner", "thriller",
]
INJURY_KW = [
    "injury", "injured", "ruled out", "fitness", "doubt", "return",
    "surgery", "hamstring", "knee", "ankle", "suspension", "suspended",
    "ban", "banned", "miss", "misses", "sidelined", "blow", "setback",
    "scan", "assessment", "recovery",
]
NOISE_KW = [
    "quiz", "fantasy", "podcast", "gallery", "photo", "video",
    "watch:", "live blog", "opinion:", "column:", "how to",
    "best xi", "rated:", "review:", "preview:", "ranked:",
]

# 转会可靠度关键词 → 吃瓜指数
RUMOUR_SIGNALS = {
    5: ["official", "confirmed", "completed", "announces", "unveiled", "signs", "signed for"],
    4: ["agree", "agreed", "personal terms", "medical", "close to", "set to sign", "imminent"],
    3: ["in talks", "negotiations", "bid accepted", "fee agreed", "interested"],
    2: ["eyeing", "considering", "monitoring", "linked", "could", "might", "may"],
    1: ["rumour", "rumored", "speculation", "sources claim", "reportedly", "whispers"],
}
RUMOUR_LABELS = {5: "官方确认", 4: "接近官宣", 3: "可靠消息", 2: "野史流言", 1: "纯属猜测"}

# 球队 → emoji 映射
TEAM_EMOJI = {
    "arsenal": "🔴", "chelsea": "🔵", "liverpool": "🔴",
    "manchester city": "🩵", "man city": "🩵",
    "manchester united": "🔴", "man united": "🔴", "man utd": "🔴",
    "tottenham": "⚪", "spurs": "⚪",
    "newcastle": "⚫", "aston villa": "🟣", "west ham": "⚒️",
    "barcelona": "🔵", "real madrid": "⚪", "atletico": "🔴",
    "psg": "🔵", "juventus": "⚫", "inter": "🔵", "ac milan": "🔴",
    "bayern": "🔴", "dortmund": "🟡",
    "transfer": "💰", "injury": "🚑", "match": "⚽",
}

OUTPUT_DIR = Path(__file__).parent.parent / "web"


# ─────────────────────────────────────────
# HTML 解析器
# ─────────────────────────────────────────
class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []
        self._in_a = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            if d.get("href"):
                self._href = d["href"]
                self._text = []
                self._in_a = True

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            text = re.sub(r'\s+', ' ', " ".join(self._text)).strip()
            if self._href and len(text) > 20:
                self.links.append({"href": self._href, "text": text})
            self._in_a = False
            self._href = None

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data.strip())


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            cs = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(cs, errors="replace")
    except Exception as e:
        print(f"  ⚠ {url}: {e}", file=sys.stderr)
        return ""


# ─────────────────────────────────────────
# 分类 & 过滤
# ─────────────────────────────────────────
def classify(title: str) -> str | None:
    t = title.lower()
    if any(k in t for k in NOISE_KW):
        return None
    if any(k in t for k in TRANSFER_KW):
        return "transfer"
    if any(k in t for k in MATCH_KW):
        return "match_report"
    if any(k in t for k in INJURY_KW):
        return "injury"
    return None


# ─────────────────────────────────────────
# 规则引擎分析（替代 AI）
# ─────────────────────────────────────────
def pick_emoji(title: str) -> str:
    t = title.lower()
    for team, em in TEAM_EMOJI.items():
        if team in t:
            return em
    return "⚽"


def gossip_rating(title: str) -> tuple[int, str]:
    t = title.lower()
    for score in [5, 4, 3, 2, 1]:
        if any(sig in t for sig in RUMOUR_SIGNALS[score]):
            return score, RUMOUR_LABELS[score]
    return 2, RUMOUR_LABELS[2]


def extract_players(title: str) -> list[str]:
    """提取大写开头的人名/球队名（简单启发式）"""
    # 匹配 2-3 个连续首字母大写的词
    words = re.findall(r'\b[A-Z][a-záéíóúàèìòùñ]+(?:\s+[A-Z][a-záéíóúàèìòùñ]+){0,2}\b', title)
    # 过滤太短或太常见的词
    skip = {"The", "BBC", "Man", "Cup", "Premier", "League", "Champions", "Europa",
            "FA", "EFL", "Sky", "BT", "After", "How", "Why", "What", "When", "For"}
    seen = set()
    result = []
    for w in words:
        if w not in skip and w not in seen and len(w) > 3:
            result.append(w)
            seen.add(w)
    return result[:4]


def build_core_event(title: str, category: str, source: str) -> str:
    """根据标题生成核心事件描述"""
    t = title.lower()
    if category == "transfer":
        if any(s in t for s in RUMOUR_SIGNALS[5]):
            return f"【官方】{source} 报道此次转会已正式完成。{title}"
        elif any(s in t for s in RUMOUR_SIGNALS[4]):
            return f"据 {source}，双方谈判进入最终阶段，签约进展顺利。{title}"
        elif any(s in t for s in RUMOUR_SIGNALS[3]):
            return f"{source} 报道，转会谈判正在进行中，细节尚待确认。{title}"
        else:
            return f"{source} 披露这一转会传言，可靠度有待核实。{title}"
    elif category == "match_report":
        return f"来自 {source} 的赛事报道。{title}"
    else:
        return f"{source} 报道了这一伤病 / 停赛动态。{title}"


def build_impact(title: str, category: str) -> str:
    """生成战术/财务简评"""
    t = title.lower()
    if category == "transfer":
        if any(x in t for x in ["£100m", "£90m", "£80m", "€100m", "€120m"]):
            impact = "本次转会金额巨大，将对俱乐部的财政健康和 FFP 合规性构成压力。"
        elif any(x in t for x in ["loan", "temporary"]):
            impact = "租借操作灵活，不占永久薪资配额，是一种低风险补强策略。"
        elif any(x in t for x in ["free", "released", "out of contract"]):
            impact = "自由转会无需支付转会费，仅需考虑薪资成本，性价比极高。"
        else:
            impact = "此次引援将直接影响球队阵容深度和战术灵活性，具体影响视价格和球员定位而定。"
    elif category == "match_report":
        if any(x in t for x in ["3-0", "4-0", "4-1", "5-0", "5-1"]):
            impact = "大比分差距清晰揭示了两队实力分野，失利方需重审战术部署。"
        elif any(x in t for x in ["1-0", "2-1", "2-0"]):
            impact = "紧张的比分说明双方实力接近，细节决定胜负，临场调整至关重要。"
        elif "draw" in t or "1-1" in t or "0-0" in t:
            impact = "平局结果对积分榜的影响需结合其他竞争对手的战绩综合判断。"
        else:
            impact = "本场比赛结果将对积分榜格局产生影响，需关注后续赛程安排。"
    else:  # injury
        if any(x in t for x in ["ruled out", "surgery", "months"]):
            impact = "长期缺阵将迫使主帅调整主力阵容，考验替补球员的临场发挥。"
        elif any(x in t for x in ["doubt", "fitness", "assessment"]):
            impact = "球员伤势尚未明朗，主帅需在赛前做好两手准备，保持战术弹性。"
        else:
            impact = "伤病将影响球队短期人员配置，主帅需在接下来的赛程中合理轮换。"
    return impact


def analyse(articles: list[dict]) -> list[dict]:
    enriched = []
    for art in articles:
        title = art["title"]
        cat = art["category"]
        rating, label = (gossip_rating(title) if cat == "transfer" else (None, None))
        enriched.append({
            **art,
            "emoji_tag": pick_emoji(title),
            "core_event": build_core_event(title, cat, art["source"]),
            "tactical_financial_impact": build_impact(title, cat),
            "gossip_rating": rating,
            "gossip_label": label,
            "key_players": extract_players(title),
        })
    return enriched


# ─────────────────────────────────────────
# 爬虫
# ─────────────────────────────────────────
def scrape(key: str, cfg: dict) -> list[dict]:
    print(f"🌐 抓取 {cfg['name']}...")
    html = fetch(cfg["url"])
    if not html:
        return []

    parser = LinkExtractor()
    parser.feed(html)

    articles, seen = [], set()
    for lnk in parser.links:
        href, title = lnk["href"], lnk["text"]
        if href.startswith("/"):
            href = cfg["base"] + href
        if not href.startswith("http") or title in seen or len(title) < 25:
            continue
        seen.add(title)
        cat = classify(title)
        if not cat:
            continue
        articles.append({"source": cfg["name"], "title": title, "url": href, "category": cat})

    print(f"  ✓ 找到 {len(articles)} 条相关文章")
    return articles[:15]


# ─────────────────────────────────────────
# HTML 渲染
# ─────────────────────────────────────────
def render_html(articles: list[dict], generated_at: str) -> str:
    all_json = json.dumps(articles, ensure_ascii=False)
    total = len(articles)
    cnt = {c: sum(1 for a in articles if a["category"] == c)
           for c in ["transfer", "match_report", "injury"]}

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FOOTBALL INTEL // {generated_at[:10]}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;600&family=Playfair+Display:wght@700;900&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --green:#1a7c3e;--green-light:#22a052;--green-dark:#0f4d26;
  --carbon:#0e0e0e;--carbon-2:#1a1a1a;--carbon-3:#252525;
  --yellow:#e8ff00;--yellow-dim:#c8dd00;
  --white:#f5f5f0;--grey:#888;--red:#e03535;
  --fd:'Bebas Neue',Impact,sans-serif;
  --fe:'Playfair Display',serif;
  --fb:'Inter',sans-serif;
  --fm:'IBM Plex Mono',monospace;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--carbon);color:var(--white);font-family:var(--fb);line-height:1.5}}
.masthead{{background:var(--carbon);border-bottom:4px solid var(--yellow)}}
.mi{{max-width:1200px;margin:0 auto;padding:24px 24px 0}}
.mt{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}}
.pn{{font-family:var(--fd);font-size:clamp(48px,8vw,96px);line-height:.9;color:var(--white)}}
.pn span{{color:var(--yellow)}}
.mm{{text-align:right;font-family:var(--fm);font-size:11px;color:var(--grey);letter-spacing:1px;padding-bottom:4px}}
.mm strong{{color:var(--yellow);display:block;font-size:13px;margin-bottom:2px}}
.mnav{{display:flex;margin-top:16px;border-top:1px solid #333}}
.nt{{padding:10px 20px;font-family:var(--fm);font-size:11px;font-weight:600;letter-spacing:2px;text-transform:uppercase;cursor:pointer;border:none;background:transparent;color:var(--grey);border-bottom:3px solid transparent;transition:all .2s}}
.nt:hover{{color:var(--white)}}
.nt.active{{color:var(--yellow);border-bottom-color:var(--yellow)}}
.nc{{background:var(--green);color:var(--white);font-size:9px;padding:1px 5px;border-radius:2px;margin-left:6px}}
.main{{max-width:1200px;margin:0 auto;padding:32px 24px 80px}}
.sec{{display:none}}.sec.active{{display:block}}
.cg{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:2px;background:#222}}
.card{{background:var(--carbon-2);padding:24px;cursor:pointer;transition:background .15s;border-top:3px solid transparent}}
.card:hover{{background:var(--carbon-3)}}
.card.transfer{{border-top-color:var(--yellow)}}
.card.match_report{{border-top-color:var(--green-light)}}
.card.injury{{border-top-color:var(--red)}}
.ch{{display:flex;align-items:flex-start;gap:10px;margin-bottom:12px}}
.ce{{font-size:28px;line-height:1;flex-shrink:0}}
.cs{{font-family:var(--fm);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--grey);margin-bottom:2px}}
.cb{{display:inline-block;font-family:var(--fm);font-size:9px;font-weight:600;letter-spacing:1px;padding:2px 6px;text-transform:uppercase}}
.cat-transfer{{background:var(--yellow);color:var(--carbon)}}
.cat-match_report{{background:var(--green-light);color:var(--white)}}
.cat-injury{{background:var(--red);color:var(--white)}}
.ct{{font-family:var(--fe);font-size:18px;font-weight:700;line-height:1.3;color:var(--white);margin-bottom:10px}}
.csum{{font-size:13px;color:#aaa;line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.gs{{display:flex;gap:3px;margin-top:12px;align-items:center}}
.star{{font-size:13px}}.star.on{{color:var(--yellow)}}.star.off{{color:#333}}
.gl{{font-family:var(--fm);font-size:10px;color:var(--grey);margin-left:8px;letter-spacing:1px}}
.cf{{display:flex;justify-content:space-between;align-items:center;margin-top:14px;padding-top:12px;border-top:1px solid #2a2a2a}}
.ctags{{display:flex;flex-wrap:wrap;gap:4px}}
.tag{{font-family:var(--fm);font-size:9px;background:#1e2a1e;color:var(--green-light);padding:2px 6px;border-radius:2px}}
.rm{{font-family:var(--fm);font-size:10px;color:var(--yellow-dim);letter-spacing:1px;text-decoration:none}}
.rm:hover{{color:var(--yellow)}}
.mo{{position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px;opacity:0;visibility:hidden;transition:all .2s;backdrop-filter:blur(4px)}}
.mo.open{{opacity:1;visibility:visible}}
.md{{background:var(--carbon-2);border:1px solid #333;border-top:4px solid var(--yellow);max-width:660px;width:100%;max-height:90vh;overflow-y:auto;transform:translateY(20px);transition:transform .2s;position:relative}}
.mo.open .md{{transform:translateY(0)}}
.mh{{background:linear-gradient(135deg,var(--green-dark) 0%,#0a0a0a 100%);padding:28px 28px 20px;border-bottom:2px solid #1e3d1e;position:relative}}
.mc{{position:absolute;top:16px;right:16px;background:transparent;border:1px solid #444;color:var(--grey);width:32px;height:32px;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center;transition:all .2s}}
.mc:hover{{border-color:var(--yellow);color:var(--yellow)}}
.ml{{font-family:var(--fm);font-size:10px;letter-spacing:3px;color:var(--green-light);text-transform:uppercase;margin-bottom:10px}}
.mtt{{font-family:var(--fe);font-size:clamp(20px,3vw,28px);font-weight:900;line-height:1.2;color:var(--white)}}
.mb{{padding:28px}}
.bs{{margin-bottom:24px}}
.bh{{font-family:var(--fm);font-size:10px;font-weight:600;letter-spacing:3px;text-transform:uppercase;color:var(--yellow);margin-bottom:8px;display:flex;align-items:center;gap:8px}}
.bh::after{{content:'';flex:1;height:1px;background:#2a2a2a}}
.bt{{font-size:14px;line-height:1.7;color:#ccc}}
.tb{{background:var(--carbon-3);border-left:3px solid var(--green-light);padding:16px 18px;font-size:14px;line-height:1.7;color:#ccc}}
.pv{{background:radial-gradient(ellipse at center,#1a5c2a 0%,var(--green-dark) 60%,#0a2010 100%);border:2px solid #1e3d1e;border-radius:4px;padding:20px;margin-bottom:20px;text-align:center;position:relative;min-height:90px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px}}
.pp{{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}}
.pc{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:var(--white);font-family:var(--fm);font-size:11px;padding:4px 10px;border-radius:20px}}
.pc2{{width:60px;height:60px;border:2px solid rgba(255,255,255,.3);border-radius:50%;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);opacity:.4}}
.sr{{display:flex;align-items:center;gap:12px;padding:16px 18px;background:rgba(232,255,0,.04);border:1px solid rgba(232,255,0,.1);margin-top:20px}}
.sl{{font-family:var(--fm);font-size:11px;color:var(--grey);letter-spacing:1px}}
.sk{{font-family:var(--fm);font-size:11px;color:var(--yellow);letter-spacing:1px}}
.mlink{{display:block;margin-top:20px;padding:12px 18px;border:1px solid #333;font-family:var(--fm);font-size:11px;color:var(--grey);text-decoration:none;letter-spacing:1px;transition:all .2s;text-align:center}}
.mlink:hover{{border-color:var(--yellow);color:var(--yellow);background:rgba(232,255,0,.03)}}
.footer{{position:fixed;bottom:0;left:0;right:0;background:var(--carbon);border-top:2px solid var(--green-dark);padding:10px 24px;display:flex;justify-content:space-between;align-items:center;font-family:var(--fm);font-size:10px;color:var(--grey);letter-spacing:1px;z-index:100}}
.footer span{{color:var(--green-light)}}
.empty{{padding:60px;text-align:center;color:var(--grey);font-family:var(--fm);font-size:12px;letter-spacing:2px;border:1px dashed #2a2a2a}}
@media(max-width:600px){{.mi{{padding:16px 16px 0}}.main{{padding:20px 16px 80px}}.nt{{padding:8px 12px;font-size:9px;letter-spacing:1px}}.pn{{font-size:40px}}}}
</style>
</head>
<body>
<div class="masthead">
  <div class="mi">
    <div class="mt">
      <div class="pn">FOOTBALL<span>INTEL</span></div>
      <div class="mm">
        <strong>📡 RULE ENGINE EDITION</strong>
        {generated_at}<br>BBC Sport · The Guardian
      </div>
    </div>
    <nav class="mnav">
      <button class="nt active" onclick="showSec('all',this)">全部 <span class="nc" id="c-all">{total}</span></button>
      <button class="nt" onclick="showSec('transfer',this)">转会情报 <span class="nc" id="c-tr">{cnt['transfer']}</span></button>
      <button class="nt" onclick="showSec('match_report',this)">赛事战报 <span class="nc" id="c-mr">{cnt['match_report']}</span></button>
      <button class="nt" onclick="showSec('injury',this)">伤病速递 <span class="nc" id="c-inj">{cnt['injury']}</span></button>
    </nav>
  </div>
</div>
<main class="main">
  <div id="s-all" class="sec active"><div class="cg" id="g-all"></div></div>
  <div id="s-transfer" class="sec"><div class="cg" id="g-transfer"></div></div>
  <div id="s-match_report" class="sec"><div class="cg" id="g-match_report"></div></div>
  <div id="s-injury" class="sec"><div class="cg" id="g-injury"></div></div>
</main>
<div class="mo" id="mo" onclick="closeMo(event)">
  <div class="md">
    <div class="mh">
      <button class="mc" onclick="closeMo(null)">✕</button>
      <div class="ml" id="m-lb"></div>
      <div class="mtt" id="m-tt"></div>
    </div>
    <div class="mb">
      <div class="pv"><div class="pc2"></div><div class="pp" id="m-pl"></div></div>
      <div class="bs"><div class="bh">核心事件</div><div class="bt" id="m-co"></div></div>
      <div class="bs"><div class="bh">战术 / 财务影响</div><div class="tb" id="m-im"></div></div>
      <div id="m-sr" class="sr" style="display:none">
        <div class="sl">吃瓜指数</div>
        <div class="gs" id="m-st"></div>
        <div class="sk" id="m-gl"></div>
      </div>
      <a id="m-lk" href="#" target="_blank" class="mlink">→ 阅读原文报道</a>
    </div>
  </div>
</div>
<footer class="footer">
  <div>FOOTBALL INTEL // RULE ENGINE</div>
  <div>Generated: <span>{generated_at}</span></div>
  <div>Sources: <span>BBC · GUARDIAN</span></div>
</footer>
<script>
const DATA={all_json};
const CL={{'transfer':'转会情报','match_report':'赛事战报','injury':'伤病速递'}};
const CE={{'transfer':'TRANSFER INTEL','match_report':'MATCH REPORT','injury':'INJURY UPDATE'}};
function st(n){{let s='';for(let i=1;i<=5;i++)s+=`<span class="star ${{i<=n?'on':'off'}}">★</span>`;return s;}}
function mkCard(a){{
  const d=document.createElement('div');
  d.className=`card ${{a.category}}`;
  d.onclick=()=>openMo(a);
  const gH=a.gossip_rating!=null?`<div class="gs">${{st(a.gossip_rating)}}<span class="gl">${{a.gossip_label}}</span></div>`:'';
  const tH=(a.key_players||[]).slice(0,3).map(p=>`<span class="tag">${{p}}</span>`).join('');
  d.innerHTML=`
    <div class="ch"><div class="ce">${{a.emoji_tag}}</div>
    <div><div class="cs">${{a.source}}</div><span class="cb cat-${{a.category}}">${{CL[a.category]}}</span></div></div>
    <div class="ct">${{a.title}}</div>
    <div class="csum">${{a.core_event}}</div>
    ${{gH}}
    <div class="cf"><div class="ctags">${{tH}}</div><span class="rm">战术板 →</span></div>`;
  return d;
}}
function render(){{
  const G={{'all':document.getElementById('g-all'),'transfer':document.getElementById('g-transfer'),'match_report':document.getElementById('g-match_report'),'injury':document.getElementById('g-injury')}};
  DATA.forEach(a=>{{G.all.appendChild(mkCard(a));if(G[a.category])G[a.category].appendChild(mkCard(a));}});
  Object.values(G).forEach(g=>{{if(!g.children.length)g.innerHTML='<div class="empty">// NO DATA</div>';}});
}}
function showSec(k,btn){{
  document.querySelectorAll('.sec').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nt').forEach(t=>t.classList.remove('active'));
  document.getElementById(`s-${{k}}`).classList.add('active');
  btn.classList.add('active');
}}
function openMo(a){{
  document.getElementById('m-lb').textContent=`// ${{CE[a.category]}} · ${{a.source.toUpperCase()}}`;
  document.getElementById('m-tt').textContent=a.title;
  document.getElementById('m-co').textContent=a.core_event;
  document.getElementById('m-im').textContent=a.tactical_financial_impact;
  document.getElementById('m-lk').href=a.url;
  document.getElementById('m-pl').innerHTML=(a.key_players||[]).map(p=>`<span class="pc">${{p}}</span>`).join('');
  const sr=document.getElementById('m-sr');
  if(a.gossip_rating!=null){{sr.style.display='flex';document.getElementById('m-st').innerHTML=st(a.gossip_rating);document.getElementById('m-gl').textContent=a.gossip_label||'';}}
  else sr.style.display='none';
  document.getElementById('mo').classList.add('open');
  document.body.style.overflow='hidden';
}}
function closeMo(e){{
  if(e&&e.target!==document.getElementById('mo'))return;
  document.getElementById('mo').classList.remove('open');
  document.body.style.overflow='';
}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeMo(null);}});
render();
</script>
</body>
</html>"""


# ─────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────
def main():
    print("🚀 Football Intel（免费版）启动...\n")

    # 1. 抓取
    all_articles = []
    for k, cfg in SOURCES.items():
        all_articles.extend(scrape(k, cfg))

    if not all_articles:
        print("⚠ 未抓到任何文章，请检查网络连接", file=sys.stderr)
        sys.exit(1)

    print(f"\n📰 共收集到 {len(all_articles)} 条文章")

    # 2. 规则分析
    print("🔍 规则引擎分析中...")
    enriched = analyse(all_articles)

    # 3. 保存 JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "data.json", "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    # 4. 渲染 HTML
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render_html(enriched, now)
    html_path = OUTPUT_DIR / "index.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 完成！报告已生成：{html_path}")
    print("   在浏览器打开查看效果 🎉")


if __name__ == "__main__":
    main()
