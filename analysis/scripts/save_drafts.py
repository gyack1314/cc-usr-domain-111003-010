"""把内部审核草稿经浏览器表单保存到门户（草稿状态恒为 待人工审批，不触发任何对外通知）。

幂等：相同正文与引用重复提交，门户返回既有版本、不增历史；内容变化则按已读版本修订。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def build_specs(coverage_path: str, index_path: str, outdir: str) -> list:
    coverage = json.loads(Path(coverage_path).read_text(encoding="utf-8"))
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    refs = {rid: meta["reference"] for rid, meta in index["records"].items()}
    out = Path(outdir)

    def dealer_of(category):
        return sorted({d["dealer_at_freeze"] for d in coverage["devices"].values()
                       if d["classification"] == category})

    specs = [{
        "key": "coverage-review",
        "title": "召回范围核对与分层通知准备摘要（服务经理审核稿）",
        "body": (out / "manager_summary.md").read_text(encoding="utf-8"),
        "refs": [refs[rid] for rid in sorted(refs)],
    }]
    key_by_dealer = {"D东": "dealer-notice-dong", "D西": "dealer-notice-xi"}
    refs_by_dealer = {"D东": ["R1", "M3"], "D西": ["R1", "M2"]}
    for dealer in dealer_of("needs_recall"):
        body = (out / "dealers" / f"通知草稿-{dealer}.md").read_text(encoding="utf-8")
        specs.append({
            "key": key_by_dealer[dealer],
            "title": f"经销商通知草稿（{dealer}，内部预审，未发出）",
            "body": body,
            "refs": [refs[r] for r in refs_by_dealer[dealer]],
        })
    return specs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args()

    specs = build_specs(args.coverage, args.index, args.outdir)
    saved = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        for spec in specs:
            page.goto(f"{args.url}/drafts", wait_until="domcontentloaded")
            hrefs = page.locator("a[href^='/draft/']").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))")
            existing = {h.rsplit("/", 1)[1] for h in hrefs}
            from urllib.parse import quote, unquote
            existing = {unquote(h) for h in existing}
            if spec["key"] in existing:
                page.goto(f"{args.url}/draft/{quote(spec['key'])}", wait_until="domcontentloaded")
            else:
                page.goto(f"{args.url}/draft/new", wait_until="domcontentloaded")
                page.get_by_label("草稿编号").fill(spec["key"])
            version = page.locator("input[name='version']").get_attribute("value")
            page.get_by_label("标题", exact=True).fill(spec["title"])
            page.get_by_label("正文", exact=True).fill(spec["body"])
            page.get_by_label("资料引用，每行一项 编号@摘要").fill("\n".join(spec["refs"]))
            page.get_by_role("button", name="保存预审草稿").click()
            page.wait_for_load_state("domcontentloaded")
            status = page.locator('[data-testid="draft-status"]').inner_text()
            if status != "待人工审批":
                raise RuntimeError(f"草稿 {spec['key']} 状态异常: {status}")
            new_version = page.locator("input[name='version']").get_attribute("value")
            body_back = page.get_by_label("正文", exact=True).input_value()
            if body_back != spec["body"]:
                raise RuntimeError(f"草稿 {spec['key']} 回读正文不一致")
            saved[spec["key"]] = {
                "title": spec["title"],
                "previous_version": int(version),
                "version": int(new_version),
                "status": status,
                "refs": spec["refs"],
                "body_sha256": hashlib.sha256(spec["body"].encode("utf-8")).hexdigest(),
            }
        browser.close()
    result_path = Path(args.result)
    result_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: {"version": v["version"], "status": v["status"]}
                      for k, v in saved.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
