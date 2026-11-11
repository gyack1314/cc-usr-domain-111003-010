"""最终反查：独立重读门户与本地材料，核对总量守恒、逐机去向、材料范围与草稿一致。

任何一项不符即以非零退出，并把结果写入 output/verification.md。
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--devices", required=True)
    ap.add_argument("--downloads", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()

    out = Path(args.outdir)
    coverage = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    saved = json.loads(Path(args.drafts).read_text(encoding="utf-8"))
    devices_csv = list(csv.DictReader(open(args.devices, encoding="utf-8")))
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1) 回读门户草稿：数量、状态、正文与本地一致
    expected_bodies = {
        "coverage-review": (out / "manager_summary.md").read_text(encoding="utf-8"),
        "dealer-notice-dong": (out / "dealers" / "通知草稿-D东.md").read_text(encoding="utf-8"),
        "dealer-notice-xi": (out / "dealers" / "通知草稿-D西.md").read_text(encoding="utf-8"),
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(f"{args.url}/drafts", wait_until="domcontentloaded")
        hrefs = page.locator("a[href^='/draft/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        from urllib.parse import unquote
        keys = sorted(unquote(h.rsplit("/", 1)[1]) for h in hrefs if not h.endswith("/new"))
        check("门户草稿集合与本地一致（无重复、无追加）", keys == sorted(expected_bodies), f"门户={keys}")
        for key, body in expected_bodies.items():
            page.goto(f"{args.url}/draft/{key}", wait_until="domcontentloaded")
            status = page.locator('[data-testid="draft-status"]').inner_text()
            back = page.get_by_label("正文", exact=True).input_value()
            refs_back = page.get_by_label("资料引用，每行一项 编号@摘要").input_value().split()
            check(f"草稿 {key} 状态为待人工审批", status == "待人工审批", status)
            check(f"草稿 {key} 正文与本地文件一致", back == body)
            check(f"草稿 {key} 引用与本地记录一致", refs_back == saved[key]["refs"],
                  f"{len(refs_back)} 项")
        browser.close()

    # 2) 总量守恒
    cons = coverage["conservation"]
    devs = coverage["devices"]
    check("登记设备数 = 分类设备数 = 8",
          cons["registered_devices"] == cons["classified_devices"] == cons["category_total"] == 8)
    all_sn = [sn for v in cons["by_category"].values() for sn in v]
    check("每台设备恰属一个分类", sorted(all_sn) == sorted(devs.keys()) and len(all_sn) == len(set(all_sn)))
    affected = cons["affected_components_installed"]
    check("受影响组件无重复计数", len(affected) == len(set(affected)))
    check("受影响组件数 = 需召回 + 人工核实",
          cons["affected_components_count"]
          == len(cons["by_category"].get("needs_recall", [])) + len(cons["by_category"].get("manual_review", [])))

    # 3) 每台登记设备去向
    csv_sns = {d["device"] for d in devices_csv}
    check("登记设备与覆盖结论一一对应", csv_sns == set(devs.keys()))
    whereabouts = []
    for d in devices_csv:
        sn = d["device"]
        c = devs[sn]
        dest = c["dealer_at_freeze"] or "留置待定（持有冲突）"
        whereabouts.append((sn, d["dealer"], dest, c["classification"]))
        check(f"{sn} 去向明确（{d['dealer']} → {dest}）",
              c["dealer_at_freeze"] is not None or "冲突" in str(c["open_issues"]) or c["classification"] == "manual_review")

    # 4) 每份收件材料的范围与用途
    for rid, meta in sorted(index["records"].items()):
        uses = coverage["record_usage"].get(rid, [])
        check(f"材料 {rid} 已纳入核对并标明用途", bool(uses), "；".join(uses))
        check(f"材料 {rid} 文书原文与结构化字段一致",
              coverage["document_checks"][rid]["document_fields_equal_json"])
    check("撤回通告仅作谱系关联，未参与范围",
          coverage["record_usage"]["R0"] == ["通告谱系"] and coverage["record_usage"]["N0"] == ["通告谱系"])

    # 5) 通告范围按文书原文独立重解析
    doc = (Path(args.downloads) / "R1" / index["records"]["R1"]["document_file"]).read_text(encoding="utf-8")
    fields = dict(re.findall(r"^([A-Za-z_]+)：(.+)$", doc, flags=re.M))
    scope = coverage["notice"]["scope"]
    check("通告原文前缀/起止/排除与覆盖结论一致",
          json.loads(fields["prefix"]) == scope["prefix"]
          and json.loads(fields["lo"]) == scope["lo"]
          and json.loads(fields["hi"]) == scope["hi"]
          and json.loads(fields["inclusive"]) is True
          and json.loads(fields["excluded"]) == scope["excluded"])

    # 6) 经销商隔离：草稿只含本商设备与必要联系人
    contacts = {d["device"]: d["contact"] for d in devices_csv}
    dealer_lists = {"D东": ["SN02"], "D西": ["SN03", "SN06"]}
    for dealer, sns in dealer_lists.items():
        text = (out / "dealers" / f"通知草稿-{dealer}.md").read_text(encoding="utf-8")
        others = [s for s in devs if s not in sns]
        check(f"{dealer} 草稿恰好包含本商设备 {sns}",
              all(s in text for s in sns) and all(s not in text for s in others))
        leaked = [c for s, c in contacts.items() if s not in sns and c in text]
        check(f"{dealer} 草稿未泄露他方联系人", not leaked, str(leaked))
        check(f"{dealer} 草稿标明尚未发出且无争议备注",
              "尚未发出" in text and "留置" not in text and "H2" not in text and "M4" not in text)
    check("跨经销商名册与争议细节仅存于私有材料",
          (out / "private" / "内部备注.md").is_file()
          and "SN08" in (out / "private" / "内部备注.md").read_text(encoding="utf-8"))

    # 7) 进度账
    out_notes = subprocess.run([sys.executable, "-m", "workbench.tools", "notes", args.journal],
                               cwd=args.workspace, capture_output=True, text=True)
    journal_keys = [n["key"] for n in json.loads(out_notes.stdout)]
    check("进度账包含采集/分析/报告/草稿四环",
          journal_keys == ["collect:portal", "analyze:coverage", "reports:files", "drafts:saved"],
          str(journal_keys))

    # 汇总
    failed = [c for c in checks if not c["ok"]]
    lines = ["# 最终反查报告", "",
             f"检查项 {len(checks)}，通过 {len(checks) - len(failed)}，失败 {len(failed)}。", "",
             "## 每台登记设备去向（冻结时点）", "",
             "| 整机 | 登记经销商 | 冻结时点归属 | 分类 |", "|---|---|---|---|"]
    label = {"needs_recall": "需召回", "excluded_with_basis": "排除有据",
             "manual_review": "人工核实", "not_affected": "不在范围"}
    for sn, reg, dest, cat in whereabouts:
        lines.append(f"| {sn} | {reg} | {dest} | {label[cat]} |")
    lines += ["", "## 每份收件材料的范围", "", "| 资料 | 摘要(前12位) | 用途 |", "|---|---|---|"]
    for rid, meta in sorted(index["records"].items()):
        uses = "；".join(coverage["record_usage"].get(rid, []))
        lines.append(f"| {rid}（{meta['title']}） | {meta['attachment_sha256'][:12]} | {uses} |")
    lines += ["", "## 逐项检查", ""]
    for c in checks:
        lines.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}" + (f" — {c['detail']}" if c["detail"] else ""))
    lines += ["", "结论：" + ("全部通过；门户草稿与本地摘要相符，保持未通知状态。" if not failed else "存在失败项，需处理。"), ""]
    (out / "verification.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"checks": len(checks), "failed": [c["name"] for c in failed]}, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
